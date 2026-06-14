"""Generate human-readable Markdown reports from analysis data."""

import json
from pathlib import Path
from datetime import datetime


def export_all(
    data: dict,
    output_dir: str | Path,
    *,
    novel_title: str = "未知小说",
    author: str = "未知作者",
) -> list[Path]:
    """Export all analysis results as Markdown files.

    Args:
        data: Complete analysis data dict containing:
            - outline (from Layer 4)
            - character_profiles
            - faction_profiles
            - location_profiles
            - power_profiles
            - plot_arcs
            - retcons
            - gaps
            - stats
        output_dir: Directory to write markdown files.
        novel_title: Novel title for headers.
        author: Author name.

    Returns:
        List of written file paths.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    written = []

    # 1. Main report (outline + overview)
    main_path = output_dir / "00_全书概览.md"
    main_path.write_text(_render_main_report(data, novel_title, author, timestamp),
                         encoding="utf-8")
    written.append(main_path)

    # 2. Character profiles
    chars = data.get("character_profiles", [])
    if chars:
        char_path = output_dir / "01_角色大全.md"
        char_path.write_text(_render_characters(chars, novel_title),
                            encoding="utf-8")
        written.append(char_path)

    # 3. Faction profiles
    factions = data.get("faction_profiles", [])
    if factions:
        fact_path = output_dir / "02_势力格局.md"
        fact_path.write_text(_render_factions(factions, novel_title),
                            encoding="utf-8")
        written.append(fact_path)

    # 4. Location atlas
    locations = data.get("location_profiles", [])
    if locations:
        loc_path = output_dir / "03_地理图志.md"
        loc_path.write_text(_render_locations(locations, novel_title),
                           encoding="utf-8")
        written.append(loc_path)

    # 5. Power system
    powers = data.get("power_profiles", [])
    if powers:
        pow_path = output_dir / "04_修炼体系.md"
        pow_path.write_text(_render_powers(powers, novel_title),
                           encoding="utf-8")
        written.append(pow_path)

    # 6. Plot arcs
    arcs = data.get("plot_arcs", [])
    if arcs:
        arc_path = output_dir / "05_剧情线分析.md"
        arc_path.write_text(_render_arcs(arcs, novel_title),
                           encoding="utf-8")
        written.append(arc_path)

    # 7. Retcons and gaps
    retcons = data.get("retcons", [])
    gaps = data.get("gaps", [])
    if retcons or gaps:
        issue_path = output_dir / "06_矛盾与缺口.md"
        issue_path.write_text(_render_issues(retcons, gaps, novel_title),
                             encoding="utf-8")
        written.append(issue_path)

    return written


def _render_main_report(data: dict, title: str, author: str, timestamp: str) -> str:
    """Render the main overview report."""
    stats = data.get("stats", {})
    outline = data.get("outline", {})
    chars = data.get("character_profiles", [])
    factions = data.get("faction_profiles", [])
    locations = data.get("location_profiles", [])
    powers = data.get("power_profiles", [])

    lines = [
        f"# 《{title}》全书分析报告",
        f"",
        f"> 作者: {author}",
        f"> 生成时间: {timestamp}",
        f"> 分析引擎: novel-decomp v0.1.0 (Claude Sonnet)",
        f"",
        "---",
        "",
        "## 📊 概览",
        "",
        f"| 项目 | 数据 |",
        f"|------|------|",
        f"| 总章节数 | {stats.get('total_chapters', outline.get('total_chapters', '?'))} |",
        f"| 角色总数 | {len(chars)} |",
        f"| 势力总数 | {len(factions)} |",
        f"| 地点总数 | {len(locations)} |",
        f"| 功法/能力数 | {len(powers)} |",
        f"| 剧情弧数 | {len(data.get('plot_arcs', []))} |",
        f"| 检测到的矛盾 | {len(data.get('retcons', []))} |",
        f"| 角色长期缺席 | {len(data.get('gaps', []))} |",
        f"",
        "---",
        "",
        "## 📖 全书大纲",
        "",
    ]

    # Render volumes
    volumes = outline.get("volumes", [])
    for vol in volumes:
        lines.append(f"### {vol.get('title', '')} "
                     f"({vol.get('chapter_range', [0,0])[0]}-{vol.get('chapter_range', [0,0])[1]}章)")
        lines.append(f"")
        lines.append(f"{vol.get('summary', '')}")
        lines.append(f"")
        if vol.get("key_developments"):
            for dev in vol["key_developments"][:5]:
                lines.append(f"- {dev}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 📋 逐章概要")
    lines.append("")

    for ch in outline.get("chapter_outline", [])[:50]:  # First 50 chapters in overview
        lines.append(f"- **第{ch['number']}章 {ch.get('title', '')}**: {ch.get('summary', '')[:100]}...")
        lines.append("")

    if outline.get("chapter_outline", []) and len(outline["chapter_outline"]) > 50:
        lines.append(f"*... (共 {len(outline['chapter_outline'])} 章，完整内容见分卷文件)*")

    return "\n".join(lines)


def _render_characters(chars: list[dict], title: str) -> str:
    """Render character profiles Markdown."""
    lines = [
        f"# 《{title}》角色大全",
        "",
        f"> 共 {len(chars)} 个角色",
        "",
        "---",
        "",
    ]

    role_order = {"主角": 0, "反派": 1, "导师": 2, "恋人": 3, "家人": 4,
                   "配角": 5, "路人": 6}
    chars_sorted = sorted(chars, key=lambda c: (
        role_order.get(c.get("role", "其他"), 99),
        -c.get("appearance_count", 0),
    ))

    for i, ch in enumerate(chars_sorted):
        lines.append(f"## {ch['name']}")
        lines.append(f"")
        lines.append(f"| 属性 | 值 |")
        lines.append(f"|------|----|")
        lines.append(f"| ID | `{ch.get('id', '')}` |")
        lines.append(f"| 角色定位 | {ch.get('role', '')} |")
        lines.append(f"| 性别 | {ch.get('gender', '未提及')} |")
        lines.append(f"| 年龄 | {ch.get('age_hint', '未提及')} |")
        lines.append(f"| 别名 | {', '.join(ch.get('aliases', [])) or '无'} |")
        lines.append(f"| 状态 | {ch.get('status', '')} |")
        lines.append(f"| 首次出场 | 第{ch.get('first_appearance', '?')}章 |")
        lines.append(f"| 最后出场 | 第{ch.get('last_appearance', '?')}章 |")
        lines.append(f"| 出场次数 | {ch.get('appearance_count', 0)}章 |")
        lines.append(f"| 所属势力 | {', '.join(ch.get('faction_affiliations', [])) or '无'} |")
        lines.append(f"| 性格特征 | {', '.join(ch.get('personality', [])) or '未提及'} |")
        lines.append(f"| 能力 | {', '.join(ch.get('powers', [])) or '未提及'} |")
        lines.append("")
        if ch.get("description"):
            lines.append(f"**描述**: {ch['description']}")
            lines.append("")
        if ch.get("character_arc"):
            lines.append(f"**角色弧光**: {ch['character_arc']}")
            lines.append("")
        if ch.get("relationships"):
            lines.append("**关系网**:")
            for rel in ch["relationships"][:10]:
                lines.append(f"- {rel}")
            lines.append("")
        if ch.get("image_hints"):
            lines.append(f"**形象提示**: {', '.join(ch['image_hints'])}")
            lines.append("")

        # ── 动态演变（性格变化、能力进阶、状态时间线）──
        evo = ch.get("evolution", {})
        if evo:
            # 性格变化
            personality_stages = evo.get("personality_stages", [])
            if len(personality_stages) >= 2:
                lines.append("### 性格演变")
                lines.append("")
                lines.append("| 章节 | 性格特征 |")
                lines.append("|------|---------|")
                for stage in personality_stages:
                    traits = "、".join(stage.get("traits", []))
                    lines.append(f"| 第{stage['chapter']}章 | {traits} |")
                lines.append("")

            # 能力进阶
            power_stages = evo.get("power_stages", [])
            if power_stages:
                lines.append("### 能力/修炼进阶")
                lines.append("")
                lines.append("| 章节 | 能力变化 | 新增 |")
                lines.append("|------|---------|------|")
                for stage in power_stages:
                    hints = "、".join(stage.get("hints", [])[:5])
                    new = "、".join(stage.get("new", [])[:5])
                    lines.append(f"| 第{stage['chapter']}章 | {hints} | {new} |")
                lines.append("")

            # 状态时间线
            status_timeline = evo.get("status_timeline", [])
            if len(status_timeline) >= 2:
                lines.append("### 状态变化")
                lines.append("")
                status_map = {"active": "🟢 活跃", "活跃": "🟢 活跃",
                              "deceased": "💀 已死亡", "已死亡": "💀 已死亡",
                              "missing": "❓ 失踪", "失踪": "❓ 失踪",
                              "unknown": "❔ 未知", "未知": "❔ 未知"}
                for st in status_timeline:
                    icon = status_map.get(st.get("status", ""), st.get("status", ""))
                    lines.append(f"- 第{st['chapter']}章 → {icon}")
                lines.append("")

            # 所属势力变化
            faction_changes = evo.get("faction_changes", [])
            if len(faction_changes) >= 2:
                lines.append("### 势力归属变化")
                lines.append("")
                for fc in faction_changes:
                    lines.append(f"- 第{fc['chapter']}章 → **{fc['faction']}**")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _render_factions(factions: list[dict], title: str) -> str:
    """Render faction profiles."""
    lines = [
        f"# 《{title}》势力格局",
        "",
        f"> 共 {len(factions)} 个势力",
        "",
        "---",
        "",
    ]

    for f in factions:
        lines.append(f"## {f['name']}")
        lines.append(f"")
        lines.append(f"| 属性 | 值 |")
        lines.append(f"|------|----|")
        lines.append(f"| 类型 | {f.get('type', '')} |")
        lines.append(f"| 首领 | {f.get('leader', '')} |")
        lines.append(f"| 成员数 | {f.get('member_count', 0)} |")
        lines.append(f"| 势力范围 | {f.get('territory', '')} |")
        lines.append(f"| 实力评估 | {f.get('strength_hint', '')} |")
        lines.append(f"| 状态 | {f.get('status', '')} |")
        lines.append(f"| 首次出场 | 第{f.get('first_appearance', '?')}章 |")
        lines.append("")
        if f.get("ideology"):
            lines.append(f"**理念**: {f['ideology']}")
            lines.append("")
        if f.get("goals"):
            lines.append("**目标**:")
            for g in f["goals"]:
                lines.append(f"- {g}")
            lines.append("")
        if f.get("allies") or f.get("enemies"):
            lines.append(f"**同盟**: {', '.join(f.get('allies', [])) or '无'}")
            lines.append(f"**敌对**: {', '.join(f.get('enemies', [])) or '无'}")
            lines.append("")
        if f.get("timeline"):
            lines.append("**重大事件**:")
            for ev in f["timeline"][:10]:
                lines.append(f"- 第{ev.get('chapter', '?')}章: {ev.get('event', '')}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _render_locations(locations: list[dict], title: str) -> str:
    """Render location atlas."""
    lines = [
        f"# 《{title}》地理图志",
        "",
        f"> 共 {len(locations)} 个地点",
        "",
        "---",
        "",
    ]

    for loc in locations:
        lines.append(f"## {loc['name']}")
        lines.append(f"- **类型**: {loc.get('type', '')}")
        lines.append(f"- **所属区域**: {loc.get('parent', '无')}")
        lines.append(f"- **重要性**: {loc.get('significance', '')}")
        lines.append(f"- **首次出场**: 第{loc.get('first_appearance', '?')}章")
        lines.append(f"- **出场次数**: {loc.get('chapter_count', 0)}章")
        if loc.get('features'):
            lines.append(f"- **特征**: {', '.join(loc['features'])}")
        if loc.get('affiliated_factions'):
            lines.append(f"- **关联势力**: {', '.join(loc['affiliated_factions'])}")
        if loc.get('description'):
            lines.append(f"- **描述**: {loc['description']}")
        lines.append("")

    return "\n".join(lines)


def _render_powers(powers: list[dict], title: str) -> str:
    """Render power system."""
    lines = [
        f"# 《{title}》修炼体系",
        "",
        f"> 共 {len(powers)} 个功法/能力",
        "",
        "---",
        "",
    ]

    # Group by category
    from collections import defaultdict
    by_category = defaultdict(list)
    for p in powers:
        by_category[p.get("category", "其他")].append(p)

    for cat, items in sorted(by_category.items()):
        lines.append(f"## {cat}")
        lines.append("")

        for p in items:
            lines.append(f"### {p['name']}")
            lines.append(f"- **使用者** ({p.get('user_count', 0)}人): "
                         f"{', '.join(p.get('users', [])[:10])}")
            if p.get("tiers"):
                lines.append(f"- **等级**: {' → '.join(p['tiers'])}")
            if p.get("source"):
                lines.append(f"- **来源**: {p['source']}")
            if p.get("mechanics"):
                lines.append(f"- **机制**: {p['mechanics']}")
            if p.get("limitations"):
                lines.append(f"- **限制**: {', '.join(p['limitations'])}")
            if p.get("description"):
                lines.append(f"- **描述**: {p['description']}")
            lines.append("")

    return "\n".join(lines)


def _render_arcs(arcs: list[dict], title: str) -> str:
    """Render plot arcs."""
    lines = [
        f"# 《{title}》剧情线分析",
        "",
        f"> 共 {len(arcs)} 个剧情弧",
        "",
        "---",
        "",
    ]

    for arc in arcs:
        lines.append(f"## {arc['name']} — {arc.get('arc_type', '')}")
        lines.append(f"")
        lines.append(f"- **范围**: 第{arc['start_chapter']}-{arc['end_chapter']}章 "
                     f"({arc.get('chapter_count', 0)}章)")
        lines.append(f"- **类型**: {arc.get('arc_type', '')}")
        lines.append(f"- **状态**: {'已完结' if arc.get('is_complete') else '进行中'}")
        lines.append(f"- **情感高潮**: 第{arc.get('emotional_peak_chapter', '?')}章")
        lines.append(f"- **主要角色**: {', '.join(arc.get('primary_characters', [])[:5])}")
        lines.append(f"- **主要地点**: {', '.join(arc.get('primary_locations', [])[:5])}")
        if arc.get("top_tags"):
            tags_str = ", ".join(f"{t}({c})" for t, c in arc["top_tags"])
            lines.append(f"- **标签**: {tags_str}")
        lines.append("")
        lines.append(f"**摘要**: {arc.get('summary', '')}")
        lines.append("")
        if arc.get("beats"):
            lines.append("**关键节拍**:")
            for beat in arc["beats"]:
                lines.append(f"- 第{beat['chapter']}章: {beat['summary']}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _render_issues(retcons: list[dict], gaps: list[dict], title: str) -> str:
    """Render retcons and gaps."""
    lines = [
        f"# 《{title}》矛盾与缺口",
        "",
        f"> 检测到的矛盾: {len(retcons)} 处",
        f"> 角色长期缺席: {len(gaps)} 处",
        "",
        "---",
        "",
    ]

    if retcons:
        lines.append("## ⚠️ 检测到的矛盾 (Retcons)")
        lines.append("")
        for r in retcons:
            sev = r.get("severity", "moderate")
            sev_icon = {"minor": "ℹ️", "moderate": "⚠️", "major": "🔴"}.get(sev, "⚠️")
            lines.append(f"- {sev_icon} **[{sev}]** {r.get('description', '')}")
            if r.get("chapter"):
                lines.append(f"  - 章节: 第{r['chapter']}章 | 来源: {r.get('source', '')}")
            lines.append("")
        lines.append("")

    if gaps:
        lines.append("## 🔍 角色长期缺席")
        lines.append("")
        lines.append("| 角色 | 缺席区间 | 缺口长度 | 严重度 | 推测 |")
        lines.append("|------|----------|----------|--------|------|")
        for g in gaps:
            lines.append(f"| {g.get('character_name', '?')} | "
                        f"第{g.get('disappeared_chapter', '?')}-{g.get('reappeared_chapter', '?')}章 | "
                        f"{g.get('gap_length', 0)}章 | "
                        f"{g.get('severity', '')} | "
                        f"{g.get('explanation', '')} |")
        lines.append("")

    return "\n".join(lines)
