"""Prompt templates and tool schema for Layer 2 chapter analysis.

Constructs the system prompt with rolling context and the user message
containing the current batch of chapters.
"""

import json
from ..models.chapter import BatchNarrativeSummary, BatchEntitySnapshot


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


def build_tool_schema() -> dict:
    """Build the tool-use schema for structured batch analysis output.

    All content keys are in Chinese since the novel being analyzed and the
    AI's internal reasoning are both in Chinese — this avoids a language
    mode-switch that degrades output quality.
    """
    return {
        "name": "provide_batch_analysis",
        "description": "提供本章节的完整结构化分析，包括每章摘要、实体更新、叙事总结和伏笔。",
        "input_schema": {
            "type": "object",
            "properties": {
                "叙事摘要": {
                    "type": "object",
                    "description": "本批次的压缩叙事摘要，将传递给后续批次作为滚动上下文",
                    "properties": {
                        "批次号": {"type": "integer"},
                        "章节范围": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "摘要": {
                            "type": "string",
                            "description": "200-400字的剧情摘要，概括本批次的主要情节发展",
                        },
                        "关键发展": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "3-5个本批次最重要的剧情发展，每个一句话",
                        },
                        "弧标记": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "检测到的故事弧过渡标记，如'换地图''大事件结束'等",
                        },
                    },
                    "required": ["批次号", "章节范围", "摘要", "关键发展", "弧标记"],
                },
                "章节列表": {
                    "type": "array",
                    "description": "本批次每章的详细分析",
                    "items": {
                        "type": "object",
                        "properties": {
                            "章节号": {"type": "integer"},
                            "标题": {"type": "string"},
                            "摘要": {
                                "type": "string",
                                "description": "100-200字的章节摘要",
                            },
                            "关键事件": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "类型": {
                                            "type": "string",
                                            "enum": ["登场", "退场", "战斗", "对话", "转折", "设定揭示", "修炼", "死亡", "日常", "其他"],
                                        },
                                        "描述": {"type": "string"},
                                        "涉及角色": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "重要程度": {
                                            "type": "string",
                                            "enum": ["主线关键", "次要", "过渡"],
                                        },
                                    },
                                    "required": ["类型", "描述"],
                                },
                            },
                            "视角角色": {"type": "string"},
                            "涉及地点": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "出场角色": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "揭示信息": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "本章揭示的新信息、新设定、反转",
                            },
                            "时间线": {"type": "string", "description": "故事时间标记，如'第3天'、'三个月后'"},
                            "情感基调": {
                                "type": "string",
                                "description": "本章整体氛围: 紧张/轻松/悲伤/热血/悬疑/诡异/温馨/绝望",
                            },
                            "剧情标签": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "本章内容分类: 战斗/修炼/日常/冒险/解谜/政治/感情/搞笑",
                            },
                        },
                        "required": ["章节号", "摘要", "关键事件"],
                    },
                },
                "实体更新": {
                    "type": "object",
                    "description": "本批次发现的新实体及对已知实体的更新",
                    "properties": {
                        "角色": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {
                                        "type": "string",
                                        "description": "稳定ID: char_拼音名（如 char_chenling）。若为已知实体则使用已有ID",
                                    },
                                    "名称": {"type": "string", "description": "角色的标准中文名"},
                                    "别名": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "该角色的其他称呼、外号、尊称",
                                    },
                                    "首次出场": {"type": "integer", "description": "该角色在本小说中第一次出现的章节号"},
                                    "最近出场": {"type": "integer", "description": "该角色目前最后一次出现的章节号"},
                                    "角色定位": {
                                        "type": "string",
                                        "enum": ["主角", "配角", "反派", "路人", "导师", "恋人", "家人", "其他"],
                                    },
                                    "所属势力": {"type": "string", "description": "该角色所属的势力名称"},
                                    "性格特征": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "能力提示": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "该角色展现的能力、功法的关键描述",
                                    },
                                    "状态": {
                                        "type": "string",
                                        "enum": ["活跃", "已死亡", "失踪", "未知"],
                                    },
                                    "关系网": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "对象": {"type": "string", "description": "关系对方的角色名（用实际名字，如'陈伶'）"},
                                                "关系": {"type": "string", "description": "关系类型: 师徒/敌对/血亲/朋友/恋人/同盟/上下级/利益"},
                                                "描述": {"type": "string", "description": "一句话描述这段关系的动态"},
                                            },
                                            "required": ["对象", "关系"],
                                        },
                                    },
                                    "是否新实体": {"type": "boolean", "description": "本批次首次出现的角色为true"},
                                    "是否更新": {"type": "boolean", "description": "对已存在角色的信息补充为true"},
                                },
                                "required": ["id", "名称", "角色定位", "是否新实体"],
                            },
                        },
                        "势力": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string", "description": "稳定ID: fact_拼音名"},
                                    "名称": {"type": "string"},
                                    "势力类型": {"type": "string", "description": "宗门/家族/国家/组织/商会/佣兵团/..."},
                                    "首领": {"type": "string", "description": "首领的中文名"},
                                    "成员": {"type": "array", "items": {"type": "string"}, "description": "已知成员的中文名"},
                                    "势力范围": {"type": "string", "description": "势力控制的地域"},
                                    "描述": {"type": "string"},
                                    "首次出场": {"type": "integer"},
                                    "是否新实体": {"type": "boolean"},
                                },
                                "required": ["id", "名称", "是否新实体"],
                            },
                        },
                        "地点": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string", "description": "稳定ID: loc_拼音名"},
                                    "名称": {"type": "string"},
                                    "地点类型": {"type": "string", "description": "城市/宗门/秘境/星球/建筑/国家/..."},
                                    "所属区域": {"type": "string", "description": "上级地点名称"},
                                    "描述": {"type": "string"},
                                    "重要性": {"type": "string", "description": "该地点在故事中的重要性"},
                                    "首次出场": {"type": "integer"},
                                    "是否新实体": {"type": "boolean"},
                                },
                                "required": ["id", "名称", "是否新实体"],
                            },
                        },
                        "功法能力": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string", "description": "稳定ID: pow_拼音名"},
                                    "名称": {"type": "string"},
                                    "类别": {"type": "string", "description": "境界体系/修炼功法/武技/法术/天赋/丹药/法宝/系统能力/..."},
                                    "使用者": {"type": "array", "items": {"type": "string"}, "description": "拥有此能力的角色名"},
                                    "等级体系": {"type": "array", "items": {"type": "string"}, "description": "如果有等级划分，如['筑基','金丹','元婴']"},
                                    "描述": {"type": "string"},
                                    "限制": {"type": "array", "items": {"type": "string"}, "description": "使用限制、代价、弱点"},
                                    "首次出场": {"type": "integer"},
                                    "是否新实体": {"type": "boolean"},
                                },
                                "required": ["id", "名称", "是否新实体"],
                            },
                        },
                    },
                },
                "伏笔": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "描述": {"type": "string", "description": "伏笔内容描述"},
                            "章节": {"type": "integer", "description": "埋下伏笔的章节号"},
                            "重要性": {"type": "string", "enum": ["主线", "支线"]},
                            "推测回收": {"type": "string", "description": "猜测这个伏笔后续会如何回收"},
                        },
                        "required": ["描述", "章节"],
                    },
                },
                "与前文矛盾": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "章节": {"type": "integer"},
                            "主题": {"type": "string"},
                            "描述": {"type": "string", "description": "矛盾的具体描述"},
                            "矛盾章节": {"type": "integer", "description": "与此矛盾的先前章节号"},
                        },
                        "required": ["描述"],
                    },
                },
            },
            "required": ["叙事摘要", "章节列表", "实体更新"],
        },
    }


