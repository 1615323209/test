"""AI-powered data rewriting — polishes and enriches analysis data.
Prompts are loaded from external files in prompts/rewrite_*.md.
"""

import json
import asyncio
from pathlib import Path
from typing import Optional

from novel_decomp.config import (
    DEFAULT_MODEL, OUTPUT_DIR, PROMPTS_DIR, create_client,
)


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"rewrite_{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


async def rewrite_sections(
    analysis_dir: str | Path = "",
    output_dir: str | Path = "",
    *,
    model: str = "",
    sections: list[str] | None = None,
    custom_world: str = "",
) -> dict[str, Path]:
    analysis_dir = Path(analysis_dir) if analysis_dir else OUTPUT_DIR
    out_dir = Path(output_dir) if output_dir else analysis_dir / "rewrite"
    out_dir.mkdir(parents=True, exist_ok=True)

    if sections is None:
        sections = ["world", "characters", "outline"]

    l4_path = analysis_dir / "layer4_synthesis.json"
    if not l4_path.exists():
        raise FileNotFoundError(f"Analysis data not found: {l4_path}")
    l4 = json.loads(l4_path.read_text(encoding="utf-8"))

    l2_dir = analysis_dir / "layer2"
    chapters = _load_chapters(l2_dir)

    client = create_client(model=model or DEFAULT_MODEL)
    results = {}
    world_bg = custom_world or ""
    world_parts = []

    # Phase 1: World
    if l4.get("faction_profiles") or l4.get("power_profiles") or l4.get("location_profiles"):
        label = "重建世界观" if not world_bg else "基于你的世界观生成势力、体系、地点"
        print(f"  [1/3] {label}...")

    if l4.get("faction_profiles"):
        print("    势力...")
        path, t = await _rewrite_factions(client, l4["faction_profiles"], out_dir, world_bg)
        results["factions"] = path
        world_parts.append(t)

    if l4.get("power_profiles"):
        print("    修炼体系...")
        path, t = await _rewrite_powers(client, l4["power_profiles"], out_dir, world_bg)
        results["powers"] = path
        world_parts.append(t)

    if l4.get("location_profiles"):
        print("    地点...")
        path, t = await _rewrite_locations(client, l4["location_profiles"], out_dir, world_bg)
        results["locations"] = path
        world_parts.append(t)

    world_details = "\n\n".join(world_parts)
    world_context = world_bg
    if world_details:
        world_context = world_bg + "\n\n## 基于背景生成的世界详情\n\n" + world_details if world_bg else world_details

    # Phase 2: Characters and outline
    if l4.get("character_profiles") or chapters:
        label = "基于你的世界观重建角色和剧情" if custom_world else "在新世界观下重建角色和剧情"
        print(f"  [2/3] {label}...")
        char_roster = ""
        if l4.get("character_profiles"):
            print("    角色...")
            path, char_roster = await _rewrite_characters(client, l4["character_profiles"], out_dir, world_context)
            results["characters"] = path

        if chapters:
            print("    细纲...")
            path = await _rewrite_outline(client, chapters, l4, out_dir, world_context, char_roster)
            results["outline"] = path

    usage = client.usage_summary
    print(f"\n  改写完成 ({len(results)} 个文件): {out_dir}")
    print(f"  API: {usage['calls']} calls, {usage['total_tokens']:,} tokens, ${usage['estimated_cost_usd']:.2f}")
    return results


# ── Characters ──

