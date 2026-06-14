"""Full pipeline orchestrator — coordinates all 4 layers.

Entry point for both fresh runs and resumed runs.
"""

import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

from novel_decomp.config import (
    DEFAULT_MODEL, CHEAP_MODEL, DEFAULT_BATCH_SIZE,
    OUTPUT_DIR, CHECKPOINT_DIR, EXPORT_DIR, DATA_DIR,
    create_client,
)
from novel_decomp.cache.disk_cache import DiskCache
from novel_decomp.pipeline.checkpoint import CheckpointManager
from novel_decomp.layer1.extractor import extract_chapters, validate_chapters
from novel_decomp.layer1.batcher import build_batches, get_batch_stats


async def run_full_pipeline(
    novel_path: str | Path,
    *,
    output_dir: str | Path = OUTPUT_DIR,
    checkpoint_dir: str | Path = CHECKPOINT_DIR,
    model: str = DEFAULT_MODEL,
    cheap_model: str = CHEAP_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sample_size: int = 20,
    verbose: bool = False,
) -> dict:
    """Run the complete 4-layer novel decomposition pipeline.

    Args:
        novel_path: Path to the novel .txt file.
        output_dir: Directory for all outputs.
        checkpoint_dir: Directory for checkpoint state.
        model: Primary Claude model for analysis.
        cheap_model: Cheaper model for light tasks.
        batch_size: Target chapters per batch.
        sample_size: Random chapters for human review export.
        verbose: Enable detailed logging.

    Returns:
        Dict with pipeline results summary.
    """
    novel_path = Path(novel_path)
    output_dir = Path(output_dir)
    checkpoint_dir = Path(checkpoint_dir)

    if not novel_path.exists():
        raise FileNotFoundError(f"Novel file not found: {novel_path}")

    # Initialize
    checkpoint_mgr = CheckpointManager(checkpoint_dir)
    cache = DiskCache(DATA_DIR / "cache", enabled=True)
    client = create_client(cache=cache, model=model)

    start_time = datetime.now()

    # ═════ Layer 1: Preprocessing ═════
    print("\n" + "=" * 60)
    print("  Layer 1: Preprocessing")
    print("=" * 60)

    checkpoint_mgr.update_layer_state(1, status="running")

    chapters = extract_chapters(str(novel_path))
    print(f"  ✓ Extracted {len(chapters)} chapters")

    issues = validate_chapters(chapters)
    if issues:
        for issue in issues[:5]:
            print(f"    ⚠ {issue}")

    batches = build_batches(chapters, target_chapters_per_batch=batch_size)
    stats = get_batch_stats(batches)
    print(f"  ✓ Built {stats['total_batches']} batches "
          f"(avg {stats['avg_chapters_per_batch']:.1f} chapters, "
          f"~{stats['avg_tokens_per_batch']:.0f} tokens/batch)")

    # Extract metadata
    novel_metadata = _extract_metadata(novel_path, chapters)

    checkpoint_mgr.update_layer_state(1, status="completed", batches_completed=1, total_batches=1)

    # ═════ Layer 2: Chapter Analysis ═════
    print("\n" + "=" * 60)
    print("  Layer 2: Chapter Analysis (Rolling Context)")
    print("=" * 60)

    checkpoint_mgr.update_layer_state(2, status="running", total_batches=len(batches))

    from novel_decomp.layer2.runner import Layer2Runner

    layer2_output_dir = output_dir / "layer2"
    runner = Layer2Runner(
        client=client,
        novel_metadata=novel_metadata,
        output_dir=layer2_output_dir,
        checkpoint_dir=checkpoint_dir,
        model=model,
    )

    try:
        batch_results = await runner.run(batches)
        print(f"  ✓ Layer 2 complete: {len(batch_results)} batches analyzed")
        checkpoint_mgr.update_layer_state(2, status="completed", batches_completed=len(batches))
    except Exception as e:
        print(f"  ✗ Layer 2 failed: {e}")
        checkpoint_mgr.update_layer_state(2, status="failed", error=str(e))
        raise

    # ═════ Layer 3: Aggregation ═════
    print("\n" + "=" * 60)
    print("  Layer 3: Aggregation & Entity Resolution")
    print("=" * 60)

    checkpoint_mgr.update_layer_state(3, status="running")

    from novel_decomp.layer3.collator import collate_batch_results
    from novel_decomp.layer3.resolver import resolve_entities, find_ambiguous_merges
    from novel_decomp.layer3.detector import detect_retcons, detect_gaps, summarize_entity_db

    # Collate
    raw_data = collate_batch_results(layer2_output_dir)
    print(f"  ✓ Collated {raw_data['batch_count']} batches, "
          f"{raw_data['total_chapter_summaries']} chapter summaries")
    print(f"  ✓ Raw entities: {len(raw_data['raw_characters'])} characters, "
          f"{len(raw_data['raw_factions'])} factions, "
          f"{len(raw_data['raw_locations'])} locations, "
          f"{len(raw_data['raw_powers'])} powers")

    # Resolve
    resolved = resolve_entities(raw_data)
    stats = resolved.pop("resolution_stats", {})
    for etype, estats in stats.items():
        print(f"  ✓ {etype}: {estats['raw_count']} raw → "
              f"{estats['resolved_count']} resolved ({estats['merges']} merges)")

    # Detect issues — pass raw data for cross-batch status timeline scan
    retcons = detect_retcons(
        resolved.get("characters", {}),
        resolved.get("factions", {}),
        raw_data.get("all_contradictions", []),
        raw_characters=raw_data.get("raw_characters", []),
        raw_factions=raw_data.get("raw_factions", []),
    )
    gaps = detect_gaps(resolved.get("characters", {}))
    print(f"  ✓ Detected {len(retcons)} retcons, {len(gaps)} character gaps")

    # Ambiguous merges for review
    ambiguous = find_ambiguous_merges(resolved.get("characters", {}))
    if ambiguous:
        print(f"  ℹ {len(ambiguous)} ambiguous entity pairs (may need manual review)")
        for amb in ambiguous[:5]:
            print(f"    - {amb['entity1_name']} ↔ {amb['entity2_name']} "
                  f"(similarity: {amb['similarity']:.2f})")

    entity_summary = summarize_entity_db(resolved)
    print(f"  ✓ Entity DB: {entity_summary['character_count']} characters, "
          f"{entity_summary['faction_count']} factions, "
          f"{entity_summary['location_count']} locations, "
          f"{entity_summary['power_count']} powers")

    # Save Layer 3 output
    layer3_output = {
        "resolved_entities": {
            "characters": resolved.get("characters", {}),
            "factions": resolved.get("factions", {}),
            "locations": resolved.get("locations", {}),
            "powers": resolved.get("powers", {}),
        },
        "retcons": retcons,
        "gaps": gaps,
        "ambiguous_merges": ambiguous,
        "stats": entity_summary,
        "resolution_stats": stats,
    }
    l3_path = output_dir / "layer3_resolved.json"
    l3_path.write_text(json.dumps(layer3_output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ Saved to {l3_path}")

    checkpoint_mgr.update_layer_state(3, status="completed", batches_completed=1, total_batches=1)

    # ═════ Layer 4: Synthesis ═════
    print("\n" + "=" * 60)
    print("  Layer 4: Global Synthesis")
    print("=" * 60)

    checkpoint_mgr.update_layer_state(4, status="running")

    from novel_decomp.layer4.outline import build_full_outline
    from novel_decomp.layer4.profiles import (
        build_character_profiles, build_faction_profiles,
        build_location_profiles, build_power_profiles,
        build_character_evolution,
    )
    from novel_decomp.layer4.arcs import synthesize_plot_arcs

    # Build outline
    outline = build_full_outline(
        raw_data["chapters"],
        raw_data["narrative_summaries"],
    )
    print(f"  ✓ Built outline: {len(outline['volumes'])} volumes")

    # Build character evolution from raw batch data
    evolutions = build_character_evolution(raw_data.get("raw_characters", []))
    chars_with_evo = sum(1 for e in evolutions.values()
                         if len(e.get("personality_stages", [])) >= 2
                         or len(e.get("power_stages", [])) >= 1
                         or len(e.get("status_timeline", [])) >= 2)
    print(f"  ✓ Built character evolutions: {chars_with_evo} characters with dynamic arcs")

    # Build profiles
    char_profiles = build_character_profiles(
        resolved.get("characters", {}),
        evolutions=evolutions,
    )
    faction_profiles = build_faction_profiles(resolved.get("factions", {}))
    location_profiles = build_location_profiles(resolved.get("locations", {}))
    power_profiles = build_power_profiles(resolved.get("powers", {}))
    print(f"  ✓ Built profiles: {len(char_profiles)} characters, "
          f"{len(faction_profiles)} factions, "
          f"{len(location_profiles)} locations, "
          f"{len(power_profiles)} powers")

    # Synthesize arcs
    plot_arcs = synthesize_plot_arcs(
        raw_data["chapters"],
        raw_data["narrative_summaries"],
    )
    print(f"  ✓ Synthesized {len(plot_arcs)} plot arcs")

    # Save Layer 4 output
    layer4_output = {
        "outline": outline,
        "character_profiles": char_profiles,
        "faction_profiles": faction_profiles,
        "location_profiles": location_profiles,
        "power_profiles": power_profiles,
        "plot_arcs": plot_arcs,
        "retcons": retcons,
        "gaps": gaps,
        "entity_summary": entity_summary,
        "ambiguous_merges": ambiguous,
    }
    l4_path = output_dir / "layer4_synthesis.json"
    l4_path.write_text(json.dumps(layer4_output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ Saved to {l4_path}")

    checkpoint_mgr.update_layer_state(4, status="completed", batches_completed=1, total_batches=1)

    # ═════ Export ═════
    print("\n" + "=" * 60)
    print("  Export: Markdown Reports")
    print("=" * 60)

    from novel_decomp.export.markdown import export_all
    from novel_decomp.export.sampling import export_human_review_sample

    md_files = export_all(
        layer4_output,
        output_dir / "reports",
        novel_title=novel_metadata.get("title", ""),
        author=novel_metadata.get("author", ""),
    )
    for f in md_files:
        print(f"  ✓ {f.name}")

    # Human review sample
    review_path = export_human_review_sample(
        layer2_output_dir,
        novel_path,
        EXPORT_DIR,
        sample_size=sample_size,
    )
    print(f"  ✓ Human review sample: {review_path.name} ({sample_size} chapters)")

    # ═════ Summary ═════
    elapsed = (datetime.now() - start_time).total_seconds()
    usage = client.usage_summary

    print("\n" + "=" * 60)
    print("  Pipeline Complete!")
    print("=" * 60)
    print(f"  Novel: {novel_metadata.get('title', '?')} by {novel_metadata.get('author', '?')}")
    print(f"  Chapters: {len(chapters)}")
    print(f"  Time: {elapsed:.0f}s")
    print(f"  API Calls: {usage['calls']}")
    print(f"  Tokens: {usage['total_tokens']:,}")
    print(f"  Est. Cost: ${usage['estimated_cost_usd']:.2f}")
    print(f"  Output: {output_dir}")
    print(f"  Review: {review_path}")

    return {
        "success": True,
        "novel_metadata": novel_metadata,
        "total_chapters": len(chapters),
        "total_batches": len(batches),
        "elapsed_seconds": elapsed,
        "usage": usage,
        "output_dir": str(output_dir),
        "review_file": str(review_path),
        "entity_summary": entity_summary,
    }


async def resume_pipeline(
    checkpoint_dir: str | Path = CHECKPOINT_DIR,
    output_dir: str | Path = OUTPUT_DIR,
    *,
    novel_path: str = "",
    model: str = "",
) -> dict:
    """Resume pipeline from last checkpoint.

    Args:
        checkpoint_dir: Directory with checkpoint state.
        output_dir: Output directory.
        novel_path: Override novel path (if moved).
        model: Override model.

    Returns:
        Pipeline results summary.
    """
    checkpoint_dir = Path(checkpoint_dir)
    checkpoint_mgr = CheckpointManager(checkpoint_dir)
    state = checkpoint_mgr.load()

    if not state:
        raise FileNotFoundError(f"No checkpoint found in {checkpoint_dir}")

    np_path = novel_path or state.get("novel_path", "")
    if not np_path:
        raise ValueError("Novel path not found in checkpoint — provide --novel")

    current_layer = state.get("current_layer", 1)
    print(f"Resuming from Layer {current_layer}...")

    # TODO: Implement selective resume per layer
    # For now, re-run from the failed layer
    return await run_full_pipeline(
        novel_path=np_path,
        output_dir=output_dir,
        checkpoint_dir=checkpoint_dir,
        model=model or DEFAULT_MODEL,
    )


def _extract_metadata(novel_path: Path, chapters: list) -> dict:
    """Extract novel metadata."""
    import re
    metadata = {
        "title": novel_path.stem,
        "author": "未知",
        "synopsis": "",
        "total_chapters": len([c for c in chapters if not c.is_afterword]),
    }
    try:
        text = novel_path.read_text(encoding="utf-8")
        for line in text.split("\n")[:30]:
            line = line.strip()
            if line.startswith("书名"):
                metadata["title"] = re.sub(r"书名[：:]?\s*", "", line)
            elif line.startswith("作者"):
                metadata["author"] = re.sub(r"作者[：:]?\s*", "", line)
            elif line.startswith("简介"):
                metadata["synopsis"] = re.sub(r"简介[：:]?\s*", "", line)[:500]
                break
    except Exception:
        pass
    return metadata
