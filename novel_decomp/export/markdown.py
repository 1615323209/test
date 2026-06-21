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
    layer2_dir: str | Path = "",
) -> list[Path]:
    """Export all analysis results as Markdown files.

    Args:
        data: Complete analysis data dict (Layer 4 synthesis).
        output_dir: Directory to write markdown files.
        novel_title: Novel title for headers.
        author: Author name.
        layer2_dir: Optional path to Layer 2 batch files (for chapter outline).

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

    # 5. Retcons and gaps
    retcons = data.get("retcons", [])
    gaps = data.get("gaps", [])
    if retcons or gaps:
        issue_path = output_dir / "05_矛盾与缺口.md"
        issue_path.write_text(_render_issues(retcons, gaps, novel_title),
                             encoding="utf-8")
        written.append(issue_path)

    # 8. Chapter outline with foreshadowing
    if layer2_dir and Path(layer2_dir).exists():
        try:
            outline_path = export_chapter_outline(
                layer2_dir, output_dir, novel_title=novel_title)
            written.append(outline_path)
        except Exception:
            pass  # Non-critical

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
        f"> 分析引擎: novel-decomp v0.1.0",
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
        f"| 检测到的矛盾 | {len(data.get('retcons', []))} |",
        f"| 角色长期缺席 | {len(data.get('gaps', []))} |",
        f"",
        "---",
        "",
        "## 📖 全书大纲",
        "",
    ]

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

    # World-building: synthesize from analyzed data
    top_locs = sorted(locations, key=lambda x: -x.get("chapter_count", 0))
    dao = [p for p in powers if p.get("tiers")]
    major_factions = sorted(factions, key=lambda x: -x.get("member_count", 0))[:5]

    lines.append("## 🌍 世界观设定")
    lines.append("")

    parts = []
    if top_locs:
        world_locs = [l for l in top_locs if l.get("type") in ("界域", "星球", "世界") or not l.get("parent")]
        if not world_locs:
            world_locs = top_locs[:3]
        loc_names = "、".join(l["name"] for l in world_locs)
        parts.append(f"故事舞台为{loc_names}")
        loc_desc = [l["description"] for l in world_locs if l.get("description")]
        if loc_desc:
            parts[-1] += f"（{'; '.join(loc_desc[:2])}）"
        parts[-1] += "。"

    if dao:
        top_dao = [p for p in dao if p.get("category") == "体系"]
        if not top_dao:
            top_dao = dao[:3]
        dao_str = "、".join(p["name"] for p in top_dao[:5])
        parts.append(f"修炼体系为{dao_str}，每条道途各有九阶")

    if major_factions:
        faction_str = "、".join(f"{f['name']}（{f.get('type', '组织')}）" for f in major_factions)
        parts.append(f"主要势力包括{faction_str}")

    if parts:
        lines.append("。".join(parts) + "。")
    else:
        lines.append("（待数据积累后自动生成。）")

    lines.append("")
    lines.append("---")
    lines.append("")

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

    chars_sorted = sorted(chars, key=lambda c: -c.get("appearance_count", 0))

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
        catchphrases = ch.get("catchphrases", [])
        if catchphrases:
            lines.append(f"| 口头禅 | {', '.join(catchphrases)} |")
        lines.append(f"| 状态 | {ch.get('status', '')} |")
        lines.append(f"| 首次出场 | 第{ch.get('first_appearance', '?')}章 |")
        lines.append(f"| 最后出场 | 第{ch.get('last_appearance', '?')}章 |")
        lines.append(f"| 出场次数 | {ch.get('appearance_count', 0)}章 |")
        lines.append(f"| 所属势力 | {', '.join(ch.get('faction_affiliations', [])) or '无'} |")
        lines.append(f"| 性格特征 | {', '.join(ch.get('personality', [])) or '未提及'} |")
        lines.append(f"| 能力 | {', '.join(ch.get('powers', [])) or '未提及'} |")
        lines.append("")
        if ch.get("background"):
            lines.append(f"**背景**: {ch['background']}")
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
        traits = ch.get("appearance_traits", [])
        if traits:
            lines.append("**外貌特征**:")
            for t in traits:
                if isinstance(t, str):
                    lines.append(f"- {t}")
                else:
                    name = t.get("trait", t.get("特征", "?"))
                    ch_ep = t.get("chapter", t.get("出现章节", "?"))
                    src = t.get("source", t.get("来源", ""))
                    lines.append(f"- **{name}** — 第{ch_ep}章出现{f'（{src}）' if src else ''}")
            lines.append("")

        # ── 动态演变（性格变化、能力进阶、状态时间线）──
        evo = ch.get("evolution", {})
        if evo:
            # 性格变化
            personality_stages = evo.get("personality_stages", [])
            if len(personality_stages) >= 2:
                lines.append("### 性格演变")
                lines.append("")
                lines.append("| 章节 | 性格 | 触发事件 |")
                lines.append("|------|------|----------|")
                for stage in personality_stages:
                    traits = "、".join(stage.get("traits", []))
                    trigger = stage.get("trigger", "")
                    lines.append(f"| 第{stage['chapter']}章 | {traits} | {trigger or '初始性格'} |")
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

            # 能力进阶
            power_stages = evo.get("power_stages", [])
            if power_stages:
                lines.append("### 能力/修炼进阶")
                lines.append("")
                lines.append("| 章节 | 新增能力 | 触发事件 |")
                lines.append("|------|---------|----------|")
                for stage in power_stages:
                    new_p = "、".join(stage.get("new", [])[:5])
                    trigger = stage.get("trigger", "")
                    lines.append(f"| 第{stage['chapter']}章 | {new_p or '—'} | {trigger or '—'} |")
                lines.append("")


            # 所属势力变化
            faction_changes = evo.get("faction_changes", [])
            if faction_changes:
                lines.append("### 势力归属")
                lines.append("")
                for fc in faction_changes:
                    nature = fc.get("nature", "")
                    note = fc.get("note", "")
                    icon = {"正式加入": "🟢", "卧底潜伏": "🔴", "隐藏身份揭露": "👁", "被驱逐": "🚫", "主动退出": "⬅", "联盟合作": "🤝", "双重身份": "🔄"}.get(nature, "")
                    extra = f" — {note}" if note else ""
                    if nature:
                        extra = f"（{icon} {nature}）{extra}"
                    lines.append(f"- 第{fc['chapter']}章 → **{fc['faction']}**{extra}")
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
        lines.append(f"| 首次出场 | 第{f.get('first_appearance', '?')}章 |")
        lines.append("")
        if f.get("description"):
            lines.append(f"**介绍**: {f['description']}")
            lines.append("")
        if f.get("ideology"):
            lines.append(f"**理念**: {f['ideology']}")
            lines.append("")
        if f.get("goals"):
            lines.append("**目标**:")
            for g in f["goals"]:
                if isinstance(g, str):
                    lines.append(f"- {g}")
                else:
                    desc = g.get("描述", g.get("description", str(g)))
                    ch = g.get("确定章节", g.get("chapter", "?"))
                    status = g.get("状态", g.get("status", ""))
                    status_map = {"进行中": "⏳", "已达成": "✅", "已放弃": "❌", "已变更": "🔄"}
                    icon = status_map.get(status, "")
                    lines.append(f"- {icon} 第{ch}章确立: {desc}{f' ({status})' if status else ''}")
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
    """Render location atlas with hierarchical tree structure."""
    lines = [
        f"# 《{title}》地理图志",
        "",
        f"> 共 {len(locations)} 个地点",
        "",
        "---",
        "",
    ]

    if not locations:
        return "\n".join(lines)

    # Build tree: {parent_name: [child_locations]}
    top_level: list[dict] = []
    children_of: dict[str, list[dict]] = {}
    loc_by_name: dict[str, dict] = {}

    for loc in locations:
        name = loc.get("name", "")
        loc_by_name[name] = loc
        parent = loc.get("parent", "")
        if parent and parent in {l.get("name") for l in locations}:
            children_of.setdefault(parent, []).append(loc)
        else:
            top_level.append(loc)

    # Sort top-level by chapter_count desc
    top_level.sort(key=lambda l: -l.get("chapter_count", 0))

    def _render_tree(loc_list: list[dict], depth: int):
        for loc in loc_list:
            name = loc.get("name", "")
            indent = "  " * depth
            prefix = "└ " if depth > 0 else ""
            lines.append(f"{indent}- **{prefix}{name}** "
                         f"({loc.get('type', '')}) "
                         f"— 第{loc.get('first_appearance', '?')}章首次出场，"
                         f"共 {loc.get('chapter_count', 0)} 章")
            if loc.get("description"):
                lines.append(f"{indent}  {loc['description']}")
            if loc.get("significance"):
                lines.append(f"{indent}  *{loc['significance']}*")
            if loc.get("affiliated_factions"):
                lines.append(f"{indent}  势力: {', '.join(loc['affiliated_factions'])}")
            lines.append("")

            # Render children
            kids = children_of.get(name, [])
            kids.sort(key=lambda l: -l.get("chapter_count", 0))
            _render_tree(kids, depth + 1)

    _render_tree(top_level, 0)

    return "\n".join(lines)


def _render_powers(powers: list[dict], title: str) -> str:
    """Render power system with hierarchical tree structure."""
    lines = [
        f"# 《{title}》修炼体系",
        "",
        f"> 共 {len(powers)} 个功法/能力",
        "",
        "---",
        "",
    ]

    if not powers:
        return "\n".join(lines)

    # Separate into top-level systems and sub-paths
    top_systems: list[dict] = []
    children_of: dict[str, list[dict]] = {}
    orphans: list[dict] = []

    for p in powers:
        parent = p.get("parent_system", "")
        if parent and parent in {x.get("name") for x in powers}:
            children_of.setdefault(parent, []).append(p)
        elif parent or p.get("category") == "体系":
            top_systems.append(p)
        else:
            # No parent and not a top-level system → orphan
            orphans.append(p)

    def _render_power_entry(p: dict, depth: int):
        indent = "  " * depth
        prefix = "└ " if depth > 0 else ""
        lines.append(f"{indent}### {prefix}{p['name']}")
        lines.append(f"{indent}- **类别**: {p.get('category', '')}")
        users = p.get("users", [])
        if users:
            lines.append(f"{indent}- **使用者** ({p.get('user_count', len(users))}人): {', '.join(users[:10])}")
        if p.get("tiers"):
            lines.append(f"{indent}- **等级** ({p.get('tier_count', len(p['tiers']))}阶): {' → '.join(p['tiers'])}")
        if p.get("tier_details"):
            lines.append("")
            lines.append(f"{indent}| 阶数 | 阶名 | 进阶方式 | 获得能力 | 代表人物 |")
            lines.append(f"{indent}|------|------|----------|----------|----------|")
            for td in p["tier_details"]:
                advance = td.get("advance_method") or td.get("进阶方式", "?")
                abilities = ", ".join(td.get("abilities") or td.get("获得能力", []))
                reps = ", ".join(td.get("representatives") or td.get("代表人物", []))
                lines.append(f"{indent}| {td.get('level', td.get('阶数', '?'))} "
                             f"| {td.get('name', td.get('阶名', '?'))} "
                             f"| {advance} "
                             f"| {abilities or '—'} "
                             f"| {reps or '—'} |")
            lines.append("")
        if p.get("source"):
            lines.append(f"{indent}- **来源**: {p['source']}")
        if p.get("limitations"):
            lines.append(f"{indent}- **限制**: {', '.join(p['limitations'])}")
        if p.get("description"):
            lines.append(f"{indent}- **描述**: {p['description']}")
        lines.append("")

        # Render children
        kids = children_of.get(p["name"], [])
        kids.sort(key=lambda x: -x.get("user_count", 0))
        for kid in kids:
            _render_power_entry(kid, depth + 1)

    # Sort: systems first, then by user count
    top_systems.sort(key=lambda x: -x.get("user_count", 0))
    for sys in top_systems:
        _render_power_entry(sys, 0)

    # Orphans at the bottom
    if orphans:
        lines.append("## 其他功法/能力")
        lines.append("")
        orphans.sort(key=lambda x: -x.get("user_count", 0))
        for p in orphans:
            _render_power_entry(p, 0)

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


def export_chapter_outline(
    layer2_output_dir: str | Path,
    output_dir: str | Path,
    *,
    novel_title: str = "未知小说",
) -> Path:
    """Generate per-chapter outline with foreshadowing tracking."""
    import json

    l2_dir = Path(layer2_output_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    batch_files = sorted(l2_dir.glob("batch_*.json"))
    if not batch_files:
        raise FileNotFoundError(f"No batch files in {l2_dir}")

    # Build location description map from entity updates
    loc_descriptions: dict[str, str] = {}
    for bf in batch_files:
        try:
            batch = json.loads(bf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for loc in batch.get("entity_updates", {}).get("locations", []):
            name = loc.get("canonical_name") or loc.get("名称", "")
            desc = loc.get("description") or loc.get("描述", "")
            if name and desc:
                loc_descriptions[name] = desc

    lines = [
        f"# 《{novel_title}》章节细纲",
        "",
        "---",
        "",
    ]

    def _strs(items) -> list[str]:
        """Extract strings from items that may be str or dict."""
        result = []
        for it in (items or []):
            if isinstance(it, str):
                result.append(it)
            elif isinstance(it, dict):
                result.append(it.get("名称") or it.get("name", str(it)))
            else:
                result.append(str(it))
        return result

    for bf in batch_files:
        try:
            batch = json.loads(bf.read_text(encoding="utf-8"))
        except Exception:
            continue

        for ch in batch.get("chapters", []):
            ch_num = ch.get("chapter_number", 0)
            title = ch.get("title", "")
            summary = ch.get("summary", "")
            chars = ch.get("characters_appeared", [])
            locations = _strs(ch.get("locations_visited", []))

            planted = ch.get("foreshadowing_planted", ch.get("本章伏笔", []))
            resolved = ch.get("foreshadowing_resolved", ch.get("回收伏笔", []))

            lines.append(f"## 第{ch_num}章 {title}")
            lines.append(f"")
            lines.append(f"**摘要**: {summary}")
            if chars:
                lines.append("**出场角色**:")
                for c in chars:
                    if isinstance(c, str):
                        lines.append(f"- {c}")
                    else:
                        name = c.get("名称", c.get("name", "?"))
                        emotion = c.get("情绪变化", c.get("emotion", ""))
                        em_str = f"（{emotion}）" if emotion else ""
                        lines.append(f"- **{name}**{em_str}")
                lines.append("")

            # Chapter-level relationships
            rels = ch.get("character_relationships", ch.get("人物关系", []))
            if rels:
                lines.append("**人物关系**:")
                for r in rels:
                    pair = r.get("角色", r.get("characters", ["?", "?"]))
                    rel = r.get("关系", r.get("relation", "?"))
                    desc = r.get("描述", r.get("description", ""))
                    desc_str = f" — {desc}" if desc else ""
                    lines.append(f"- {pair[0]} ↔ {pair[1]}（{rel}）{desc_str}")
                lines.append("")
            if locations:
                loc_strs = []
                for l in locations:
                    desc = loc_descriptions.get(l, "")
                    loc_strs.append(f"{l}（{desc}）" if desc else l)
                lines.append(f"**地点**: {', '.join(loc_strs[:5])}")
            lines.append("")

            events = ch.get("key_events", [])
            if events:
                lines.append("**关键事件**:")
                for ev in events:
                    ev_type = ev.get("type", ev.get("类型", "其他"))
                    ev_desc = ev.get("description", ev.get("描述", ""))
                    ev_sig = ev.get("significance", ev.get("重要程度", ""))
                    sig_mark = "★" if ev_sig in ("主线关键", "major") else "·"
                    lines.append(f"- {sig_mark} [{ev_type}] {ev_desc}")
                lines.append("")

            if planted:
                lines.append(f"**📌 本章埋下伏笔** ({len(planted)}条):")
                for fp in planted:
                    imp = fp.get("重要性", fp.get("importance", ""))
                    desc = fp.get("描述", fp.get("description", ""))
                    hint = fp.get("推测回收方式", fp.get("hint", ""))
                    hint_str = f" → 推测: {hint}" if hint else ""
                    lines.append(f"- [{imp}] {desc}{hint_str}")
                lines.append("")

            if resolved:
                lines.append(f"**🔓 本章回收伏笔** ({len(resolved)}条):")
                for fr in resolved:
                    fr_desc = fr.get("描述", fr.get("description", ""))
                    fr_ch = fr.get("埋下章节", fr.get("planted_chapter", "?"))
                    fr_how = fr.get("回收方式", fr.get("resolution", ""))
                    hint = f" ({fr_how})" if fr_how else ""
                    lines.append(f"- 第{fr_ch}章埋下: {fr_desc} → 本章回收{hint}")
                lines.append("")

            lines.append("---")
            lines.append("")

    out_path = out_dir / "06_章节细纲.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