async def _rewrite_characters(client, characters, out_dir, world_context="", prev_chars="") -> tuple:
    rewritten = []
    roster = []
    total = len(characters)
    for i, c in enumerate(characters):
        name = c.get("name", "")
        print(f"    [{i+1}/{total}] {name}")
        evo = c.get("evolution", {})
        personality_stages = evo.get("personality_stages", [])
        power_stages = evo.get("power_stages", [])
        faction_changes = evo.get("faction_changes", [])

        prev_ctx = ""
        if roster:
            prev_ctx = "已生成的角色（新角色必须与他们有关联）:\n" + "\n".join(f"- {r}" for r in roster) + "\n\n"

        source = f"""{prev_ctx}角色: {name}
性别: {c.get('gender', '未知')}
年龄: {c.get('age_hint', '未知')}
角色定位: {c.get('role', '配角')}
当前性格: {', '.join(c.get('personality', []))}
外貌标志: {', '.join(_joinable(c.get('appearance_traits', [])))}
口头禅: {', '.join(c.get('catchphrases', []))}
所属势力: {', '.join(_joinable(c.get('faction_affiliations', [])))}
能力: {', '.join(_joinable(c.get('powers', [])))}
描述: {c.get('description', '')}
背景: {c.get('background', '')}"""

        if personality_stages:
            stages = "\n".join(
                f"  - 第{s['chapter']}章: {', '.join(s.get('traits', []))}"
                + (f" (触发: {s.get('trigger', '')})" if s.get('trigger') else "")
                for s in personality_stages
            )
            source += f"\n性格演变:\n{stages}"

        if power_stages:
            stages = "\n".join(
                f"  - 第{s['chapter']}章新增: {', '.join(s.get('new', []))}"
                + (f" (触发: {s.get('trigger', '')})" if s.get('trigger') else "")
                for s in power_stages
            )
            source += f"\n能力演进:\n{stages}"

        if faction_changes:
            changes = "\n".join(
                f"  - 第{fc['chapter']}章: {fc['faction']}"
                + (f" ({fc.get('nature', '')})" if fc.get('nature') else "")
                + (f" — {fc.get('note', '')}" if fc.get('note') else "")
                for fc in faction_changes
            )
            source += f"\n势力变更:\n{changes}"

        prompt = _load_prompt("characters").format(source=source, world=world_context or "（使用原世界观）")

        try:
            response = await client.analyze(
                system_prompt="你是小说设定创作专家。根据角色结构创作全新角色来替换。全部替换名字、性别、外貌、背景。保持角色功能和输出格式。只输出新数据。",
                user_message=prompt, max_tokens=1024, temperature=0.7, layer=99, batch_id=i,
            )
            text = _extract_text(response)
            rewritten.append(f"## {name}\n\n{text.strip()}\n\n---\n")
            first_line = text.strip().split("\n")[0] if text else name
            roster.append(f"{first_line}")
        except Exception as e:
            rewritten.append(f"## {name}\n\n*(改写失败: {e})*\n\n---\n")
            roster.append(f"{name}（改写失败）")

    out_path = out_dir / "rewrite_角色档案.md"
    out_path.write_text("\n".join(rewritten), encoding="utf-8")
    return out_path, "\n".join(roster)


# ── Outline ──

async def _rewrite_outline(client, chapters, l4, out_dir, world_context="", char_roster="") -> Path:
    batch_size = 15
    parts = []
    total = len(chapters)
    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        batch = chapters[batch_start:batch_end]
        first = batch[0]["number"]
        last = batch[-1]["number"]
        print(f"    第{first}-{last}章 ({len(batch)}章)...")

        ch_lines = []
        for ch in batch:
            events = "\n".join(
                f"    - [{ev['type']}] {ev['description']}"
                for ev in ch.get("key_events", [])
            )
            chars = _extract_names(ch.get("characters", []))
            foreshadowing = ch.get("foreshadowing_planted", [])
            resolved = ch.get("foreshadowing_resolved", [])
            extra = ""
            if foreshadowing:
                extra += "\n    伏笔: " + "; ".join(_fs(f) for f in foreshadowing[:2])
            if resolved:
                extra += "\n    回收: " + "; ".join(_fs(f) for f in resolved[:2])

            ch_lines.append(
                f"第{ch['number']}章 {ch.get('title', '')}\n"
                f"  摘要: {ch.get('summary', '')}\n"
                f"  出场: {', '.join(chars[:6])}\n"
                f"  事件:\n{events}{extra}"
            )

        prompt = _load_prompt("outline").format(
            chapters="\n".join(ch_lines),
            world=world_context or "（使用原世界观）",
            characters=char_roster or "（使用原角色名）",
        )

        try:
            response = await client.analyze(
                system_prompt="你是小说创作专家。根据细纲结构创作全新剧情来替换。全部替换事件、场景、角色。保持节奏和伏笔结构。只输出新细纲。",
                user_message=prompt, max_tokens=4096, temperature=0.7, layer=99, batch_id=batch_start,
            )
            parts.append(_extract_text(response).strip())
        except Exception as e:
            parts.append(f"*(第{first}-{last}章改写失败: {e})*")

    out_path = out_dir / "rewrite_章节细纲.md"
    out_path.write_text("\n\n".join(parts), encoding="utf-8")
    return out_path


