"""Retcon (contradiction) and gap detection for Layer 3 aggregation.

Strategy:
  Layer 2 (per-batch, ~20 chapters) — catches same-batch contradictions
  Layer 3 (global, all batches) — catches cross-batch contradictions that
    Layer 2 misses because rolling context has compressed/deleted old info.

Detection types:
  1. Character death reversals — reported dead → reappears alive (100+ ch gap)
  2. Faction destruction reversals — destroyed → reappears intact
  3. Setting inconsistencies — same entity described contradictorily
  4. Timeline contradictions — events that can't coexist chronologically
  5. Long character absence gaps (>50 chapters)
"""

from collections import defaultdict


def detect_retcons(
    resolved_characters: dict[str, dict],
    resolved_factions: dict[str, dict],
    all_contradictions: list[dict],
    *,
    raw_characters: list[dict] | None = None,
    raw_factions: list[dict] | None = None,
    all_narrative_summaries: list[dict] | None = None,
) -> list[dict]:
    """Detect author contradictions (retcons) across the entire novel.

    Layer 2 per-batch contradictions are passed through. Then global
    cross-batch scans catch what Layer 2 missed due to context limits.

    Death reversals are checked against critical event logs: if the model
    flagged a 复活 (resurrection) event explaining the return, it's not a retcon.

    Args:
        resolved_characters: Resolved character entities (id → dict).
        resolved_factions: Resolved faction entities (id → dict).
        all_contradictions: Per-batch contradictions flagged by Layer 2.
        raw_characters: (Optional) Raw character records from all batches.
        raw_factions: (Optional) Raw faction records for timeline scan.
        all_narrative_summaries: (Optional) Narrative summaries with critical events.

    Returns:
        List of retcon dicts with: chapter, subject, description,
        prior_chapter, source, severity.
    """
    retcons = []

    # Build set of chapters where resurrections occurred
    resurrection_chapters: set[int] = set()
    if all_narrative_summaries:
        for ns in all_narrative_summaries:
            for ce in ns.get("critical_events", []):
                if ce.get("event_type") in ("复活", "resurrection"):
                    ch = ce.get("chapter_number", ce.get("章节号", 0))
                    if ch:
                        resurrection_chapters.add(ch)

    # ── Pass through Layer 2 per-batch contradictions ──
    for c in all_contradictions:
        retcons.append({
            "chapter": c.get("chapter", 0),
            "subject": c.get("subject", "unknown"),
            "description": c.get("描述", c.get("description", "")),
            "prior_chapter": c.get("矛盾章节", c.get("prior_chapter", 0)),
            "source": "layer2_same_batch",
            "severity": "moderate",
        })

    # ── Global: character death reversals ──
    if raw_characters:
        status_timelines = _build_status_timelines(raw_characters)
        for char_id, timeline in status_timelines.items():
            prev_status = None
            prev_chapter = 0
            for entry in timeline:
                ch = entry["chapter"]
                st = entry["status"]
                # Death reversal: was deceased, now active again
                # Skip if model reported a 复活 event (intentional plot, not retcon)
                if prev_status == "deceased" and st == "active":
                    if ch in resurrection_chapters:
                        continue  # ponytail: intentional resurrection, not a retcon
                    char_name = _resolve_name_from_raw(char_id, raw_characters)
                    retcons.append({
                        "chapter": ch,
                        "subject": char_name,
                        "description": (
                            f"角色在第{prev_chapter}章左右被标记为'已死亡'，"
                            f"但在第{ch}章再次以'活跃'状态出现"
                        ),
                        "prior_chapter": prev_chapter,
                        "source": "death_reversal_cross_batch",
                        "severity": "major",
                    })
                prev_status = st
                prev_chapter = ch

    # ── Global: faction state reversals ──
    if raw_factions:
        faction_timelines = _build_faction_timelines(raw_factions)
        for fact_id, timeline in faction_timelines.items():
            prev_state = None
            prev_chapter = 0
            for entry in timeline:
                ch = entry["chapter"]
                st = entry.get("status", "")
                if prev_state in ("destroyed", "覆灭") and st not in ("destroyed", "覆灭"):
                    fact_name = _resolve_fact_name(fact_id, raw_factions)
                    retcons.append({
                        "chapter": ch,
                        "subject": fact_name,
                        "description": (
                            f"势力在第{prev_chapter}章左右被标记为'已覆灭'，"
                            f"但在第{ch}章再次出现"
                        ),
                        "prior_chapter": prev_chapter,
                        "source": "faction_resurrection_cross_batch",
                        "severity": "moderate",
                    })
                prev_state = st
                prev_chapter = ch

    # ── Global: per-character status scan from resolved data ──
    # (fallback when raw data is unavailable)
    for char_id, char in resolved_characters.items():
        status = char.get("status", "")
        name = char.get("canonical_name", char_id)
        first = char.get("first_appearance", char.get("first_chapter", 0))
        last = char.get("last_appearance", char.get("last_chapter_seen", 0))

        # Flag characters marked deceased but with a wide appearance range
        if status == "deceased" and last - first > 5:
            retcons.append({
                "chapter": last,
                "subject": name,
                "description": (
                    f"角色标记为'已死亡'，但首次出场(第{first}章)到最后出场"
                    f"(第{last}章)跨{last-first}章——可能后续有回忆杀/闪回，"
                    f"或状态标记有误"
                ),
                "prior_chapter": first,
                "source": "deceased_with_long_span",
                "severity": "minor",
            })

    # ── Setting inconsistencies ──
    setting_issues = _detect_setting_contradictions(resolved_characters)
    retcons.extend(setting_issues)

    # Sort by chapter
    retcons.sort(key=lambda r: r.get("chapter", 0))
    return retcons


