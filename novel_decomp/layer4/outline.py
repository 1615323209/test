"""Full novel outline builder — organizes chapter summaries into a coherent outline.

Groups chapters into narrative segments (volumes/arcs) based on:
- Plot tag clustering
- Arc markers from batch analysis
- Chapter density changes
"""

from typing import Optional

from novel_decomp.models.chapter import ChapterSummary


def build_full_outline(
    chapters: list[ChapterSummary],
    narrative_summaries: list[dict],
    *,
    volume_size: int = 100,
) -> dict:
    """Build a hierarchical novel outline from chapter summaries.

    Args:
        chapters: Sorted list of chapter summaries.
        narrative_summaries: Batch narrative summaries with arc_markers.
        volume_size: Target chapters per volume (for auto-segmentation).

    Returns:
        Dict with:
            - title: str (for display)
            - total_chapters: int
            - volumes: list of volume dicts
            - chapter_outline: list of per-chapter summaries
    """
    total = len(chapters)

    # Detect volume boundaries from arc markers
    arc_boundaries = _extract_arc_boundaries(narrative_summaries, total)

    # Segment into volumes
    volumes = _segment_volumes(chapters, arc_boundaries, volume_size)

    # Build chapter-level outline
    chapter_outline = []
    for ch in chapters:
        chapter_outline.append({
            "number": ch.chapter_number,
            "title": ch.title,
            "summary": ch.summary,
            "pov": ch.pov_character,
            "key_events": [
                {"type": ev.type, "description": ev.description}
                for ev in ch.key_events[:3]  # Top 3 events
            ],
            "tone": ch.emotional_tone,
            "tags": ch.plot_tags,
            "locations": ch.locations_visited,
            "characters": ch.characters_appeared[:5],  # Top 5 characters
        })

    return {
        "title": "全书大纲",
        "total_chapters": total,
        "volumes": volumes,
        "chapter_outline": chapter_outline,
    }


def _extract_arc_boundaries(
    narrative_summaries: list[dict],
    total_chapters: int,
) -> set[int]:
    """Extract chapter numbers where story arcs transition.

    Args:
        narrative_summaries: Batch narrative summaries from Layer 2.
        total_chapters: Total novel chapters.

    Returns:
        Set of chapter numbers that mark arc boundaries.
    """
    boundaries = {1}  # Always start at chapter 1

    for ns in narrative_summaries:
        arc_markers = ns.get("arc_markers", [])
        ch_range = ns.get("chapter_range", [])

        if arc_markers and ch_range and len(ch_range) == 2:
            # If this batch has arc markers, use the first chapter as a boundary
            boundaries.add(ch_range[0])

    boundaries.add(total_chapters + 1)  # End marker
    return boundaries


def _segment_volumes(
    chapters: list[ChapterSummary],
    boundaries: set[int],
    default_size: int = 100,
) -> list[dict]:
    """Segment chapters into volumes based on arc boundaries or chapter count.

    Args:
        chapters: Sorted chapter summaries.
        boundaries: Detected arc boundary chapters.
        default_size: Fallback volume size if no boundaries detected.

    Returns:
        List of volume dicts with title, range, summary.
    """
    boundaries_sorted = sorted(boundaries)
    volumes = []
    vol_num = 0

    # If only start/end boundaries, segment by default size
    if len(boundaries_sorted) <= 2:
        for start in range(1, len(chapters) + 1, default_size):
            end = min(start + default_size - 1, len(chapters))
            vol_num += 1
            volumes.append({
                "volume_number": vol_num,
                "title": f"第{vol_num}卷",
                "chapter_range": [start, end],
                "chapter_count": end - start + 1,
                "summary": f"第{start}-{end}章",
                "key_developments": [],
            })
    else:
        for i in range(len(boundaries_sorted) - 1):
            start = boundaries_sorted[i]
            end = boundaries_sorted[i + 1] - 1

            # Only create volumes that actually contain chapters
            if start <= len(chapters) and end >= start:
                vol_num += 1
                volumes.append({
                    "volume_number": vol_num,
                    "title": f"第{vol_num}卷",
                    "chapter_range": [start, end],
                    "chapter_count": end - start + 1,
                    "summary": f"第{start}-{end}章",
                    "key_developments": [],
                })

    # Fill in volume summaries from actual chapter content
    for vol in volumes:
        start = vol["chapter_range"][0]
        end = vol["chapter_range"][1]
        vol_chapters = [c for c in chapters if start <= c.chapter_number <= end]

        if vol_chapters:
            # Collate key developments
            devs = set()
            for ch in vol_chapters:
                for ev in ch.key_events[:1]:  # Top event per chapter
                    if ev.significance == "major":
                        devs.add(ev.description)
            vol["key_developments"] = list(devs)[:10]

            # Generate volume summary from first/last chapters
            first_ch = vol_chapters[0]
            last_ch = vol_chapters[-1]
            vol["summary"] = f"从第{start}章「{first_ch.title}」到第{end}章「{last_ch.title}」"
            vol["opening"] = first_ch.summary[:100]
            vol["closing"] = last_ch.summary[:100]

    return volumes


def generate_outline_markdown(outline: dict) -> str:
    """Render the full outline as Markdown.

    Args:
        outline: Output from build_full_outline.

    Returns:
        Markdown string.
    """
    lines = [
        f"# {outline['title']}",
        f"",
        f"> 共 {outline['total_chapters']} 章, {len(outline['volumes'])} 卷",
        f"",
        "---",
        "",
    ]

    for vol in outline["volumes"]:
        lines.append(f"## {vol['title']}")
        lines.append(f"**{vol['chapter_range'][0]}-{vol['chapter_range'][1]}章** "
                     f"({vol['chapter_count']}章)")
        lines.append("")
        lines.append(f"{vol['summary']}")
        lines.append("")

        if vol.get("key_developments"):
            lines.append("### 关键发展")
            for dev in vol["key_developments"]:
                lines.append(f"- {dev}")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Chapter-level outline (condensed)
    lines.append("## 逐章概要")
    lines.append("")
    for ch in outline.get("chapter_outline", []):
        events = "; ".join(f"{ev['type']}: {ev['description']}"
                          for ev in ch.get("key_events", []))
        lines.append(f"- **第{ch['number']}章 {ch['title']}**: {ch['summary'][:80]}... "
                     f"({', '.join(ch.get('tags', [])[:2])})")
        lines.append(f"  - POV: {ch.get('pov', '')}, 地点: {', '.join(ch.get('locations', [])[:3])}")
        lines.append(f"  - 事件: {events}")
        lines.append("")

    return "\n".join(lines)
