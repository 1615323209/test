"""Single-batch analysis via Anthropic API with structured tool-use output."""

import json
import asyncio
import hashlib
from typing import Optional

from novel_decomp.anthropic_client import AnthropicClient
from novel_decomp.models.chapter import BatchAnalysisOutput, BatchNarrativeSummary, BatchEntitySnapshot, CriticalEvent
from novel_decomp.layer2.prompt import build_system_prompt, build_user_message, build_tool_schema


async def analyze_batch(
    client: AnthropicClient,
    batch_id: int,
    chapters: list,
    novel_metadata: dict,
    rolling_summary: str = "",
    entity_snapshot: str = "",
    critical_events_log: list[dict] | None = None,
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
        critical_events_log: Accumulated critical events from all prior batches.
        model: Claude model override (defaults to client default).
        max_retries: Number of retry attempts.

    Returns:
        BatchAnalysisOutput with chapter summaries and entity updates.
    """
    system_prompt = build_system_prompt(
        novel_metadata=novel_metadata,
        rolling_summary=rolling_summary,
        entity_snapshot=entity_snapshot,
        critical_events_log=critical_events_log,
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
                max_tokens=65536,  # ponytail: 32K still truncates with rich entity schema
                temperature=0.2,
            )

            # Detect empty/short responses (DeepSeek quirk)
            ch_count = len(tool_output.get("章节列表", tool_output.get("chapters", [])))
            if ch_count == 0 and attempt < max_retries - 1:
                wait = 3 * (attempt + 1)
                print(f"  ⚠ Empty response (0 chapters), retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            if ch_count < len(chapters) * 0.3 and attempt < max_retries - 1:
                wait = 3 * (attempt + 1)
                print(f"  ⚠ Short response ({ch_count}/{len(chapters)} chapters), retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue

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
        "角色定位": "role_type", "性别": "gender", "年龄": "age_hint",
        "背景": "background",
        "口头禅": "catchphrases", "外貌特征": "appearance_traits",
        "所属势力": "faction", "势力归属": "faction_affiliations",
        "性格特征": "personality_traits", "性格演变": "personality_evolution",
        "能力提示": "power_hints", "能力演变": "power_evolution",
        "状态": "status", "关系网": "relationships",
        "是否新实体": "is_new", "是否更新": "is_update",
        # Relationship
        "对象": "target", "关系": "relation", "描述": "description",
        # Faction
        "势力类型": "faction_type", "首领": "leader", "成员": "members",
        "势力范围": "territory", "实力评估": "strength_hint", "目标": "goals",
        # Location
        "地点类型": "location_type", "所属区域": "parent_location",
        "重要性": "significance",
        # Power
        "类别": "power_category", "所属体系": "parent_system",
        "使用者": "users",
        "等级体系": "tiers", "阶详情": "tier_details",
        "来源": "source", "限制": "limitations",
        # Narrative summary
        "批次号": "batch_id", "章节范围": "chapter_range",
        "摘要": "summary", "关键发展": "major_developments",
        "弧标记": "arc_markers",
        # Foreshadowing
        "章节": "chapter", "推测回收": "speculated_payoff",
        # Contradiction
        "主题": "subject", "矛盾章节": "prior_chapter",
        # Critical events
        "关键事件": "critical_events", "章节号": "chapter_number",
        "事件类型": "event_type",
        # Status enum values
        "活跃": "active", "已死亡": "deceased", "失踪": "missing",
    }

    def _normalize_entity(entities: list) -> list:
        """Recursively convert Chinese keys in entity dicts to English.
        Silently drops non-dict entries (can happen from truncated JSON recovery)."""
        result = []
        for ent in entities:
            if not isinstance(ent, dict):
                continue  # skip noise
            normalized = {}
            for k, v in ent.items():
                new_k = _ENTITY_KEY_MAP.get(k, k)
                if isinstance(v, dict):
                    # Handle nested objects (e.g. 等级体系 with 阶详情)
                    inner = {}
                    for ik, iv in v.items():
                        ik_new = _ENTITY_KEY_MAP.get(ik, ik)
                        if isinstance(iv, list) and iv and isinstance(iv[0], dict):
                            inner[ik_new] = _normalize_entity(iv)
                        else:
                            inner[ik_new] = iv
                    # If the object has 阶名列表, extract it into tiers
                    if "阶名列表" in v:
                        normalized["tiers"] = v["阶名列表"]
                    elif "tier_names" in inner:
                        normalized["tiers"] = inner["tier_names"]
                    normalized[new_k] = inner
                elif isinstance(v, list) and v and isinstance(v[0], dict):
                    normalized[new_k] = _normalize_entity(v)
                elif isinstance(v, str) and v in _ENTITY_KEY_MAP:
                    normalized[new_k] = _ENTITY_KEY_MAP[v]
                else:
                    normalized[new_k] = v
            result.append(normalized)
        return result

    # Parse 叙事摘要 (narrative summary)
    ns_raw = tool_output.get("叙事摘要", tool_output.get("narrative_summary", {}))
    if not isinstance(ns_raw, dict):
        ns_raw = {}  # ponytail: corrupted JSON recovery

    chapter_range = tuple(ns_raw.get("章节范围", ns_raw.get("chapter_range", [
        chapters[0].number if chapters else 0,
        chapters[-1].number if chapters else 0,
    ])))

    # Parse 关键事件 (critical events) from narrative summary
    critical_events = []
    ce_list = ns_raw.get("关键事件", ns_raw.get("critical_events", []))
    if not isinstance(ce_list, list):
        ce_list = []
    for ce_raw in ce_list:
        if not isinstance(ce_raw, dict):
            continue
        ch_num = ce_raw.get("章节号", ce_raw.get("chapter_number", 0))
        etype = ce_raw.get("事件类型", ce_raw.get("event_type", "其他"))
        desc = _ensure_str(ce_raw.get("描述", ce_raw.get("description")))
        if not desc:
            continue
        # Build a stable event_id for dedup
        desc_hash = hashlib.md5(desc.encode()).hexdigest()[:6]
        etype_slug = etype[:8]
        event_id = f"ev_{ch_num}_{etype_slug}_{desc_hash}"
        critical_events.append(CriticalEvent(
            chapter_number=ch_num,
            description=desc,
            event_type=etype,
            event_id=event_id,
        ))

    narrative_summary = BatchNarrativeSummary(
        batch_id=batch_id,
        chapter_range=chapter_range,
        summary=_ensure_str(ns_raw.get("摘要", ns_raw.get("summary")), "未提供摘要"),
        major_developments=_ensure_list(ns_raw.get("关键发展", ns_raw.get("major_developments"))),
        arc_markers=_ensure_list(ns_raw.get("弧标记", ns_raw.get("arc_markers"))),
        critical_events=critical_events,
    )

    # Parse 章节列表 (chapters)
    chapter_summaries = []
    ch_list = tool_output.get("章节列表", tool_output.get("chapters", []))
    for ch_raw in ch_list:
        if not isinstance(ch_raw, dict):
            continue  # ponytail: skip noise from truncated JSON recovery
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
            character_relationships=ch_raw.get("人物关系", ch_raw.get("character_relationships", [])),
            foreshadowing_planted=ch_raw.get("本章伏笔", ch_raw.get("foreshadowing_planted", [])),
            foreshadowing_resolved=ch_raw.get("回收伏笔", ch_raw.get("foreshadowing_resolved", [])),
        ))

    # Parse 实体更新 (entity updates) — normalize Chinese sub-keys to English
    eu_raw = tool_output.get("实体更新", tool_output.get("entity_updates", {}))
    if not isinstance(eu_raw, dict):
        eu_raw = {}  # ponytail: corrupted JSON recovery produced wrong type
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