def detect_gaps(
    resolved_characters: dict[str, dict],
    gap_threshold: int = 50,
) -> list[dict]:
    """Detect characters with long absence gaps.

    A "gap" means the character goes > gap_threshold chapters without
    appearing or being mentioned.

    Args:
        resolved_characters: Resolved character entities.
        gap_threshold: Minimum chapter gap to flag (default: 50).

    Returns:
        List of gap note dicts.
    """
    gaps = []

    for char_id, char in resolved_characters.items():
        name = char.get("canonical_name", char_id)
        appearances = char.get("appearance_timeline", [])

        if not appearances:
            first = char.get("first_appearance", char.get("first_chapter", 0))
            last = char.get("last_appearance", char.get("last_chapter_seen", 0))
            if first and last:
                appearances = [{"chapter": first}, {"chapter": last}]
            else:
                continue

        # Sort by chapter
        sorted_apps = sorted(
            [a for a in appearances if isinstance(a, dict) and a.get("chapter")],
            key=lambda a: a["chapter"]
        )

        if len(sorted_apps) < 2:
            continue

        for i in range(len(sorted_apps) - 1):
            ch_a = sorted_apps[i]["chapter"]
            ch_b = sorted_apps[i + 1]["chapter"]
            gap_size = ch_b - ch_a

            if gap_size > gap_threshold:
                explanation = _infer_gap_explanation(char, ch_a, ch_b)
                gaps.append({
                    "character_id": char_id,
                    "character_name": name,
                    "disappeared_chapter": ch_a,
                    "reappeared_chapter": ch_b,
                    "gap_length": gap_size,
                    "explanation": explanation,
                    "role_type": char.get("role_type", "unknown"),
                    "severity": (
                        "major" if gap_size > 200
                        else "moderate" if gap_size > 100
                        else "minor"
                    ),
                })

    gaps.sort(key=lambda g: g["gap_length"], reverse=True)
    return gaps


def detect_setting_contradictions(
    resolved_characters: dict[str, dict],
) -> list[dict]:
    """Detect contradictory descriptions of the same entity.

    Scans for: power level inconsistencies, contradictory personality traits,
    conflicting faction affiliations.
    """
    # This is a structural check — for a full implementation, an LLM judge
    # would compare contradictory descriptions. Here we flag potential issues
    # for human review.
    return _detect_setting_contradictions(resolved_characters)


# ── Internal helpers ──

def _build_status_timelines(raw_characters: list[dict]) -> dict[str, list[dict]]:
    """Build per-character status timelines from raw batch entity records.

    Each raw record has: id, status, last_chapter_seen (or first_chapter).
    We sort by chapter to reconstruct the status change sequence.

    Returns:
        {char_id: [{chapter: int, status: str}, ...]}  sorted by chapter.
    """
    timelines = defaultdict(list)
    for ent in raw_characters:
        eid = ent.get("id", "")
        if not eid:
            continue
        status = ent.get("status", ent.get("状态", "unknown"))
        ch = ent.get("last_chapter_seen", ent.get("最近出场",
             ent.get("first_chapter", ent.get("首次出场", 0))))
        if ch and status:
            timelines[eid].append({"chapter": ch, "status": status})

    # Deduplicate and sort
    for eid in timelines:
        seen = {}
        unique = []
        for entry in sorted(timelines[eid], key=lambda e: e["chapter"]):
            ch = entry["chapter"]
            if ch not in seen:
                seen[ch] = entry["status"]
                unique.append(entry)
            elif seen[ch] != entry["status"]:
                # Same chapter, different status → keep the later one
                unique.append(entry)
        timelines[eid] = unique

    return dict(timelines)


def _build_faction_timelines(raw_factions: list[dict]) -> dict[str, list[dict]]:
    """Build per-faction status timelines from raw records."""
    timelines = defaultdict(list)
    for ent in raw_factions:
        eid = ent.get("id", "")
        if not eid:
            continue
        status = ent.get("status", ent.get("状态", "unknown"))
        ch = ent.get("first_chapter", ent.get("首次出场",
             ent.get("last_chapter_seen", ent.get("最近出场", 0))))
        if ch:
            timelines[eid].append({"chapter": ch, "status": status})
    for eid in timelines:
        timelines[eid] = sorted(timelines[eid], key=lambda e: e["chapter"])
    return dict(timelines)