def build_system_prompt(
    novel_metadata: dict,
    rolling_summary: str = "",
    entity_snapshot: str = "",
) -> str:
    """Build the system prompt for batch analysis.

    Args:
        novel_metadata: Dict with 'title', 'author', 'synopsis' keys.
        rolling_summary: Compressed narrative summary from prior batches.
        entity_snapshot: Markdown table of known entities.

    Returns:
        System prompt string.
    """
    title = novel_metadata.get("title", "未知小说")
    author = novel_metadata.get("author", "未知作者")
    synopsis = novel_metadata.get("synopsis", "")

    prompt = f"""你是一位专业的小说分析员。你的任务是仔细阅读一批小说章节，并提取结构化的分析信息。

## 小说信息
- 书名: 《{title}》
- 作者: {author}
- 简介: {synopsis}

## 任务说明
仔细阅读下面提供的小说章节原文，提取以下信息：
1. **每章摘要**: 100-200字的剧情概括
2. **关键事件**: 每章的关键事件，标注类型(登场/退场/战斗/对话/转折/设定揭示/修炼/死亡/日常/其他)
3. **实体更新**: 角色、势力、地点、功法能力的结构化信息，标注is_new和is_update
4. **叙事摘要**: 200-400字概括本批次整体剧情，用于传递给下一批次作为上下文
5. **伏笔**: 检测本批次埋下的伏笔
6. **矛盾**: 与之前已知信息是否有矛盾

## 重要指导原则

### 实体提取
- 每个新角色分配唯一的稳定ID: `char_拼音名` (如: char_chenling)
- 势力ID: `fact_拼音名`, 地点ID: `loc_拼音名`, 能力ID: `pow_拼音名`
- 如有别名/称号/昵称，务必列在aliases中
- 网文中同一角色可能有多个称呼: 本名、称号、外号、尊称等，请统一识别
- 仅为**新出现或状态有重大变化**的实体生成完整记录

### 叙事摘要要求
- narrative_summary.summary 必须简洁(200-400字)，只包含最重要的情节推进
- major_developments 列出3-5个最重要的剧情发展
- arc_markers 标注明显的剧情弧过渡(如换地图、大事件收尾、新篇章开启)

### 伏笔检测
- 注意作者刻意埋下的未解之谜
- 注意提到但未展开的设定
- 注意神秘角色的出场(未透露身份)

### 状态追踪（重要）
- 每个实体的"状态"字段必须准确: 活跃/已死亡/失踪/未知
- 如果本批次内某个角色死亡，必须将其状态设为"已死亡"
- 如果已知实体在之前批次中被标记为"已死亡"但本批次又出现，在"与前文矛盾"中标注
- 实体更新时，即使角色在其他方面没有变化，只要状态变了就要记录（设置"是否更新": true）

### 矛盾检测
- 如果角色在之前显示死亡但在此再次出现，标注矛盾
- 如果设定与之前描述不一致，标注矛盾
- 如果时间线出现明显混乱，标注矛盾
- 只在确实发现矛盾时才输出，不要编造
"""

    # Add rolling context if available
    if rolling_summary:
        prompt += f"""
## 前文剧情摘要 (滚动上下文)
{rolling_summary}
"""

    if entity_snapshot:
        prompt += f"""
## 已知实体汇总
{entity_snapshot}

使用以上已知实体的ID和名称。如果遇到已知实体，使用其已有ID，并设置is_new=false, is_update=true。
只创建真正的新实体(is_new=true)。
"""

    prompt += """
## 输出格式
使用 provide_batch_analysis 工具输出完整的结构化分析结果。
"""
    return prompt


def build_user_message(
    batch_id: int,
    chapters: list,
) -> str:
    """Build the user message containing the actual chapter text.

    Args:
        batch_id: Sequential batch number.
        chapters: List of RawChapter objects.

    Returns:
        User message string with chapter text.
    """
    parts = [f"## 批次 {batch_id}\n"]
    parts.append(f"共 {len(chapters)} 章。请分析以下章节原文：\n")

    for ch in chapters:
        if ch.is_afterword:
            parts.append(f"\n### 完本感言 / 后记\n")
        else:
            parts.append(f"\n### 第{ch.number}章 {ch.title}\n")
        parts.append(ch.content)
        parts.append("")

    parts.append("\n请使用 provide_batch_analysis 工具输出完整的结构化分析。")
    return "\n".join(parts)


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
