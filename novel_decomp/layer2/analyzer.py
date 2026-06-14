"""Single-batch analysis via Anthropic API with structured tool-use output."""

import json
import asyncio
from typing import Optional

from novel_decomp.anthropic_client import AnthropicClient
from novel_decomp.models.chapter import BatchAnalysisOutput, BatchNarrativeSummary, BatchEntitySnapshot
from novel_decomp.layer2.prompt import build_system_prompt, build_user_message, build_tool_schema


async def analyze_batch(
    client: AnthropicClient,
    batch_id: int,
    chapters: list,
    novel_metadata: dict,
    rolling_summary: str = "",
    entity_snapshot: str = "",
    *,
    model: str = "",
    max_retries: int = 3,
) -> BatchAnalysisOutput:
    """Analyze a single batch of chapters and return structured output.

    Args:
        client: AnthropicClient instance.
        batch_id: Sequential batch number.
        chapters: List of RawChapter objects.
        novel_metadata: Dict with title, author, synopsis.
        rolling_summary: Compressed narrative summary from prior batches.
        entity_snapshot: Markdown entity snapshot table.
        model: Claude model override (defaults to client default).
        max_retries: Number of retry attempts.

    Returns:
        BatchAnalysisOutput with chapter summaries and entity updates.
    """
    system_prompt = build_system_prompt(
        novel_metadata=novel_metadata,
        rolling_summary=rolling_summary,
        entity_snapshot=entity_snapshot,
    )
    user_message = build_user_message(batch_id, chapters)
    tool_schema = build_tool_schema()

    last_error = None
    for attempt in range(max_retries):
        try:
            tool_output = await client.analyze_with_tool(
                system_prompt=system_prompt,
                user_message=user_message,
                tool_schema=tool_schema,
                model=model,
                layer=2,
                batch_id=batch_id,
                max_tokens=16384,
                temperature=0.3,
            )

            # Parse into BatchAnalysisOutput
            return _parse_batch_output(batch_id, chapters, tool_output)

        except json.JSONDecodeError as e:
            last_error = e
            wait = 2 ** attempt
            print(f"  ⚠ JSON parse error (attempt {attempt+1}/{max_retries}), "
                  f"retrying in {wait}s...")
            await asyncio.sleep(wait)

        except Exception as e:
            last_error = e
            wait = 2 ** attempt * 1.5
            print(f"  ⚠ Batch analysis error (attempt {attempt+1}/{max_retries}): {e}")
            await asyncio.sleep(wait)

    raise RuntimeError(
        f"Batch {batch_id} analysis failed after {max_retries} retries: {last_error}"
    )


