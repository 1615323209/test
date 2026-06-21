"""Character and faction profile builder.

Transforms resolved entity data into readable narrative profiles.
"""

from collections import defaultdict
from typing import Optional


def _normalize_faction_names(items: list) -> list[str]:
    """Extract faction names from items that may be strings or dicts."""
    result = []
    for item in (items or []):
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            name = item.get("faction") or item.get("势力") or str(item)
            result.append(name)
    return result


def _resolve_name(name_or_id: str, entities: dict[str, dict] | None = None, *, characters: dict[str, dict] | None = None, factions: dict[str, dict] | None = None) -> str:
    """Resolve a name-or-ID string to a display name.

    If it looks like an entity ID (char_xxx, fact_xxx, loc_xxx, pow_xxx),
    look up the canonical_name in the appropriate entity dict.
    Falls back to extracting a readable name from the ID if the entity
    is not found in any dictionary.

    Args:
        name_or_id: The string to resolve (may be a name or an ID).
        entities: Optional combined dict of all entity types (id → entity).
                  Only used as fallback if characters/factions are not provided.
        characters: Character entities dict (id → entity).
        factions: Faction entities dict (id → entity).

    Returns:
        Human-readable display name.
    """
    if not name_or_id:
        return "未知"

    # Check if it's an entity ID pattern
    if name_or_id.startswith("char_"):
        if characters and name_or_id in characters:
            return characters[name_or_id].get("canonical_name", name_or_id)
        if entities and name_or_id in entities:
            return entities[name_or_id].get("canonical_name", name_or_id)
        # Fallback: extract readable name from char_xxx → xxx
        return _id_fallback(name_or_id, "char_")
    elif name_or_id.startswith("fact_"):
        if factions and name_or_id in factions:
            return factions[name_or_id].get("canonical_name", name_or_id)
        if entities and name_or_id in entities:
            return entities[name_or_id].get("canonical_name", name_or_id)
        return _id_fallback(name_or_id, "fact_")
    elif name_or_id.startswith("loc_"):
        if entities and name_or_id in entities:
            return entities[name_or_id].get("canonical_name", name_or_id)
        return _id_fallback(name_or_id, "loc_")
    elif name_or_id.startswith("pow_"):
        if entities and name_or_id in entities:
            return entities[name_or_id].get("canonical_name", name_or_id)
        return _id_fallback(name_or_id, "pow_")

    # If it's already a display name, return as-is
    return name_or_id


def _id_fallback(raw_id: str, prefix: str) -> str:
    """Extract a readable fallback name from an unresolvable entity ID.

    Example: 'char_chenyan' → 'chenyan(未注册)'
             'fact_huanghunshe' → 'huanghunshe(未注册)'
    """
    name_part = raw_id[len(prefix):] if raw_id.startswith(prefix) else raw_id
    if not name_part:
        return raw_id
    return f"{name_part}(未注册)"


