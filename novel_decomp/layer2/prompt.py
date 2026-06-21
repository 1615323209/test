"""Prompt templates and tool schema for Layer 2 chapter analysis.

Loads prompt templates from external files in the prompts/ directory,
making them easy to edit without touching code.
"""

import json
from pathlib import Path

from ..config import PROMPTS_DIR
from ..models.chapter import BatchNarrativeSummary, BatchEntitySnapshot


# ── Cache for loaded templates ──
_cache: dict[str, str] = {}


def _load_text(filename: str) -> str:
    """Load a text file from the prompts directory, with in-memory cache.

    Args:
        filename: File name relative to PROMPTS_DIR (e.g. 'system_prompt.md').

    Returns:
        File contents as a UTF-8 string.
    """
    if filename not in _cache:
        path = PROMPTS_DIR / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {path}\n"
                f"Expected prompt templates in: {PROMPTS_DIR}"
            )
        _cache[filename] = path.read_text(encoding="utf-8")
    return _cache[filename]


def _load_json(filename: str) -> dict:
    """Load a JSON file from the prompts directory, with in-memory cache.

    Args:
        filename: File name relative to PROMPTS_DIR (e.g. 'tool_schema.json').

    Returns:
        Parsed JSON as a dict.
    """
    # Use a separate cache key prefix to avoid collision with text files
    cache_key = f"__json__{filename}"
    if cache_key not in _cache:
        path = PROMPTS_DIR / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {path}\n"
                f"Expected prompt templates in: {PROMPTS_DIR}"
            )
        _cache[cache_key] = json.loads(path.read_text(encoding="utf-8"))
    return _cache[cache_key]


def _safe_id(val) -> str:
    """Ensure an entity ID is always a hashable string.

    LLMs sometimes return id as a dict or other non-hashable type.
    """
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return str(val.get("id", val.get("name", str(hash(frozenset(val.items()))))))
    if val is None:
        return ""
    return str(val)


# ═══════════════════════════════════════════════════════════════
# Public API — same signatures as before
# ═══════════════════════════════════════════════════════════════

def build_tool_schema() -> dict:
    """Build the tool-use schema for structured batch analysis output.

    Loads the JSON Schema from prompts/tool_schema.json.
    """
    return _load_json("tool_schema.json")


def build_system_prompt(
    novel_metadata: dict,
    rolling_summary: str = "",
    entity_snapshot: str = "",
    critical_events_log: list[dict] | None = None,
) -> str:
    """Build the system prompt for batch analysis.

    Loads the template from prompts/system_prompt.md and fills in
    placeholders with the provided metadata and context.

    Args:
        novel_metadata: Dict with 'title', 'author', 'synopsis' keys.
        rolling_summary: Compressed narrative summary from prior batches.
        entity_snapshot: Markdown table of known entities.
        critical_events_log: Accumulated critical events from all prior batches,
            displayed as a persistent reference table.

    Returns:
        System prompt string.
    """
    template = _load_text("system_prompt.md")

    title = novel_metadata.get("title", "未知小说")
    author = novel_metadata.get("author", "未知作者")
    synopsis = novel_metadata.get("synopsis", "")

    # Build conditional sections in code (logic belongs here, not in template)
    if rolling_summary:
        rolling_context_section = (
            "## 前文剧情摘要 (滚动上下文)\n"
            f"{rolling_summary}\n"
        )
    else:
        rolling_context_section = ""

    if entity_snapshot:
        entity_snapshot_section = (
            "## 已知实体汇总\n"
            f"{entity_snapshot}\n\n"
            "使用以上已知实体的ID和名称。如果遇到已知实体，使用其已有ID，"
            "并设置is_new=false, is_update=true。\n"
            "只创建真正的新实体(is_new=true)。\n"
        )
    else:
        entity_snapshot_section = ""

    # Build critical events log section
    if critical_events_log:
        table_rows = []
        for ev in critical_events_log:
            ch = ev.get("chapter_number", "?")
            etype = ev.get("event_type", "?")
            desc = ev.get("description", "?")
            table_rows.append(f"| {ch} | {etype} | {desc} |")
        critical_events_section = (
            '## 关键事件日志（历史累计）\n\n'
            '以下为截至目前整个故事中已记录的所有不可逆关键事件。'
            '请逐条对比当前批次内容：\n'
            '- 如果当前批次有角色的状态与日志中的事件明显矛盾（如已死亡角色复活但未解释），'
            '务必在「与前文矛盾」中标注\n'
            '- 如果当前批次发生了新的不可逆事件（死亡/复活/毁灭/身份揭露/不可逆伤害等），'
            '务必在「叙事摘要.关键事件」中添加\n\n'
            '| 章节 | 类型 | 描述 |\n'
            '|---|---|---|\n'
            + '\n'.join(table_rows) +
            '\n\n'
        )
    else:
        critical_events_section = ""

    return template.format(
        title=title,
        author=author,
        synopsis=synopsis,
        rolling_context_section=rolling_context_section,
        entity_snapshot_section=entity_snapshot_section,
        critical_events_section=critical_events_section,
    )


