"""Collect and flatten all Layer 2 batch analysis outputs.

Merges per-batch entity snapshots into a single raw entity collection
for downstream resolution.
"""

import json
from pathlib import Path
from typing import Optional

from novel_decomp.models.chapter import BatchAnalysisOutput, ChapterSummary


def collate_batch_results(
    batch_dir: str | Path,
    *,
    max_batches: Optional[int] = None,
) -> dict:
    """Collect all batch analysis outputs from Layer 2.

    Args:
        batch_dir: Directory containing batch_NNNN.json files.
        max_batches: Maximum number of batches to load (for testing).

    Returns:
        Dict with:
            - chapters: list of ChapterSummary (sorted by chapter_number)
            - raw_characters: list of character dicts (with duplicates)
            - raw_factions: list of faction dicts (with duplicates)
            - raw_locations: list of location dicts
            - raw_powers: list of power dicts
            - all_foreshadowing: list of foreshadowing dicts
            - all_contradictions: list of contradiction dicts
            - narrative_summaries: list of batch narrative summaries
            - batch_count: int
    """
    batch_dir = Path(batch_dir)
    if not batch_dir.exists():
        raise FileNotFoundError(f"Batch directory not found: {batch_dir}")

    # Load all batch files
    batch_files = sorted(batch_dir.glob("batch_*.json"))
    if max_batches:
        batch_files = batch_files[:max_batches]

    if not batch_files:
        raise ValueError(f"No batch files found in {batch_dir}")

    all_chapters: list[ChapterSummary] = []
    raw_characters: list[dict] = []
    raw_factions: list[dict] = []
    raw_locations: list[dict] = []
    raw_powers: list[dict] = []
    all_foreshadowing: list[dict] = []
    all_contradictions: list[dict] = []
    narrative_summaries: list[dict] = []

    for bf in batch_files:
        try:
            batch = json.loads(bf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"  ⚠ Skipping corrupt batch file: {bf.name}")
            continue

        # Collect chapters (reconstruct ChapterSummary)
        for ch in batch.get("chapters", []):
            from novel_decomp.models.chapter import KeyEvent
            events = []
            for ev in ch.get("key_events", []):
                events.append(KeyEvent(
                    type=ev.get("type", "其他"),
                    description=ev.get("description", ""),
                    characters_involved=ev.get("characters_involved", []),
                    significance=ev.get("significance", "transitional"),
                ))
            all_chapters.append(ChapterSummary(
                chapter_number=ch.get("chapter_number", 0),
                title=ch.get("title", ""),
                summary=ch.get("summary", ""),
                key_events=events,
                pov_character=ch.get("pov_character", ""),
                locations_visited=ch.get("locations_visited", []),
                characters_appeared=ch.get("characters_appeared", []),
                character_relationships=ch.get("character_relationships", []),
                foreshadowing_planted=ch.get("foreshadowing_planted", []),
                foreshadowing_resolved=ch.get("foreshadowing_resolved", []),
            ))

        # Collect entity updates
        eu = batch.get("entity_updates", {})
        raw_characters.extend(eu.get("characters", []))
        raw_factions.extend(eu.get("factions", []))
        raw_locations.extend(eu.get("locations", []))
        raw_powers.extend(eu.get("powers", []))

        # Collect cross-cutting
        all_foreshadowing.extend(batch.get("foreshadowing", []))
        all_contradictions.extend(batch.get("contradictions_with_prior", []))

        # Narrative summary
        ns = batch.get("narrative_summary", {})
        if ns:
            narrative_summaries.append(ns)

    # Sort chapters by number
    all_chapters.sort(key=lambda c: c.chapter_number)

    # Build per-location chapter presence from all chapters
    from collections import defaultdict
    location_chapters: dict[str, list[int]] = defaultdict(list)
    for ch in all_chapters:
        for loc_name in ch.locations_visited:
            location_chapters[loc_name].append(ch.chapter_number)

    # Also count character appearances for the resolver
    char_chapters: dict[str, list[int]] = defaultdict(list)
    for ch in all_chapters:
        for c in ch.characters_appeared:
            name = c.get("名称", c.get("name", str(c))) if isinstance(c, dict) else str(c)
            char_chapters[name].append(ch.chapter_number)

    return {
        "chapters": all_chapters,
        "raw_characters": raw_characters,
        "raw_factions": raw_factions,
        "raw_locations": raw_locations,
        "raw_powers": raw_powers,
        "all_foreshadowing": all_foreshadowing,
        "all_contradictions": all_contradictions,
        "narrative_summaries": narrative_summaries,
        "batch_count": len(batch_files),
        "total_chapter_summaries": len(all_chapters),
        "location_chapters": dict(location_chapters),
        "char_chapters": dict(char_chapters),
    }
