"""Layer 2 async pipeline runner with rolling context dependency.

Processes batches sequentially (dependency on rolling context) but
fires concurrent API calls for pipeline efficiency.
"""

import json
import asyncio
from pathlib import Path
from typing import Optional, Callable
from datetime import datetime

from novel_decomp.anthropic_client import AnthropicClient
from novel_decomp.models.chapter import BatchAnalysisOutput, RawChapter
from novel_decomp.models.pipeline import RollingContextState
from novel_decomp.layer2.prompt import (
    build_entity_snapshot_markdown,
    merge_entity_snapshot,
)
from novel_decomp.layer2.analyzer import analyze_batch


class Layer2Runner:
    """Orchestrates Layer 2 chapter analysis with rolling context."""

    def __init__(
        self,
        client: AnthropicClient,
        novel_metadata: dict,
        *,
        output_dir: Path = Path("data/output"),
        checkpoint_dir: Path = Path("data/checkpoint"),
        model: str = "",
        on_batch_complete: Optional[Callable] = None,
    ):
        self.client = client
        self.novel_metadata = novel_metadata
        self.output_dir = Path(output_dir)
        self.checkpoint_dir = Path(checkpoint_dir)
        self.model = model
        self.on_batch_complete = on_batch_complete

        # State
        self.rolling_summary = ""
        self.entity_snapshot: dict = {}
        self.arc_summaries: list[dict] = []
        self.recent_summaries: list[dict] = []
        self.results: list[BatchAnalysisOutput] = []

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        batches: list[list[RawChapter]],
        start_batch: int = 0,
    ) -> list[BatchAnalysisOutput]:
        """Run Layer 2 analysis on all batches.

        Args:
            batches: List of chapter batches (from Layer 1).
            start_batch: Resume from this 0-based batch index.

        Returns:
            List of BatchAnalysisOutput for all batches.
        """
        total = len(batches)
        print(f"\n{'='*60}")
        print(f"Layer 2: Chapter Analysis")
        print(f"  Total batches: {total}")
        print(f"  Starting from batch: {start_batch + 1}")
        print(f"  Model: {self.model or self.client.model}")
        print(f"{'='*60}\n")

        for i in range(start_batch, total):
            batch_id = i + 1
            batch = batches[i]

            # Build rolling context
            rolling_context = self._build_rolling_context()

            # Build entity snapshot markdown
            entity_md = build_entity_snapshot_markdown(self.entity_snapshot)

            first_ch = batch[0].number if batch else 0
            last_ch = batch[-1].number if batch else 0
            est_tokens = sum(ch.estimated_tokens for ch in batch)

            print(f"Batch {batch_id}/{total} [Ch {first_ch}-{last_ch}] "
                  f"({len(batch)} chapters, ~{est_tokens} tokens) ... ", end="", flush=True)

            try:
                result = await analyze_batch(
                    client=self.client,
                    batch_id=batch_id,
                    chapters=batch,
                    novel_metadata=self.novel_metadata,
                    rolling_summary=rolling_context,
                    entity_snapshot=entity_md,
                    model=self.model,
                )
                self.results.append(result)
                print(f"✓ ({result.batch_estimated_tokens}t in, "
                      f"{len(result.chapters)} chapters analyzed)")

                # Update rolling context
                self._update_rolling_context(result)

                # Save progressive output
                self._save_batch_result(result)
                self._save_checkpoint(batch_id, total)

                if self.on_batch_complete:
                    self.on_batch_complete(batch_id, total, result)

            except Exception as e:
                print(f"✗ FAILED")
                print(f"  Error: {e}")
                # Save checkpoint for resume
                self._save_checkpoint(batch_id - 1, total)
                raise

        # Save final Layer 2 output
        self._save_full_results()
        print(f"\n✓ Layer 2 complete: {len(self.results)}/{total} batches processed")
        self._print_entity_stats()

        return self.results

    def _build_rolling_context(self) -> str:
        """Build the rolling context string from accumulated state."""
        parts = []

        # Add arc summaries (compressed old batches)
        if self.arc_summaries:
            parts.append("## 已总结的故事弧\n")
            for arc in self.arc_summaries:
                parts.append(f"- **{arc['label']}**: {arc['summary']}")
            parts.append("")

        # Add recent batch summaries (last 3)
        if self.recent_summaries:
            parts.append("## 最近剧情\n")
            for s in self.recent_summaries[-3:]:
                parts.append(f"### 批次{s['batch_id']} (第{s['start_ch']}-{s['end_ch']}章)")
                parts.append(s['summary'])
                if s.get('developments'):
                    parts.append("关键发展:")
                    for dev in s['developments']:
                        parts.append(f"  - {dev}")
                parts.append("")

        return "\n".join(parts) if parts else "（故事开始，无前文）"

    def _update_rolling_context(self, result: BatchAnalysisOutput):
        """Update rolling context with a completed batch's summary."""
        ns = result.narrative_summary

        # Store recent summary
        self.recent_summaries.append({
            "batch_id": result.batch_id,
            "start_ch": result.chapter_range[0],
            "end_ch": result.chapter_range[1],
            "summary": ns.summary,
            "developments": ns.major_developments,
        })

        # Keep only last 3
        if len(self.recent_summaries) > 3:
            self.recent_summaries = self.recent_summaries[-3:]

        # Every 10 batches, compress old summaries into arc summary
        if result.batch_id % 10 == 0 and len(self.recent_summaries) >= 3:
            # Move the oldest recent summary to arc summaries
            old = self.recent_summaries[0]
            self.arc_summaries.append({
                "label": f"第{old['start_ch']}-{old['end_ch']}章",
                "summary": old['summary'],
            })
            # Keep arc summaries bounded
            if len(self.arc_summaries) > 10:
                self.arc_summaries = self.arc_summaries[-10:]

        # Merge entity updates
        eu = result.entity_updates
        entity_updates_dict = {
            "characters": eu.characters,
            "factions": eu.factions,
            "locations": eu.locations,
            "powers": eu.powers,
            "unresolved_foreshadowing": eu.unresolved_foreshadowing,
        }
        self.entity_snapshot = merge_entity_snapshot(
            self.entity_snapshot, entity_updates_dict
        )

        # Prune entity snapshot if too large (>200 entities total)
        self._prune_entity_snapshot(max_entities=200)

    def _prune_entity_snapshot(self, max_entities: int = 200):
        """Prune entity snapshot to keep context bounded."""
        for entity_type in ["characters", "factions", "locations", "powers"]:
            entities = self.entity_snapshot.get(entity_type, [])
            if len(entities) <= max_entities:
                continue
            # Sort: active first, then by last_seen descending
            entities.sort(
                key=lambda e: (
                    0 if e.get("status") in ("active", None) else 1,
                    -(e.get("last_chapter_seen", e.get("last_appearance", 0)) or 0),
                )
            )
            self.entity_snapshot[entity_type] = entities[:max_entities]

    def _save_batch_result(self, result: BatchAnalysisOutput):
        """Save a single batch result as JSON."""
        output_path = self.output_dir / f"batch_{result.batch_id:04d}.json"
        output_path.write_text(
            result.model_dump_json(indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_full_results(self):
        """Save aggregated Layer 2 output."""
        full_path = self.output_dir / "layer2_full.json"
        data = {
            "novel_metadata": self.novel_metadata,
            "total_batches": len(self.results),
            "total_chapters_analyzed": sum(
                len(r.chapters) for r in self.results
            ),
            "entity_snapshot": self.entity_snapshot,
            "batches": [r.model_dump(mode="json") for r in self.results],
            "usage": self.client.usage_summary,
        }
        full_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _save_checkpoint(self, batch_id: int, total: int):
        """Save checkpoint state for resume."""
        state = {
            "layer": 2,
            "batch_id": batch_id,
            "total_batches": total,
            "novel_metadata": self.novel_metadata,
            "rolling_context": {
                "current_batch_id": batch_id,
                "recent_summaries": self.recent_summaries,
                "arc_summaries": self.arc_summaries,
                "entity_snapshot": self.entity_snapshot,
            },
            "usage": self.client.usage_summary,
            "updated_at": datetime.now().isoformat(),
        }
        cp_path = self.checkpoint_dir / "layer2_checkpoint.json"
        cp_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load_checkpoint(cls, checkpoint_dir: Path) -> Optional[dict]:
        """Load an existing Layer 2 checkpoint. Returns None if not found."""
        cp_path = Path(checkpoint_dir) / "layer2_checkpoint.json"
        if cp_path.exists():
            return json.loads(cp_path.read_text(encoding="utf-8"))
        return None

    @classmethod
    def restore_state(cls, checkpoint: dict) -> tuple[dict, str, list[dict], list[dict]]:
        """Restore rolling state from checkpoint.

        Returns: (entity_snapshot, rolling_summary, recent_summaries, arc_summaries)
        """
        rc = checkpoint.get("rolling_context", {})
        return (
            rc.get("entity_snapshot", {}),
            "",  # rolling_summary is rebuilt from summaries
            rc.get("recent_summaries", []),
            rc.get("arc_summaries", []),
        )

    def _print_entity_stats(self):
        """Print entity extraction statistics."""
        chars = len(self.entity_snapshot.get("characters", []))
        factions = len(self.entity_snapshot.get("factions", []))
        locations = len(self.entity_snapshot.get("locations", []))
        powers = len(self.entity_snapshot.get("powers", []))
        foreshadowing = len(self.entity_snapshot.get("unresolved_foreshadowing", []))

        print(f"  Entities extracted:")
        print(f"    Characters: {chars}")
        print(f"    Factions:   {factions}")
        print(f"    Locations:  {locations}")
        print(f"    Powers:     {powers}")
        print(f"    Foreshadowing: {foreshadowing}")
        print(f"  API Usage: {self.client.usage_summary}")