def build_user_message(
    batch_id: int,
    chapters: list,
) -> str:
    """Build the user message containing the actual chapter text.

    Loads the template from prompts/user_message.md and fills in
    the batch info and chapter content.

    Args:
        batch_id: Sequential batch number.
        chapters: List of RawChapter objects.

    Returns:
        User message string with chapter text.
    """
    template = _load_text("user_message.md")

    # Build the chapter list (this is data assembly, not prompt logic)
    parts = []
    for ch in chapters:
        if ch.is_afterword:
            parts.append("\n### 完本感言 / 后记\n")
        else:
            parts.append(f"\n### 第{ch.number}章 {ch.title}\n")
        parts.append(ch.content)
        parts.append("")

    chapter_list = "\n".join(parts)

    return template.format(
        batch_id=batch_id,
        chapter_count=len(chapters),
        chapter_list=chapter_list,
    )


# ═══════════════════════════════════════════════════════════════
# Entity snapshot helpers (runtime logic — not prompts)
# ═══════════════════════════════════════════════════════════════

def build_entity_snapshot_markdown(
    snapshot: dict,
    max_per_type: int = 80,
) -> str:
    """Convert entity snapshot dict to a compact Markdown table for prompt.

    Args:
        snapshot: Entity snapshot with characters/factions/locations/powers lists.
        max_per_type: Maximum entities per type to include (keep prompt small).

    Returns:
        Markdown table string.
    """
    lines = []

    # Characters
    characters = snapshot.get("characters", [])
    if characters:
        lines.append("### 角色")
        lines.append("| ID | 名称 | 别名 | 状态 | 最后章 | 角色 |")
        lines.append("|---|---|---|---|---|---|")
        for c in characters[:max_per_type]:
            aliases_raw = c.get("aliases", [])
            if isinstance(aliases_raw, str):
                aliases_raw = [aliases_raw]
            aliases = ", ".join((aliases_raw or [])[:3])
            lines.append(
                f"| {c.get('id', '?')} | {c.get('canonical_name', '?')} | "
                f"{aliases} | {c.get('status', 'active')} | "
                f"{c.get('last_chapter_seen', '?')} | {c.get('role_type', '?')} |"
            )
        if len(characters) > max_per_type:
            lines.append(f"| ... | (共{len(characters)}个角色) | ... | ... | ... | ... |")
        lines.append("")

    # Factions
    factions = snapshot.get("factions", [])
    if factions:
        lines.append("### 势力")
        lines.append("| ID | 名称 | 类型 | 首领 |")
        lines.append("|---|---|---|---|")
        for f in factions[:max_per_type]:
            lines.append(
                f"| {f.get('id', '?')} | {f.get('canonical_name', '?')} | "
                f"{f.get('faction_type', '?')} | {f.get('leader', '?')} |"
            )
        if len(factions) > max_per_type:
            lines.append(f"| ... | (共{len(factions)}个势力) | ... | ... |")
        lines.append("")

    # Locations
    locations = snapshot.get("locations", [])
    if locations:
        lines.append("### 地点")
        lines.append("| ID | 名称 | 类型 |")
        lines.append("|---|---|---|")
        for loc in locations[:max_per_type]:
            lines.append(f"| {loc.get('id', '?')} | {loc.get('canonical_name', '?')} | {loc.get('location_type', '?')} |")
        if len(locations) > max_per_type:
            lines.append(f"| ... | (共{len(locations)}个地点) | ... |")
        lines.append("")

    # Powers
    powers = snapshot.get("powers", [])
    if powers:
        lines.append("### 功法/能力")
        lines.append("| ID | 名称 | 类别 | 使用者 |")
        lines.append("|---|---|---|---|")
        for p in powers[:max_per_type]:
            users = ", ".join(p.get("users", p.get("user_names", []))[:3])
            lines.append(f"| {p.get('id', '?')} | {p.get('canonical_name', '?')} | {p.get('power_category', '?')} | {users} |")
        if len(powers) > max_per_type:
            lines.append(f"| ... | (共{len(powers)}个能力) | ... | ... |")
    lines.append("")

    # Unresolved foreshadowing
    foreshadowing = snapshot.get("unresolved_foreshadowing", [])
    if foreshadowing:
        lines.append("### 未回收的伏笔")
        for f in foreshadowing[:15]:
            lines.append(f"- [ch{f.get('chapter', '?')}] {f.get('description', '?')}")

    return "\n".join(lines)


