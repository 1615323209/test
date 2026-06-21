"""Plot arc synthesis — identify and organize story arcs from chapter data.

Groups chapters into coherent arcs based on:
- Plot tag continuity
- Character set overlaps
- Arc markers from Layer 2 analysis
- Emotional peaks (climax detection)
"""

from novel_decomp.models.chapter import ChapterSummary
from novel_decomp.layer4.profiles import _resolve_name


def synthesize_plot_arcs(
    chapters: list[ChapterSummary],
    narrative_summaries: list[dict],
    *,
    resolved_characters: dict[str, dict] | None = None,
    resolved_locations: dict[str, dict] | None = None,
) -> list[dict]:
    """Identify and construct plot arcs from chapter summaries.

    Args:
        chapters: Sorted chapter summaries.
        narrative_summaries: Batch narrative summaries (with arc_markers).
        resolved_characters: Resolved character entities (id → entity dict),
            used to normalize character names.
        resolved_locations: Resolved location entities (id → entity dict),
            used to normalize location names.

    Returns:
        List of arc dicts with structure suitable for PlotArc model.
    """
    characters = resolved_characters or {}
    locations = resolved_locations or {}

    arcs = []
    arc_id = 0

    # Use arc markers from narrative summaries as primary boundaries
    arc_boundaries = _find_arc_boundaries(narrative_summaries, len(chapters))

    # For each boundary pair, construct an arc
    for i in range(len(arc_boundaries) - 1):
        start_ch = arc_boundaries[i]
        end_ch = arc_boundaries[i + 1] - 1

        arc_chapters = [c for c in chapters
                       if start_ch <= c.chapter_number <= end_ch]

        if not arc_chapters:
            continue

        arc_id += 1

        # Classify arc type
        arc_type = _classify_arc_type(arc_chapters)
        all_characters: set[str] = set()
        all_locations: set[str] = set()
        for ch in arc_chapters:
            for c in ch.characters_appeared:
                all_characters.add(c if isinstance(c, str) else c.get("名称", c.get("name", str(c))))
            all_locations.update(ch.locations_visited)
        _char_name_set = all_characters

        # Find emotional peak
        peak_chapter = _find_emotional_peak(arc_chapters)

        # Generate arc summary
        summary = _generate_arc_summary(arc_chapters, start_ch, end_ch, arc_type)

        # Find primary characters (appear in >50% of chapters)
        # Resolve any entity IDs to display names
        ch_count = len(arc_chapters)
        primary_characters_raw = [
            c for c in all_characters
            if sum(1 for ch in arc_chapters
                   if _char_in_chapter(c, ch)) > ch_count * 0.5
        ]
        primary_characters = [
            _resolve_name(c, characters=characters)
            for c in primary_characters_raw[:10]
        ]

        # Resolve location IDs to display names
        primary_locations = [
            _resolve_name(loc, entities=locations)
            for loc in list(all_locations)[:10]
        ]

        arcs.append({
            "id": f"arc_{arc_id:03d}",
            "name": f"第{start_ch}-{end_ch}章",
            "arc_type": arc_type,
            "start_chapter": start_ch,
            "end_chapter": end_ch,
            "chapter_count": ch_count,
            "summary": summary,
            "primary_characters": primary_characters,
            "primary_locations": primary_locations,
            "emotional_peak_chapter": peak_chapter,
            "is_complete": end_ch < len(chapters),
            "beats": [
                {
                    "chapter": ch.chapter_number,
                    "summary": ch.summary[:100],
                    "significance": "major" if ch.chapter_number == peak_chapter else "transitional",
                }
                for ch in arc_chapters[::max(1, ch_count // 10)]  # Sample ~10 beats
            ],
        })

    return arcs


def _find_arc_boundaries(
    narrative_summaries: list[dict],
    total_chapters: int,
) -> list[int]:
    """Find chapter numbers where arcs begin/end."""
    boundaries = {1}

    for ns in narrative_summaries:
        markers = ns.get("arc_markers", [])
        ch_range = ns.get("chapter_range", [])

        if markers and ch_range:
            # A batch with arc markers starts a new arc
            boundaries.add(ch_range[0])

        # Large developments also suggest boundaries
        developments = ns.get("major_developments", [])
        if len(developments) >= 5 and ch_range:
            boundaries.add(ch_range[0])

    boundaries.add(total_chapters + 1)
    return sorted(boundaries)


def _classify_arc_type(
    arc_chapters: list[ChapterSummary],
) -> str:
    """Classify the arc type based on chapter count.

    Simplified: without plot_tags, we use chapter count as a basic heuristic.
    Full classification would need more semantic analysis.
    """
    if len(arc_chapters) <= 5:
        return "过渡篇"
    elif len(arc_chapters) <= 15:
        return "中篇"
    else:
        return "长篇章"


def _find_emotional_peak(arc_chapters: list[ChapterSummary]) -> int:
    """Find the chapter that serves as the emotional peak/climax.

    Heuristic: Uses event significance and emotional tone shifts.
    """
    peak_chapter = 0
    peak_score = 0

    for ch in arc_chapters:
        score = 0
        # Major events boost score
        for ev in ch.key_events:
            if ev.significance == "major":
                score += 3
            elif ev.significance == "transitional":
                score += 1

        # Death events are peak indicators
        for ev in ch.key_events:
            if ev.type == "死亡":
                score += 5

        if score > peak_score:
            peak_score = score
            peak_chapter = ch.chapter_number

    return peak_chapter or (arc_chapters[len(arc_chapters) // 2].chapter_number if arc_chapters else 0)


def _generate_arc_summary(
    arc_chapters: list[ChapterSummary],
    start_ch: int,
    end_ch: int,
    arc_type: str,
) -> str:
    """Generate a brief arc summary from chapter content."""
    if not arc_chapters:
        return f"{arc_type}，第{start_ch}-{end_ch}章"

    first = arc_chapters[0]
    last = arc_chapters[-1]

    # Collect key events
    major_events = []
    for ch in arc_chapters:
        for ev in ch.key_events:
            if ev.significance == "major":
                major_events.append(f"第{ch.chapter_number}章: {ev.description}")

    summary = f"{arc_type}，共{len(arc_chapters)}章。"
    summary += f"开始: {first.summary[:60]}..."

    if major_events:
        summary += f" 关键转折: {'; '.join(major_events[:3])}"

    summary += f" 结束: {last.summary[:60]}..."

    return summary


def _char_in_chapter(name: str, ch: ChapterSummary) -> bool:
    """Check if a character name appears in a chapter (handles both str and dict formats)."""
    for c in ch.characters_appeared:
        if isinstance(c, str):
            if c == name:
                return True
        elif isinstance(c, dict):
            if c.get("名称", c.get("name", "")) == name:
                return True
    return False