def _parse_batch_output(
    batch_id: int,
    chapters: list,
    tool_output: dict,
) -> BatchAnalysisOutput:
    """Parse raw tool output dict into a validated BatchAnalysisOutput.

    Handles missing fields gracefully with defaults.
    """

    def _ensure_list(val):
        """Normalize value to list: string → [string], None → []."""
        if val is None:
            return []
        if isinstance(val, str):
            return [val]
        if isinstance(val, list):
            return val
        return [val] if val else []

    def _ensure_str(val, default=""):
        """Normalize value to string."""
        if val is None:
            return default
        if isinstance(val, str):
            return val
        if isinstance(val, (list, dict)):
            return default
        return str(val)

    def _map_significance(val: str) -> str:
        """Map Chinese significance enum → English for Pydantic model."""
        if val in ("主线关键", "major"):
            return "major"
        if val in ("次要", "minor"):
            return "minor"
        return "transitional"

    # Key mapping: Chinese (new schema) → English (internal field names)
    _ENTITY_KEY_MAP = {
        # Character
        "名称": "canonical_name", "别名": "aliases",
        "首次出场": "first_chapter", "最近出场": "last_chapter_seen",
        "角色定位": "role_type", "所属势力": "faction",
        "性格特征": "personality_traits", "能力提示": "power_hints",
        "状态": "status", "关系网": "relationships",
        "是否新实体": "is_new", "是否更新": "is_update",
        # Relationship
        "对象": "target", "关系": "relation", "描述": "description",
        # Faction
        "势力类型": "faction_type", "首领": "leader", "成员": "members",
        "势力范围": "territory",
        # Location
        "地点类型": "location_type", "所属区域": "parent_location",
        "重要性": "significance",
        # Power
        "类别": "power_category", "使用者": "users",
        "等级体系": "tiers", "限制": "limitations",
        # Narrative summary
        "批次号": "batch_id", "章节范围": "chapter_range",
        "摘要": "summary", "关键发展": "major_developments",
        "弧标记": "arc_markers",
        # Foreshadowing
        "章节": "chapter", "推测回收": "speculated_payoff",
        # Contradiction
        "主题": "subject", "矛盾章节": "prior_chapter",
        # Status enum values
        "活跃": "active", "已死亡": "deceased", "失踪": "missing",
    }

    def _normalize_entity(entities: list) -> list:
        """Recursively convert Chinese keys in entity dicts to English."""
        result = []
        for ent in entities:
            if not isinstance(ent, dict):
                result.append(ent)
                continue
            normalized = {}
            for k, v in ent.items():
                new_k = _ENTITY_KEY_MAP.get(k, k)
                # Recursively normalize nested lists of dicts (e.g. 关系网)
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    normalized[new_k] = _normalize_entity(v)
                elif isinstance(v, str) and v in _ENTITY_KEY_MAP:
                    normalized[new_k] = _ENTITY_KEY_MAP[v]  # Map enum values
                else:
                    normalized[new_k] = v
            result.append(normalized)
        return result

    # Parse 叙事摘要 (narrative summary)
    ns_raw = tool_output.get("叙事摘要", tool_output.get("narrative_summary", {}))
    if not ns_raw:
        ns_raw = {}

    chapter_range = tuple(ns_raw.get("章节范围", ns_raw.get("chapter_range", [
        chapters[0].number if chapters else 0,
        chapters[-1].number if chapters else 0,
    ])))

    narrative_summary = BatchNarrativeSummary(
        batch_id=batch_id,
        chapter_range=chapter_range,
        summary=_ensure_str(ns_raw.get("摘要", ns_raw.get("summary")), "未提供摘要"),
        major_developments=_ensure_list(ns_raw.get("关键发展", ns_raw.get("major_developments"))),
        arc_markers=_ensure_list(ns_raw.get("弧标记", ns_raw.get("arc_markers"))),
    )

    # Parse 章节列表 (chapters)
    chapter_summaries = []
    ch_list = tool_output.get("章节列表", tool_output.get("chapters", []))
    for ch_raw in ch_list:
        from novel_decomp.models.chapter import ChapterSummary, KeyEvent

        # Parse 关键事件 (key events)
        events = []
        ev_list = ch_raw.get("关键事件", ch_raw.get("key_events", []))
        if not isinstance(ev_list, list):
            ev_list = []
        for ev_raw in ev_list:
            if not isinstance(ev_raw, dict):
                continue
            events.append(KeyEvent(
                type=_ensure_str(ev_raw.get("类型", ev_raw.get("type")), "其他"),
                description=_ensure_str(ev_raw.get("描述", ev_raw.get("description"))),
                characters_involved=_ensure_list(ev_raw.get("涉及角色", ev_raw.get("characters_involved"))),
                significance=_map_significance(ev_raw.get("重要程度", ev_raw.get("significance", "transitional"))),
            ))

        chapter_summaries.append(ChapterSummary(
            chapter_number=ch_raw.get("章节号", ch_raw.get("chapter_number", 0)),
            title=_ensure_str(ch_raw.get("标题", ch_raw.get("title"))),
            summary=_ensure_str(ch_raw.get("摘要", ch_raw.get("summary"))),
            key_events=events,
            pov_character=_ensure_str(ch_raw.get("视角角色", ch_raw.get("pov_character"))),
            locations_visited=_ensure_list(ch_raw.get("涉及地点", ch_raw.get("locations_visited"))),
            characters_appeared=_ensure_list(ch_raw.get("出场角色", ch_raw.get("characters_appeared"))),
            reveals=_ensure_list(ch_raw.get("揭示信息", ch_raw.get("reveals"))),
            timeline_hint=_ensure_str(ch_raw.get("时间线", ch_raw.get("timeline_hint"))),
            emotional_tone=_ensure_str(ch_raw.get("情感基调", ch_raw.get("emotional_tone"))),
            plot_tags=_ensure_list(ch_raw.get("剧情标签", ch_raw.get("plot_tags"))),
        ))

    # Parse 实体更新 (entity updates) — normalize Chinese sub-keys to English
    eu_raw = tool_output.get("实体更新", tool_output.get("entity_updates", {}))
    entity_updates = BatchEntitySnapshot(
        characters=_normalize_entity(eu_raw.get("角色", eu_raw.get("characters", []))),
        factions=_normalize_entity(eu_raw.get("势力", eu_raw.get("factions", []))),
        locations=_normalize_entity(eu_raw.get("地点", eu_raw.get("locations", []))),
        powers=_normalize_entity(eu_raw.get("功法能力", eu_raw.get("powers", []))),
        unresolved_foreshadowing=_normalize_entity(tool_output.get("伏笔", tool_output.get("foreshadowing", []))),
        total_entity_count=(
            len(eu_raw.get("角色", eu_raw.get("characters", [])))
            + len(eu_raw.get("势力", eu_raw.get("factions", [])))
            + len(eu_raw.get("地点", eu_raw.get("locations", [])))
            + len(eu_raw.get("功法能力", eu_raw.get("powers", [])))
        ),
    )

    # Calculate metadata
    batch_char_count = sum(ch.char_count if hasattr(ch, 'char_count') else len(ch.content) for ch in chapters)
    batch_est_tokens = sum(ch.estimated_tokens if hasattr(ch, 'estimated_tokens') else int(len(ch.content) * 0.6) for ch in chapters)

    return BatchAnalysisOutput(
        batch_id=batch_id,
        chapter_range=chapter_range,
        narrative_summary=narrative_summary,
        chapters=chapter_summaries,
        entity_updates=entity_updates,
        foreshadowing=_normalize_entity(tool_output.get("伏笔", tool_output.get("foreshadowing", []))),
        contradictions_with_prior=_normalize_entity(tool_output.get("与前文矛盾", tool_output.get("contradictions_with_prior", []))),
        batch_char_count=batch_char_count,
        batch_estimated_tokens=batch_est_tokens,
    )