# ── Factions ──

async def _rewrite_factions(client, factions, out_dir, world_context="") -> tuple:
    parts = []
    prev_list = []
    for f in factions:
        name = f.get("name", "")
        print(f"    {name}")
        goals = f.get("goals", [])
        goals_text = "\n".join(
            f"  - 第{g.get('确定章节', g.get('chapter', '?'))}章确立: {g.get('描述', str(g))}"
            + (f"（{g.get('状态', '')}）" if isinstance(g, dict) and g.get('状态') else "")
            for g in goals
        )

        prev_ctx = ""
        if prev_list:
            prev_ctx = "已生成的势力（必须与它们建立关系）:\n" + "\n".join(prev_list) + "\n\n"

        source = f"""{prev_ctx}势力: {name}
类型: {f.get('type', '组织')}
首领: {f.get('leader', '未知')}
成员数: {f.get('member_count', 0)}
势力范围: {f.get('territory', '')}
实力: {f.get('strength_hint', '')}
介绍: {f.get('description', '')}
目标:
{goals_text}"""

        prompt = _load_prompt("factions").format(source=source, world=world_context or "（参考原设定）")

        try:
            response = await client.analyze(
                system_prompt="你是世界观创作专家。根据势力结构创作全新势力来替换。全部替换名称、类型、首领、目标。保持势力功能定位。只输出新数据。",
                user_message=prompt, max_tokens=1024, temperature=1.0, layer=99,
            )
            text = _extract_text(response)
            parts.append(f"## {name}\n\n{text.strip()}\n\n---\n")
            prev_list.append(f"- {name}: {text.strip()[:120]}")
        except Exception as e:
            parts.append(f"## {name}\n\n*(改写失败: {e})*\n\n---\n")

    out_path = out_dir / "rewrite_势力格局.md"
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path, "\n".join(parts)


# ── Powers ──

async def _rewrite_powers(client, powers, out_dir, world_context="") -> tuple:
    systems = [p for p in powers if p.get("category") == "体系" and not p.get("parent_system")]
    children = {}
    for p in powers:
        parent = p.get("parent_system", "")
        if parent:
            children.setdefault(parent, []).append(p)

    source_parts = []
    for sys in systems:
        src = f"### {sys['name']} ({sys.get('category', '体系')})\n描述: {sys.get('description', '')}\n来源: {sys.get('source', '')}"
        if sys.get("tiers"):
            src += f"\n等级: {' → '.join(sys['tiers'])}"
        if sys.get("tier_details"):
            for td in sys["tier_details"]:
                src += f"\n  阶{td.get('level', td.get('阶数', '?'))} {td.get('name', td.get('阶名', '?'))}"
                abs_list = td.get("abilities") or td.get("获得能力", [])
                reps = td.get("representatives") or td.get("代表人物", [])
                if abs_list:
                    src += f" — 能力: {', '.join(abs_list[:5])}"
                if reps:
                    src += f" — 代表: {', '.join(reps[:3])}"
        for kid in children.get(sys["name"], []):
            src += f"\n  ### {kid['name']} ({kid.get('category', '')})"
            if kid.get("tiers"):
                src += f"\n  等级: {' → '.join(kid['tiers'][:9])}"
            if kid.get("tier_details"):
                for td in kid["tier_details"]:
                    src += f"\n    阶{td.get('level', td.get('阶数', '?'))} {td.get('name', td.get('阶名', '?'))}"
            if kid.get("description"):
                src += f"\n  {kid['description'][:120]}"
        source_parts.append(src)

    source = "\n".join(source_parts)
    prompt = _load_prompt("powers").format(source=source, world=world_context or "（参考原设定）")

    response = await client.analyze(
        system_prompt="你是修炼体系创作专家。根据体系结构创作全新体系来替换。全部替换名称、道途、阶名、能力。保持层级阶数。只输出新数据。",
        user_message=prompt, max_tokens=4096, temperature=1.0, layer=99,
    )
    text = _extract_text(response)
    out_path = out_dir / "rewrite_修炼体系.md"
    out_path.write_text(f"# 修炼体系\n\n{text.strip()}\n", encoding="utf-8")
    return out_path, f"# 修炼体系\n\n{text.strip()}"