def merge_entity_snapshot(
    current: dict,
    batch_entities: dict,
) -> dict:
    """Merge a batch's entity updates into the rolling entity snapshot.

    Simple key-based merge: entities with the same id are updated;
    new entities are appended.

    Args:
        current: Current entity snapshot dict.
        batch_entities: New entity updates from batch analysis.

    Returns:
        Updated entity snapshot dict.
    """
    if not current:
        current = {"characters": [], "factions": [], "locations": [], "powers": [],
                    "unresolved_foreshadowing": []}

    for entity_type in ["characters", "factions", "locations", "powers"]:
        if entity_type not in batch_entities:
            continue

        # Build existing_ids set: ensure all IDs are hashable strings
        existing_ids = set()
        for e in current.get(entity_type, []):
            eid = _safe_id(e.get("id", ""))
            existing_ids.add(eid)

        for entity in batch_entities.get(entity_type, []):
            eid = _safe_id(entity.get("id", ""))
            if not eid:
                continue  # Skip entities without valid ID
            if eid in existing_ids:
                # Update existing entity
                for i, e in enumerate(current[entity_type]):
                    if _safe_id(e.get("id", "")) == eid:
                        # Merge: keep existing fields, update changed ones
                        for key in entity:
                            if key in ("is_new", "is_update"):
                                continue
                            val = entity[key]
                            if val is not None and val != "" and not isinstance(val, dict):
                                # For lists of hashable items, extend
                                if isinstance(val, list) and key in e:
                                    try:
                                        e[key] = list(set(e[key] + val))
                                    except TypeError:
                                        e[key] = e[key] + val  # Fallback: just append
                                else:
                                    e[key] = val
                        break
            else:
                # New entity
                entity["id"] = eid  # Normalize ID to string
                current[entity_type].append(entity)
                existing_ids.add(eid)

    # Foreshadowing accumulates
    new_foreshadowing = batch_entities.get("foreshadowing", batch_entities.get("unresolved_foreshadowing", []))
    if new_foreshadowing:
        current["unresolved_foreshadowing"] = current.get("unresolved_foreshadowing", []) + new_foreshadowing

    return current
