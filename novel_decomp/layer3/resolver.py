"""Entity resolution — deduplicate and merge entities across batches.

Strategy (ordered by precision):
1. Exact ID match → automatic merge
2. Canonical name match (ignoring whitespace/case) → automatic merge
3. Alias overlap (one entity's name in another's aliases, or vice versa) → merge
4. Remaining candidates with similar names (edit distance) → flagged for review
"""

import re
from typing import Optional


def resolve_entities(raw_collection: dict) -> dict:
    """Resolve entity duplicates across all batches.

    Args:
        raw_collection: Output from collate_batch_results, containing
            raw_characters, raw_factions, raw_locations, raw_powers.

    Returns:
        Dict with resolved entities:
            - characters: dict keyed by canonical ID
            - factions: dict keyed by canonical ID
            - locations: dict keyed by canonical ID
            - powers: dict keyed by canonical ID
            - resolution_stats: dict with merge counts
    """
    result = {}
    stats = {}

    # Resolve each entity type
    for entity_type in ["characters", "factions", "locations", "powers"]:
        raw_key = f"raw_{entity_type}"
        raw_entities = raw_collection.get(raw_key, [])

        resolved, merge_count = _resolve_entity_list(raw_entities, entity_type)
        result[entity_type] = resolved
        stats[entity_type] = {
            "raw_count": len(raw_entities),
            "resolved_count": len(resolved),
            "merges": merge_count,
        }

    result["resolution_stats"] = stats
    return result


def _resolve_entity_list(
    raw_entities: list[dict],
    entity_type: str,
) -> tuple[dict, int]:
    """Resolve a single entity type list.

    Args:
        raw_entities: List of raw entity dicts from batch outputs.
        entity_type: Entity type name for logging.

    Returns:
        (resolved_dict, merge_count) where resolved_dict is keyed by canonical ID.
    """
    resolved: dict[str, dict] = {}
    merges = 0

    # Pass 1: Group by canonical ID (exact match)
    for entity in raw_entities:
        eid = entity.get("id", "")
        if not eid:
            # Generate a fallback ID
            name = entity.get("canonical_name", "unknown")
            eid = f"{_entity_prefix(entity_type)}{_slugify(name)}"
            entity["id"] = eid

        if eid in resolved:
            resolved[eid] = _merge_two_entities(resolved[eid], entity)
            merges += 1
        else:
            resolved[eid] = dict(entity)

    # Pass 2: Merge by name/alias overlap
    merged_ids = set()
    ids = list(resolved.keys())
    for i in range(len(ids)):
        if ids[i] in merged_ids:
            continue
        for j in range(i + 1, len(ids)):
            if ids[j] in merged_ids:
                continue

            e1 = resolved[ids[i]]
            e2 = resolved[ids[j]]

            if _should_merge(e1, e2):
                resolved[ids[i]] = _merge_two_entities(e1, e2)
                merged_ids.add(ids[j])
                merges += 1

    # Remove merged entities
    for mid in merged_ids:
        del resolved[mid]

    return resolved, merges


def _should_merge(e1: dict, e2: dict) -> bool:
    """Determine if two entities should be merged.

    Returns True if the entities likely refer to the same real-world entity.
    """
    # Match by canonical name (normalized)
    name1 = _normalize_name(e1.get("canonical_name", ""))
    name2 = _normalize_name(e2.get("canonical_name", ""))
    if name1 and name2 and name1 == name2:
        return True

    # Check if either name appears in the other's aliases
    aliases_raw1 = e1.get("aliases", [])
    aliases_raw2 = e2.get("aliases", [])
    if isinstance(aliases_raw1, str): aliases_raw1 = [aliases_raw1]
    if isinstance(aliases_raw2, str): aliases_raw2 = [aliases_raw2]
    aliases1 = set(_normalize_name(a) for a in (aliases_raw1 or []))
    aliases2 = set(_normalize_name(a) for a in (aliases_raw2 or []))

    if name1 and name1 in aliases2:
        return True
    if name2 and name2 in aliases1:
        return True

    # Alias overlap: if 2+ aliases match, they're likely the same
    alias_overlap = aliases1 & aliases2
    if len(alias_overlap) >= 2:
        return True

    return False