# ── Locations ──

async def _rewrite_locations(client, locations, out_dir, world_context="") -> tuple:
    parts = []
    prev_list = []
    for loc in locations:
        name = loc.get("name", "")
        print(f"    {name}")

        prev_ctx = ""
        if prev_list:
            prev_ctx = "已生成的地点（新地点必须与它们衔接）:\n" + "\n".join(prev_list) + "\n\n"

        source = f"""{prev_ctx}地点: {name}
类型: {loc.get('type', '')}
上级: {loc.get('parent', '—')}
重要性: {loc.get('significance', '')}
描述: {loc.get('description', '')}
出场次数: {loc.get('chapter_count', 0)}"""

        prompt = _load_prompt("locations").format(source=source, world=world_context or "（参考原设定）")

        try:
            response = await client.analyze(
                system_prompt="你是世界观创作专家。根据地点结构创作全新地点来替换。全部替换名称、描述、关联。保持层级。只输出新数据。",
                user_message=prompt, max_tokens=512, temperature=1.0, layer=99,
            )
            text = _extract_text(response)
            parts.append(f"## {name}\n\n{text.strip()}\n\n---\n")
            prev_list.append(f"- {name}: {text.strip()[:120]}")
        except Exception as e:
            parts.append(f"## {name}\n\n*(改写失败: {e})*\n\n---\n")

    out_path = out_dir / "rewrite_地理图志.md"
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path, "\n".join(parts)


# ── Helpers ──

def _extract_text(response) -> str:
    if hasattr(response, 'choices'):
        return response.choices[0].message.content or ""
    elif hasattr(response, 'content'):
        blocks = [b.text for b in response.content if hasattr(b, 'text')]
        return "\n".join(blocks)
    return str(response)


def _joinable(items) -> list:
    result = []
    for it in (items or []):
        if isinstance(it, str):
            result.append(it)
        elif isinstance(it, dict):
            result.append(it.get("trait", it.get("特征", str(it))))
        else:
            result.append(str(it))
    return result


def _fs(item) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get('描述', item.get('description', str(item)))
    return str(item)


def _extract_names(chars) -> list:
    names = []
    for c in (chars or []):
        if isinstance(c, str):
            names.append(c)
        elif isinstance(c, dict):
            names.append(c.get("名称", c.get("name", str(c))))
    return names


def _load_chapters(l2_dir: Path) -> list:
    chapters = []
    if not l2_dir.exists():
        return chapters
    for bf in sorted(l2_dir.glob("batch_*.json")):
        try:
            batch = json.loads(bf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ch in batch.get("chapters", []):
            chapters.append({
                "number": ch.get("chapter_number", 0),
                "title": ch.get("title", ""),
                "summary": ch.get("summary", ""),
                "key_events": [
                    {"type": ev.get("type", ev.get("类型", "")),
                     "description": ev.get("description", ev.get("描述", ""))}
                    for ev in ch.get("key_events", [])
                ],
                "characters": ch.get("characters_appeared", []),
                "character_relationships": ch.get("character_relationships", ch.get("人物关系", [])),
                "foreshadowing_planted": ch.get("foreshadowing_planted", ch.get("本章伏笔", [])),
                "foreshadowing_resolved": ch.get("foreshadowing_resolved", ch.get("回收伏笔", [])),
                "locations": ch.get("locations_visited", []),
            })
    chapters.sort(key=lambda c: c["number"])
    return chapters