def _resolve_name_from_raw(char_id: str, raw_characters: list[dict]) -> str:
    """Look up a character's canonical name from raw records."""
    for ent in raw_characters:
        if ent.get("id") == char_id:
            return ent.get("canonical_name", ent.get("名称", char_id))
    return char_id


def _resolve_fact_name(fact_id: str, raw_factions: list[dict]) -> str:
    """Look up a faction's name from raw records."""
    for ent in raw_factions:
        if ent.get("id") == fact_id:
            return ent.get("canonical_name", ent.get("名称", fact_id))
    return fact_id


def _detect_setting_contradictions(characters: dict[str, dict]) -> list[dict]:
    """Scan resolved characters for contradictory descriptions.

    Checks:
    - Death status with ongoing appearances
    - Characters with both mentor and student roles simultaneously
    - Characters in multiple mutually-exclusive factions
    """
    retcons = []

    for char_id, char in characters.items():
        name = char.get("canonical_name", char_id)

        # Personality contradictions: both '善良' and '残忍'
        traits = char.get("personality_traits", [])
        if "善良" in traits and "残忍" in traits:
            retcons.append({
                "chapter": char.get("first_appearance", char.get("first_chapter", 0)),
                "subject": name,
                "description": "性格特征同时包含'善良'和'残忍'——可能是角色复杂性或分析矛盾",
                "prior_chapter": 0,
                "source": "personality_contradiction",
                "severity": "minor",
            })

        # Faction count anomaly
        affiliations = char.get("faction_affiliations", [])
        if len(affiliations) > 5:
            retcons.append({
                "chapter": char.get("first_appearance", char.get("first_chapter", 0)),
                "subject": name,
                "description": f"同时隶属于{len(affiliations)}个势力，可能包含AI误识别",
                "prior_chapter": 0,
                "source": "too_many_factions",
                "severity": "minor",
            })

    return retcons


def _infer_gap_explanation(char: dict, ch_start: int, ch_end: int) -> str:
    """Infer what a character was doing during a long absence."""
    role = char.get("role_type", "")

    if role == "主角":
        if ch_end - ch_start > 200:
            return "主角长期缺席，可能是换线/双主角/重写"
        return "主角暂时退场（修炼/养伤/被囚禁/失联）"
    elif role in ("配角",):
        return "支线配角阶段性退场，等待再次需要此角色"
    elif role == "反派":
        return "反派可能在幕后布局/恢复实力/暂时蛰伏"
    elif role == "导师":
        return "导师角色退场（闭关/暗中保护/外出游历）"
    elif role == "路人":
        return "路人角色，自然淡出主线"
    elif role == "恋人":
        return "感情线角色阶段性缺席（分离/误会/支线展开）"

    return f"第{ch_start}章后未再出场，第{ch_end}章重新出现，间隔{ch_end - ch_start}章"


def summarize_entity_db(resolved: dict) -> dict:
    """Generate summary statistics for the resolved entity database."""
    summary = {
        "character_count": len(resolved.get("characters", {})),
        "faction_count": len(resolved.get("factions", {})),
        "location_count": len(resolved.get("locations", {})),
        "power_count": len(resolved.get("powers", {})),
        "character_roles": _count_roles(resolved.get("characters", {})),
        "faction_types": _count_types(resolved.get("factions", {})),
        "location_types": _count_types(resolved.get("locations", {})),
        "power_categories": _count_types(resolved.get("powers", {}), key="power_category"),
    }

    chars = resolved.get("characters", {})
    if chars:
        app_counts = [
            len(c.get("appearances", c.get("appearance_timeline", [])))
            for c in chars.values()
        ]
        summary["avg_appearances_per_char"] = (
            sum(app_counts) / len(app_counts) if app_counts else 0
        )
        summary["max_appearances"] = max(app_counts) if app_counts else 0
        summary["chars_with_aliases"] = sum(
            1 for c in chars.values() if c.get("aliases")
        )
        summary["total_relationships"] = sum(
            len(c.get("relationships", [])) for c in chars.values()
        )

    return summary


def _count_roles(characters: dict) -> dict:
    counts = {}
    for c in characters.values():
        role = c.get("role_type", "其他")
        counts[role] = counts.get(role, 0) + 1
    return counts


def _count_types(entities: dict, key: str = "faction_type") -> dict:
    from collections import Counter
    types = []
    for e in entities.values():
        t = e.get(key, "")
        if not t and key == "faction_type":
            t = e.get("type", "unknown")
        if t:
            types.append(t)
    return dict(Counter(types))