def _merge_two_entities(base: dict, update: dict) -> dict:
    """Merge update into base, keeping base's ID.

    Rules:
    - Lists: union
    - Strings: keep longest/most informative
    - Numbers: keep from entity with later appearance
    """
    merged = dict(base)

    # Update last_appearance / last_chapter_seen
    for key in ("last_appearance", "last_chapter_seen"):
        base_val = base.get(key, 0) or 0
        update_val = update.get(key, 0) or 0
        merged[key] = max(base_val, update_val)

    # First appearance: keep earliest
    for key in ("first_appearance", "first_chapter"):
        base_val = base.get(key, 99999) or 99999
        update_val = update.get(key, 99999) or 99999
        merged[key] = min(base_val, update_val)

    # Aliases: union
    merged["aliases"] = list(set(
        base.get("aliases", []) + update.get("aliases", [])
    ))

    # Personality traits: union
    merged["personality_traits"] = list(set(
        base.get("personality_traits", []) +
        update.get("personality_traits", [])
    ))

    # Powers: union
    merged["power_hints"] = list(set(
        base.get("power_hints", []) +
        update.get("power_hints", [])
    ))

    # Relationships: merge by target
    base_rels = {r.get("target", ""): r for r in base.get("relationships", [])}
    for r in update.get("relationships", []):
        target = r.get("target", "")
        if target and target in base_rels:
            # Keep the more detailed description
            existing = base_rels[target]
            if len(r.get("description", "")) > len(existing.get("description", "")):
                existing.update(r)
        else:
            base_rels[target] = dict(r)
    merged["relationships"] = list(base_rels.values())

    # Status: prefer non-"unknown" status
    if base.get("status") == "unknown" and update.get("status") != "unknown":
        merged["status"] = update.get("status")

    # Description: keep longer
    if len(update.get("description", "")) > len(base.get("description", "")):
        merged["description"] = update.get("description")

    # Members / users: union
    for key in ("members", "user_names", "users"):
        base_list = base.get(key, [])
        update_list = update.get(key, [])
        if base_list or update_list:
            merged[key] = list(set(base_list + update_list))

    # Confidence: take average weighted toward 1.0
    c1 = base.get("confidence", 1.0)
    c2 = update.get("confidence", 1.0)
    merged["confidence"] = min(c1, c2)

    # role_type: prefer more specific
    role_order = {"主角": 1, "反派": 2, "导师": 3, "恋人": 4, "家人": 5,
                   "配角": 6, "路人": 7, "其他": 99}
    base_role = role_order.get(base.get("role_type", "其他"), 99)
    update_role = role_order.get(update.get("role_type", "其他"), 99)
    if update_role < base_role:
        merged["role_type"] = update.get("role_type")

    return merged


def _normalize_name(name: str) -> str:
    """Normalize entity name for comparison."""
    if not name:
        return ""
    # Remove whitespace, normalize common separators
    return re.sub(r"\s+", "", name).lower().strip()


def _slugify(name: str) -> str:
    """Convert Chinese/English name to a slug for ID generation."""
    # Simple: use pinyin-like romanization for common characters
    # Fallback: use hashed representation
    import hashlib
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:12]


def _entity_prefix(entity_type: str) -> str:
    """Get ID prefix for entity type."""
    return {
        "characters": "char_",
        "factions": "fact_",
        "locations": "loc_",
        "powers": "pow_",
    }.get(entity_type, "ent_")


def find_ambiguous_merges(resolved: dict, threshold: float = 0.6) -> list[dict]:
    """Find entity pairs that might still be duplicates (for manual review).

    Uses simple Levenshtein distance on names. An LLM judge step can be
    added for production use.

    Args:
        resolved: Dict of resolved entities keyed by ID.
        threshold: Similarity threshold for flagging (lower = more sensitive).

    Returns:
        List of candidate pairs: [{entity1_id, entity2_id, similarity, reason}]
    """
    candidates = []
    ids = list(resolved.keys())

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            e1 = resolved[ids[i]]
            e2 = resolved[ids[j]]

            # Skip if same type of entity (all in same dict)
            name1 = e1.get("canonical_name", "")
            name2 = e2.get("canonical_name", "")

            if not name1 or not name2:
                continue

            # Simple Jaccard similarity on character bigrams
            sim = _bigram_similarity(name1, name2)
            if threshold <= sim < 1.0:
                candidates.append({
                    "entity1_id": ids[i],
                    "entity1_name": name1,
                    "entity2_id": ids[j],
                    "entity2_name": name2,
                    "similarity": round(sim, 3),
                    "reason": f"Name similarity: {sim:.2f}",
                })

    # Sort by similarity (highest first)
    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates


def _bigram_similarity(s1: str, s2: str) -> float:
    """Compute bigram character similarity between two strings."""
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0

    def bigrams(s):
        return set(s[i:i+2] for i in range(len(s) - 1)) if len(s) >= 2 else {s}

    b1 = bigrams(s1)
    b2 = bigrams(s2)

    if not b1 and not b2:
        return 1.0

    intersection = b1 & b2
    union = b1 | b2

    return len(intersection) / len(union) if union else 0.0