def build_character_evolution(
    raw_characters: list[dict],
    *,
    resolved_factions: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Build per-character evolution timelines from raw batch entity data.

    Scans all raw character records (one per batch per entity), groups by id,
    sorts by chapter, and detects changes in personality, power, and status.

    Args:
        raw_characters: Flat list of all character records from all batches.
            Each has: id, canonical_name, status, personality_traits,
            power_hints, last_chapter_seen, role_type.
        resolved_factions: Resolved faction entities (id → entity dict),
            used to normalize faction IDs to display names.

    Returns:
        {char_id: {
            personality_stages: [{chapter, traits}, ...],
            power_stages: [{chapter, hints}, ...],
            status_timeline: [{chapter, status}, ...],
            faction_changes: [{chapter, faction}, ...],
            role_changes: [{chapter, role}, ...],
        }}
    """
    factions = resolved_factions or {}

    # Group raw records by entity id
    by_id = defaultdict(list)
    for ent in raw_characters:
        eid = ent.get("id", "")
        if not eid:
            continue
        ch = ent.get("last_chapter_seen", ent.get("最近出场",
             ent.get("first_chapter", ent.get("首次出场", 0))))
        if ch:
            # Normalize faction: resolve fact_xxx IDs to display names
            raw_faction = ent.get("faction", ent.get("所属势力", ""))
            faction_name = _resolve_name(raw_faction, factions=factions) if raw_faction else ""

            # Check for explicit evolution data from new schema
            pevo = ent.get("personality_evolution", ent.get("性格演变", []))
            pwevo = ent.get("power_evolution", ent.get("能力演变", []))
            affs = ent.get("faction_affiliations", ent.get("势力归属", []))
            by_id[eid].append({
                "chapter": ch,
                "status": ent.get("status", ent.get("状态", "")),
                "personality": ent.get("personality_traits", ent.get("性格特征", [])),
                "powers": ent.get("power_hints", ent.get("能力提示", [])),
                "faction": faction_name,
                "role": ent.get("role_type", ent.get("角色定位", "")),
                "personality_evolution": pevo,
                "power_evolution": pwevo,
                "faction_affiliations": affs,
            })

    result = {}
    for eid, records in by_id.items():
        records.sort(key=lambda r: r["chapter"])

        # Build personality stages: prefer explicit evolution data
        personality_stages = []
        # Collect all explicit evolution entries
        seen_chapters = set()
        for r in records:
            for ev in r.get("personality_evolution", []):
                ch_ev = ev.get("chapter", ev.get("章节", 0))
                if ch_ev not in seen_chapters:
                    seen_chapters.add(ch_ev)
                    personality_stages.append({
                        "chapter": ch_ev,
                        "traits": ev.get("traits", ev.get("性格", [])),
                        "trigger": ev.get("trigger", ev.get("触发事件", "")),
                    })
        # Fallback: detect changes from personality_traits if no explicit data
        if not personality_stages:
            prev_traits = None
            for r in records:
                traits = tuple(sorted(r["personality"])) if r["personality"] else ()
                if traits and traits != prev_traits:
                    personality_stages.append({
                        "chapter": r["chapter"],
                        "traits": list(traits),
                        "trigger": "",
                    })
                    prev_traits = traits
        personality_stages.sort(key=lambda s: s["chapter"])

        # Build power stages: prefer explicit evolution data
        power_stages = []
        for r in records:
            pevo = r.get("power_evolution", [])
            for ev in pevo:
                power_stages.append({
                    "chapter": ev.get("chapter", ev.get("章节", r["chapter"])),
                    "new": ev.get("new", ev.get("新增", [])),
                    "trigger": ev.get("trigger", ev.get("触发事件", "")),
                })
        # Fallback: detect from power_hints changes
        if not power_stages:
            all_powers = []
            for r in records:
                if r["powers"]:
                    new_powers = [p for p in r["powers"] if p not in all_powers]
                    if new_powers:
                        all_powers.extend(new_powers)
                        power_stages.append({
                            "chapter": r["chapter"],
                            "new": new_powers,
                            "trigger": "",
                        })
        power_stages.sort(key=lambda s: s["chapter"])

        # Build status timeline: detect changes
        status_timeline = []
        prev_status = None
        for r in records:
            st = r["status"]
            if st and st != prev_status:
                status_timeline.append({
                    "chapter": r["chapter"],
                    "status": st,
                })
                prev_status = st

        # Build faction change timeline — prefer explicit affiliation data
        faction_changes = []
        for r in records:
            affs = r.get("faction_affiliations", r.get("势力归属", []))
            for aff in affs:
                faction_changes.append({
                    "chapter": aff.get("chapter", aff.get("确定章节", r["chapter"])),
                    "faction": aff.get("faction", aff.get("势力", "")),
                    "nature": aff.get("nature", aff.get("性质", "正式加入")),
                    "note": aff.get("note", aff.get("备注", "")),
                })
        if not faction_changes:
            prev_faction = None
            for r in records:
                fac = r["faction"]
                if fac and fac != prev_faction:
                    faction_changes.append({
                        "chapter": r["chapter"],
                        "faction": fac,
                        "nature": "",
                        "note": "",
                    })
                    prev_faction = fac
        # Deduplicate: keep only earliest entry per faction, merge notes
        seen: dict[str, dict] = {}  # faction_name -> merged entry
        for fc in faction_changes:
            fname = fc["faction"]
            if fname not in seen:
                seen[fname] = fc
            else:
                # Keep earliest chapter, merge notes if later has more detail
                if fc["chapter"] < seen[fname]["chapter"]:
                    seen[fname]["chapter"] = fc["chapter"]
                if fc["note"] and len(fc["note"]) > len(seen[fname].get("note", "")):
                    seen[fname]["note"] = fc["note"]
                if fc["nature"] and fc["nature"] != "正式加入":
                    seen[fname]["nature"] = fc["nature"]
        faction_changes = sorted(seen.values(), key=lambda x: x["chapter"])

        # Build role change timeline
        role_changes = []
        prev_role = None
        for r in records:
            role = r["role"]
            if role and role != prev_role:
                role_changes.append({
                    "chapter": r["chapter"],
                    "role": role,
                })
                prev_role = role

        result[eid] = {
            "personality_stages": personality_stages,
            "power_stages": power_stages,
            "status_timeline": status_timeline,
            "faction_changes": faction_changes,
            "role_changes": role_changes,
        }

    return result


def build_character_profiles(
    characters: dict[str, dict],
    *,
    factions: dict[str, dict] | None = None,
    evolutions: dict[str, dict] | None = None,
) -> list[dict]:
    """Build narrative character profiles from resolved entities.

    Args:
        characters: Resolved character entities (id → entity dict).
        factions: Resolved faction entities (id → entity dict),
            used to resolve faction IDs in affiliations and relationships.
        evolutions: Character evolution timelines from build_character_evolution().

    Returns:
        List of profile dicts, sorted by role importance then appearance count.
    """
    factions = factions or {}
    profiles = []

    for char_id, char in characters.items():
        name = char.get("canonical_name", char_id)

        # Build relationship summary (resolve char_xxx IDs → names)
        relationships = char.get("relationships", [])
        rel_summary = []
        for rel in relationships:
            target = rel.get("target_name", rel.get("target", "?"))
            rtype = rel.get("relation_type", rel.get("relation", "相关"))
            # Resolve entity IDs to actual names
            target = _resolve_name(target, characters=characters, factions=factions)
            rel_summary.append(f"{rtype}: {target}")

        # Build appearance summary
        appearances = char.get("appearance_timeline", [])
        app_chapters = [a.get("chapter", 0) for a in appearances if isinstance(a, dict)]
        app_chapters.sort()

        # Resolve first/last appearance: Layer 2 outputs "first_chapter"/"last_chapter_seen",
        # but older code may use "first_appearance"/"last_appearance". Check all variants.
        first_app = (
            char.get("first_chapter")
            or char.get("first_appearance")
            or (app_chapters[0] if app_chapters else 0)
            or 0
        )
        last_app = (
            char.get("last_chapter_seen")
            or char.get("last_appearance")
            or (app_chapters[-1] if app_chapters else 0)
            or 0
        )
        # If we have chapter range but no timeline, synthesize one
        if not app_chapters and first_app and last_app:
            app_chapters = [first_app, last_app]
            # For major characters, fill in approximate range
            if last_app - first_app <= 200:
                app_chapters = list(range(first_app, last_app + 1))

        # Deduplicate aliases: keep unique, remove overly long ones (>20 chars)
        raw_aliases = char.get("aliases", [])
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        seen = set()
        clean_aliases = []
        for a in raw_aliases:
            a = str(a).strip()
            if not a or len(a) > 20:
                continue  # Skip empty or overly long "aliases" that are descriptions
            if a not in seen:
                seen.add(a)
                clean_aliases.append(a)

        # Build profile
        profile = {
            "id": char_id,
            "name": name,
            "aliases": clean_aliases[:30],  # Cap at 30 most important
            "role": char.get("role_type", "未知"),
            "gender": char.get("gender", ""),
            "age_hint": char.get("age_hint", ""),
            "personality": char.get("personality_traits", []),
            "background": char.get("background", ""),
            "catchphrases": char.get("catchphrases", []),
            "appearance_traits": char.get("appearance_traits", []),
            "faction_affiliations": _normalize_faction_names(char.get("faction_affiliations", [])),
            "powers": char.get("power_hints", char.get("powers", [])),
            "goals": char.get("goals", []),
            "relationships": rel_summary,
            "first_appearance": first_app,
            "last_appearance": last_app,
            "appearance_count": len(app_chapters),
            "appearance_chapters": app_chapters[:20],  # First 20 chapters
            "status": char.get("status", "unknown"),
            "description": char.get("description", ""),
            "character_arc": char.get("character_arc", ""),
            "image_hints": char.get("image_hints", []),
            # Evolution timeline (from raw batch data)
            "evolution": evolutions.get(char_id, {}) if evolutions else {},
        }

        profiles.append(profile)

    # Sort: main characters first, then by appearance count
    role_order = {"主角": 0, "反派": 1, "导师": 2, "恋人": 3, "家人": 4,
                   "配角": 5, "路人": 6, "其他": 7}
    profiles.sort(key=lambda p: (
        role_order.get(p["role"], 99),
        -p["appearance_count"],
    ))

    return profiles


def build_faction_profiles(
    factions: dict[str, dict],
    *,
    characters: dict[str, dict] | None = None,
) -> list[dict]:
    """Build narrative faction profiles from resolved entities.

    Args:
        factions: Resolved faction entities (id → entity dict).
        characters: Resolved character entities (id → entity dict),
            used to resolve member/leader IDs to display names.

    Returns:
        List of faction profile dicts.
    """
    characters = characters or {}
    profiles = []

    for fact_id, faction in factions.items():
        name = faction.get("canonical_name", fact_id)

        # Resolve leader ID → name
        leader = _resolve_name(faction.get("leader", ""), characters=characters, factions=factions)

        # Members: try all possible field names, ensure list
        members_raw = faction.get("member_names") or faction.get("members") or []
        if isinstance(members_raw, str):
            members_raw = [members_raw]

        # Resolve member IDs to names
        members_resolved = [_resolve_name(m, characters=characters, factions=factions) for m in members_raw]

        # First/last chapter: handle field name variants
        first_ch = (
            faction.get("first_chapter")
            or faction.get("first_appearance")
            or 0
        )
        last_ch = (
            faction.get("last_chapter_seen")
            or faction.get("last_appearance")
            or 0
        )

        # Build timeline summary
        timeline = faction.get("timeline", [])
        timeline_events = []
        for event in sorted(timeline, key=lambda e: e.get("chapter", 0)):
            timeline_events.append({
                "chapter": event.get("chapter", 0),
                "event": event.get("event", ""),
                "type": event.get("event_type", ""),
            })

        profile = {
            "id": fact_id,
            "name": name,
            "type": faction.get("faction_type", faction.get("type", "未知")),
            "leader": leader,
            "members": members_resolved,
            "member_count": len(members_resolved),
            "ideology": faction.get("ideology", ""),
            "goals": faction.get("goals", []),
            "territory": faction.get("territory", ""),
            "allies": faction.get("allies", []),
            "enemies": faction.get("enemies", []),
            "strength_hint": faction.get("strength_hint", ""),
            "first_appearance": first_ch,
            "last_appearance": last_ch,
            "status": faction.get("status", "unknown"),
            "description": faction.get("description", ""),
            "internal_conflicts": faction.get("internal_conflicts", []),
            "timeline": timeline_events,
        }

        profiles.append(profile)

    # Sort by member count (largest first)
    profiles.sort(key=lambda p: -p["member_count"])
    return profiles


def build_location_profiles(locations: dict[str, dict]) -> list[dict]:
    """Build location profiles.

    Args:
        locations: Resolved location entities.

    Returns:
        List of location profile dicts.
    """
    profiles = []

    for loc_id, loc in locations.items():
        name = loc.get("canonical_name", loc_id)

        first_ch = (
            loc.get("first_chapter")
            or loc.get("first_appearance")
            or 0
        )

        profile = {
            "id": loc_id,
            "name": name,
            "type": loc.get("location_type", "未知"),
            "parent": loc.get("parent_location", ""),
            "significance": loc.get("significance", ""),
            "features": loc.get("features", []),
            "affiliated_factions": loc.get("affiliated_factions", []),
            "first_appearance": first_ch,
            "chapter_count": len(loc.get("chapters_present", [])),
            "description": loc.get("description", ""),
            "map_hints": loc.get("map_hints", ""),
        }

        profiles.append(profile)

    profiles.sort(key=lambda p: -p["chapter_count"])
    return profiles


def build_power_profiles(powers: dict[str, dict]) -> list[dict]:
    """Build power/ability profiles.

    Args:
        powers: Resolved power entities.

    Returns:
        List of power profile dicts.
    """
    profiles = []

    for pow_id, power in powers.items():
        name = power.get("canonical_name", pow_id)

        # Users: try both field names, ensure list
        users_raw = power.get("user_names") or power.get("users") or []
        if isinstance(users_raw, str):
            users_raw = [users_raw]

        first_ch = (
            power.get("first_chapter")
            or power.get("first_appearance")
            or 0
        )

        # Normalize tiers: could be flat list or nested dict from new schema
        tiers_raw = power.get("tiers", [])
        if isinstance(tiers_raw, dict):
            tier_names = tiers_raw.get("阶名列表", tiers_raw.get("tier_names", []))
            tier_details = tiers_raw.get("阶详情", tiers_raw.get("tier_details", []))
        elif isinstance(tiers_raw, list):
            # Old format: list of strings
            tier_names = tiers_raw if tiers_raw and isinstance(tiers_raw[0], str) else []
            tier_details = tiers_raw if tier_names else []
        else:
            tier_names = []
            tier_details = []

        profile = {
            "id": pow_id,
            "name": name,
            "category": power.get("power_category", "未知"),
            "parent_system": power.get("parent_system", ""),
            "users": users_raw,
            "user_count": len(users_raw),
            "tiers": tier_names,
            "tier_details": tier_details,
            "tier_count": power.get("tier_count") or len(tier_names) or 0,
            "source": power.get("source", ""),
            "mechanics": power.get("mechanics", ""),
            "limitations": power.get("limitations", []),
            "first_appearance": first_ch,
            "description": power.get("description", ""),
        }

        profiles.append(profile)

    profiles.sort(key=lambda p: -p["user_count"])
    return profiles
