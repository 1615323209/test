"""CLI entry point for the novel decomposition pipeline.

Commands:
    novel-decomp run       # Full pipeline from start
    novel-decomp resume    # Resume from last checkpoint
    novel-decomp export    # Export results (markdown, human-review samples)
    novel-decomp estimate  # Cost estimate without running
    novel-decomp status    # Show pipeline status
"""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from novel_decomp.config import (
    DEFAULT_MODEL, CHEAP_MODEL, DEFAULT_BATCH_SIZE,
    DATA_DIR, OUTPUT_DIR, CHECKPOINT_DIR,
    ANTHROPIC_API_KEY, get_price, get_provider_info,
)
from novel_decomp.pipeline.checkpoint import CheckpointManager
from novel_decomp.layer1.extractor import extract_chapters, validate_chapters
from novel_decomp.layer1.batcher import build_batches, get_batch_stats

app = typer.Typer(help="网文拆解系统 — AI-powered novel decomposition")
console = Console()


@app.command()
def run(
    novel: str = typer.Option(..., "--novel", "-n", help="小说txt文件路径"),
    output: str = typer.Option(str(OUTPUT_DIR), "--output", "-o", help="输出目录"),
    batch_size: int = typer.Option(DEFAULT_BATCH_SIZE, "--batch-size", "-b", help="每批章数"),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m", help="Claude模型ID"),
    cheap_model: str = typer.Option(CHEAP_MODEL, "--cheap-model", help="廉价模型ID"),
    concurrency: int = typer.Option(3, "--concurrency", "-c", help="最大并发API调用数"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅估算成本，不实际运行"),
    sample_size: int = typer.Option(10, "--sample-size", "-s", help="人工验证随机抽样章数"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """运行完整的网文拆解流程（4层全流程）。"""
    _check_api_key()

    novel_path = Path(novel).resolve()
    if not novel_path.exists():
        console.print(f"[red]错误:[/red] 文件不存在: {novel_path}")
        raise typer.Exit(1)

    output_dir = Path(output)
    checkpoint_dir = CHECKPOINT_DIR

    provider_info = get_provider_info()

    console.print(f"[bold cyan]══════════════════════════════════════════════[/bold cyan]")
    console.print(f"[bold cyan]  网文拆解系统 — Novel Decomposition Pipeline[/bold cyan]")
    console.print(f"[bold cyan]══════════════════════════════════════════════[/bold cyan]")
    console.print(f"  小说: {novel_path.name}")
    console.print(f"  提供商: {provider_info['provider']}")
    console.print(f"  主模型: {model}")
    console.print(f"  廉价模型: {cheap_model}")
    console.print(f"  每批章数: {batch_size}")
    console.print(f"  输出目录: {output_dir}")
    console.print()

    # ─── Layer 1: Preprocessing ───
    console.print("[bold]── Layer 1: 预处理 ──[/bold]")
    console.print("  正在解析章节...", end=" ")
    chapters = extract_chapters(str(novel_path))
    console.print(f"✓ {len(chapters)} 章")

    issues = validate_chapters(chapters)
    if issues:
        console.print(f"  [yellow]发现 {len(issues)} 个问题:[/yellow]")
        for issue in issues[:5]:
            console.print(f"    - {issue}")
    else:
        console.print("  [green]章节验证通过[/green]")

    console.print("  正在构建批次...", end=" ")
    batches = build_batches(chapters, target_chapters_per_batch=batch_size)
    stats = get_batch_stats(batches)
    console.print(f"✓ {stats['total_batches']} 个批次 (平均每批 {stats['avg_chapters_per_batch']:.1f} 章, "
                   f"~{stats['avg_tokens_per_batch']:.0f} tokens/批)")

    # Dry run: print estimate and exit
    if dry_run:
        batches = build_batches(chapters, target_chapters_per_batch=batch_size)
        stats = get_batch_stats(batches)
        _print_cost_estimate(batches, model, cheap_model, stats)
        return

    # Run full pipeline via orchestrator
    from novel_decomp.pipeline.orchestrator import run_full_pipeline

    try:
        result = asyncio.run(run_full_pipeline(
            novel_path=str(novel_path),
            output_dir=output_dir,
            checkpoint_dir=checkpoint_dir,
            model=model,
            cheap_model=cheap_model,
            batch_size=batch_size,
            sample_size=sample_size,
            verbose=verbose,
        ))

        console.print(f"\n[bold green]══════════════════════════════════════════════[/bold green]")
        console.print(f"[bold green]  拆解完成![/bold green]")
        console.print(f"[bold green]  输出目录: {output_dir}[/bold green]")
        console.print(f"[bold green]  使用情况: {result['usage']}[/bold green]")
        console.print(f"[bold green]  人工验证样本: {result['review_file']}[/bold green]")
        console.print(f"[bold green]══════════════════════════════════════════════[/bold green]")

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ 用户中断。已保存检查点，可使用 'novel-decomp resume' 继续。[/yellow]")
        raise typer.Exit(0)
    except Exception as e:
        console.print(f"\n[red]✗ 流水线失败: {e}[/red]")
        if verbose:
            import traceback
            traceback.print_exc()
        raise typer.Exit(1)


@app.command()
def resume(
    checkpoint_dir: str = typer.Option(str(CHECKPOINT_DIR), "--checkpoint", help="检查点目录"),
    novel: str = typer.Option("", "--novel", "-n", help="小说路径（如与检查点不同）"),
    force_model: str = typer.Option("", "--model", "-m", help="强制使用指定模型"),
):
    """从上次检查点恢复运行。"""
    _check_api_key()

    cp_dir = Path(checkpoint_dir)
    checkpoint_mgr = CheckpointManager(cp_dir)
    state = checkpoint_mgr.load()

    if not state:
        console.print("[red]未找到检查点文件。请使用 'novel-decomp run' 开始新任务。[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]从检查点恢复:[/cyan]")
    console.print(f"  小说: {state.get('novel_path', 'unknown')}")
    console.print(f"  当前层: {state.get('current_layer', 1)}")
    for layer_key, ls in state.get("layers", {}).items():
        console.print(f"  Layer {layer_key}: {ls.get('status')} "
                       f"({ls.get('batches_completed', 0)}/{ls.get('total_batches', 0)} batches)")
    console.print()

    # Resume via orchestrator
    from novel_decomp.pipeline.orchestrator import resume_pipeline

    try:
        result = asyncio.run(resume_pipeline(
            checkpoint_dir=str(cp_dir),
            output_dir=str(OUTPUT_DIR),
            novel_path=novel or "",
            model=force_model,
        ))
        console.print(f"\n[green]✓ 恢复完成: {result['usage']}[/green]")
    except Exception as e:
        console.print(f"[red]恢复失败: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def export(
    output: str = typer.Option(str(OUTPUT_DIR), "--output", "-o", help="输出目录"),
    format: str = typer.Option("both", "--format", "-f", help="输出格式: markdown/json/both"),
    sample_size: int = typer.Option(20, "--sample-size", "-s", help="人工验证随机抽样章数"),
    human_review: bool = typer.Option(False, "--human-review", help="导出人工验证样本"),
    partial: bool = typer.Option(False, "--partial", "-p", help="从已完成的批次生成预览报告"),
):
    """导出分析结果。--partial 从已完成的批次即时生成报告，无需等待全部完成。"""
    output_dir = Path(output)

    if human_review:
        console.print(f"[cyan]导出人工验证样本 ({sample_size} 章)...[/cyan]")
        from novel_decomp.export.sampling import export_human_review_sample
        l2_dir = output_dir / "layer2"
        if not l2_dir.exists():
            console.print("[red]未找到 Layer 2 分析数据。请先运行 'novel-decomp run'。[/red]")
            raise typer.Exit(1)
        from novel_decomp.pipeline.checkpoint import CheckpointManager
        cp_mgr = CheckpointManager(CHECKPOINT_DIR)
        state = cp_mgr.load()
        novel_path = state.get("novel_path", "") if state else ""
        if not novel_path:
            console.print("[yellow]未找到小说原始路径，请在 checkpoint 中检查。[/yellow]")
            raise typer.Exit(1)
        try:
            path = export_human_review_sample(l2_dir, novel_path, EXPORT_DIR, sample_size=sample_size)
            console.print(f"[green]✓ 已导出人工验证样本: {path}[/green]")
        except Exception as e:
            console.print(f"[red]导出失败: {e}[/red]")
            raise typer.Exit(1)
        return

    # Partial export — generate report from whatever batches exist
    if partial:
        _export_partial(output_dir)
        return

    # Full export
    console.print("[cyan]导出分析报告...[/cyan]")
    l4_path = output_dir / "layer4_synthesis.json"
    if not l4_path.exists():
        console.print("[red]未找到 Layer 4 合成数据。先运行 'novel-decomp resume' 或 'novel-decomp export --partial' 预览。[/red]")
        raise typer.Exit(1)

    import json
    data = json.loads(l4_path.read_text(encoding="utf-8"))

    from novel_decomp.export.markdown import export_all
    from novel_decomp.pipeline.checkpoint import CheckpointManager
    cp_mgr = CheckpointManager(CHECKPOINT_DIR)
    state = cp_mgr.load() or {}
    novel_meta = state.get("layers", {}).get("1", {})
    title = novel_meta.get("novel_title", "未知小说")
    author = novel_meta.get("novel_author", "未知作者")

    files = export_all(data, output_dir / "reports",
                       novel_title=title, author=author,
                       layer2_dir=output_dir / "layer2")
    console.print(f"[green]✓ 已导出 {len(files)} 个报告文件:[/green]")
    for f in files:
        console.print(f"  - {f.name}")


@app.command()
def estimate(
    novel: str = typer.Option(..., "--novel", "-n", help="小说txt文件路径"),
    batch_size: int = typer.Option(DEFAULT_BATCH_SIZE, "--batch-size", "-b", help="每批章数"),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m", help="模型ID"),
):
    """估算分析成本（不实际调用API）。"""
    novel_path = Path(novel).resolve()
    if not novel_path.exists():
        console.print(f"[red]文件不存在: {novel_path}[/red]")
        raise typer.Exit(1)

    chapters = extract_chapters(str(novel_path))
    batches = build_batches(chapters, target_chapters_per_batch=batch_size)
    stats = get_batch_stats(batches)

    console.print(f"[cyan]成本估算:[/cyan]")
    console.print(f"  总章数: {len(chapters)}")
    _print_cost_estimate(batches, model, CHEAP_MODEL, stats)


@app.command()
def status(
    checkpoint_dir: str = typer.Option(str(CHECKPOINT_DIR), "--checkpoint", help="检查点目录"),
):
    """查看当前流水线状态。"""
    cp_dir = Path(checkpoint_dir)
    checkpoint_mgr = CheckpointManager(cp_dir)
    state = checkpoint_mgr.load()

    if not state:
        console.print("[dim]无活跃任务。使用 'novel-decomp run' 开始新任务。[/dim]")
        return

    table = Table(title="Pipeline Status")
    table.add_column("Layer", style="cyan")
    table.add_column("Status", style="magenta")
    table.add_column("Progress", justify="right")
    table.add_column("Error")

    for layer_key in ["1", "2", "3", "4"]:
        ls = state.get("layers", {}).get(layer_key, {})
        status_str = ls.get("status", "pending")
        status_color = {
            "completed": "green",
            "running": "yellow",
            "failed": "red",
            "partial": "yellow",
            "pending": "dim",
        }.get(status_str, "white")

        progress = f"{ls.get('batches_completed', 0)}/{ls.get('total_batches', 0)}"
        error = ls.get("error", "")[:60]

        table.add_row(
            f"Layer {layer_key}",
            f"[{status_color}]{status_str}[/{status_color}]",
            progress,
            error,
        )

    table.add_row("", "", "", "")
    table.add_row(
        "Total Tokens",
        "",
        str(state.get("total_tokens_used", 0)),
        f"~${state.get('total_cost_estimate', 0):.2f}",
    )

    console.print(table)


def _check_api_key():
    """Verify API key is set."""
    if not ANTHROPIC_API_KEY:
        console.print("[red]错误: 未设置 ANTHROPIC_API_KEY 环境变量。[/red]")
        console.print("请在 .env 文件或环境变量中设置你的 Anthropic API Key。")
        console.print("示例: ANTHROPIC_API_KEY=sk-ant-...")
        raise typer.Exit(1)


def _export_partial(output_dir: Path):
    """Generate preview report from available batch files — no Layer 3/4 needed."""
    import json
    import re
    from collections import defaultdict

    def _build_name_map(batch_files: list) -> dict:
        """Build id→canonical_name map from entity updates across all batches."""
        name_map = {}
        for bf in batch_files:
            try:
                batch = json.loads(bf.read_text(encoding="utf-8"))
            except Exception:
                continue
            eu = batch.get("entity_updates", {})
            for ent_type in ("characters", "角色", "factions", "势力", "locations", "地点", "powers", "功法能力"):
                for ent in eu.get(ent_type, []):
                    eid = ent.get("id", "")
                    cname = ent.get("canonical_name") or ent.get("名称", "")
                    if eid and cname:
                        name_map[eid] = cname
            # Also map any name that looks like bare pinyin without prefix
            for ent in eu.get("locations", eu.get("地点", [])):
                eid = ent.get("id", "")
                cname = ent.get("canonical_name") or ent.get("名称", "")
                if eid and cname:
                    # Map both loc_xxx → 中文名 AND bare xxx → 中文名
                    if eid.startswith("loc_"):
                        bare = eid[4:]
                        if bare not in name_map:
                            name_map[bare] = cname
        return name_map

    def _resolve(name: str, name_map: dict) -> str:
        """Resolve an entity reference to its canonical Chinese name."""
        if not isinstance(name, str):
            return str(name)
        # Direct match: loc_xxx or xxx
        if name in name_map:
            return name_map[name]
        # Strip prefix and try again
        if re.match(r'^(char|fact|loc|pow)_', name):
            bare = re.sub(r'^(char|fact|loc|pow)_', '', name)
            return name_map.get(bare, bare)
        return name

    l2_dir = output_dir / "layer2"
    batch_files = sorted(l2_dir.glob("batch_*.json"))
    if not batch_files:
        console.print("[red]没有找到任何批次文件。至少需要 1 个批次完成。[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]从 {len(batch_files)} 个已完成批次生成预览报告...[/cyan]")

    # Build id→name map first, then resolve all references through it
    name_map = _build_name_map(batch_files)

    all_chars = defaultdict(lambda: {"appearances": 0, "first_ch": 9999, "last_ch": 0, "chapters": []})
    all_factions = defaultdict(lambda: {"appearances": 0, "first_ch": 9999})
    all_locs = defaultdict(lambda: {"appearances": 0, "first_ch": 9999})
    chapter_summaries = []

    for bf in batch_files:
        batch = json.loads(bf.read_text(encoding="utf-8"))
        for ch in batch.get("chapters", []):
            ch_num = ch.get("chapter_number", 0)
            chapter_summaries.append({
                "number": ch_num,
                "title": ch.get("title", ""),
                "summary": ch.get("summary", ""),
                "tone": ch.get("emotional_tone", ""),
                "tags": ch.get("plot_tags", []),
                "pov": _resolve(ch.get("pov_character", ""), name_map),
            })
            for c in ch.get("characters_appeared", []):
                name = c if isinstance(c, str) else (c.get("名称") or c.get("name", str(c)))
                name = _resolve(name, name_map)
                all_chars[name]["appearances"] += 1
                all_chars[name]["first_ch"] = min(all_chars[name]["first_ch"], ch_num)
                all_chars[name]["last_ch"] = max(all_chars[name]["last_ch"], ch_num)
                all_chars[name]["chapters"].append(ch_num)
            for r in ch.get("character_relationships", ch.get("人物关系", [])):
                for name in (r.get("角色", r.get("characters", []))):
                    name = _resolve(name, name_map)
                    all_chars[name]["appearances"] += 1
            for loc in ch.get("locations_visited", []):
                loc_name = _resolve(loc if isinstance(loc, str) else str(loc), name_map)
                all_locs[loc_name]["appearances"] += 1
                all_locs[loc_name]["first_ch"] = min(all_locs[loc_name]["first_ch"], ch_num)

        eu = batch.get("entity_updates", {})
        for f in eu.get("factions", eu.get("势力", [])):
            name = _resolve(f.get("canonical_name") or f.get("名称", str(f)), name_map)
            all_factions[name]["appearances"] += 1
            all_factions[name]["first_ch"] = min(all_factions[name]["first_ch"], batch.get("batch_id", 0) * 20)

    # ── Render preview markdown ──
    lines = [
        "# 📊 预览报告 — 部分分析结果",
        "",
        f"> ⚠ 仅基于已完成批次 ({len(batch_files)} 批)，非完整分析。",
        f"> 全部完成后运行 `novel-decomp export` 生成完整报告。",
        "",
        "---",
        "",
        f"## 已覆盖: 第 {chapter_summaries[0]['number']}–{chapter_summaries[-1]['number']} 章 ({len(chapter_summaries)} 章)",
        "",
        "---",
        "",
        "## 📋 逐章概要",
        "",
    ]
    for ch in chapter_summaries:
        tags = ", ".join(ch["tags"][:5])
        lines.append(f"- **第{ch['number']}章 {ch['title']}** "
                     f"[{ch.get('tone', '')}] [{tags}] "
                     f"{ch['summary'][:120]}")
        if ch.get("pov"):
            lines[-1] += f"  ←视角: {ch['pov']}"

    lines.extend([
        "",
        "---",
        "",
        f"## 👤 角色出场 ({len(all_chars)} 人)",
        "",
        "| 角色 | 出场次数 | 首次 | 最后 |",
        "|------|----------|------|------|",
    ])
    for name, data in sorted(all_chars.items(), key=lambda x: -x[1]["appearances"]):
        lines.append(f"| {name} | {data['appearances']} | 第{data['first_ch']}章 | 第{data['last_ch']}章 |")

    lines.extend([
        "",
        f"## 🏛 势力 ({len(all_factions)} 个)",
        "",
        "| 势力 | 出现批次 | 首次出现 |",
        "|------|----------|----------|",
    ])
    for name, data in sorted(all_factions.items(), key=lambda x: -x[1]["appearances"]):
        lines.append(f"| {name} | {data['appearances']} | 约第{data['first_ch']}章 |")

    lines.extend([
        "",
        f"## 📍 地点 ({len(all_locs)} 处)",
        "",
        "| 地点 | 出场次数 | 首次 |",
        "|------|----------|------|",
    ])
    for name, data in sorted(all_locs.items(), key=lambda x: -x[1]["appearances"])[:30]:
        lines.append(f"| {name} | {data['appearances']} | 第{data['first_ch']}章 |")

    report_dir = output_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "00_预览报告.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]✓ 预览报告: {report_path}[/green]")


def _print_cost_estimate(batches, model, cheap_model, stats):
    """Print detailed cost estimate."""
    provider_info = get_provider_info()
    in_price, out_price = get_price(model)

    # Layer 2 estimates
    total_input = stats["total_tokens"]
    total_output_est = len(batches) * 2000  # ~2K output tokens per batch
    l2_cost = (total_input * in_price + total_output_est * out_price) / 1_000_000

    # Layer 3/4 estimates (use cheap model pricing for small calls)
    cheap_in, cheap_out = get_price(cheap_model)
    l3_l4_cost = 3.00 * cheap_out / 4.0  # Scaled rough estimate

    console.print()
    console.print(f"[bold cyan]=== 成本估算 ===[/bold cyan]")
    console.print(f"  提供商: {provider_info['provider']}")
    console.print(f"  模型: {model} (廉价: {cheap_model})")
    console.print(f"  总章数: {stats['total_chapters']} 章, {stats['total_batches']} 批次")
    console.print(f"  预估总输入 tokens: {total_input:,}")
    console.print(f"  预估总输出 tokens: {total_output_est:,}")
    console.print()
    console.print(f"  Layer 2 (章节分析):        ${l2_cost:.2f}")
    console.print(f"  Layer 3+4 (聚合+合成):     ~${l3_l4_cost:.2f}")
    console.print(f"  [bold]合计估算:              ${l2_cost + l3_l4_cost:.2f}[/bold]")
    console.print()
    console.print(f"  [dim]实际费用可能因提示词设计、重试等因素上下浮动 30-50%。[/dim]")


@app.command()
def rewrite(
    output: str = typer.Option(str(OUTPUT_DIR), "--output", "-o", help="分析数据目录"),
    out_dir: str = typer.Option("", "--out", help="输出目录（默认为 data/output/rewrite/）"),
    model: str = typer.Option("", "--model", "-m", help="模型ID"),
    world_file: str = typer.Option("", "--world", "-w", help="世界观设定文件路径（如 my_world.md）。提供后跳过AI生成世界观，直接基于你的设定改写角色和细纲"),
    sections: str = typer.Option("", "-s", "--sections", help="要改写的章节，逗号分隔。可选: world,characters,outline。默认全部"),
):
    """改写拆解数据。先用--world提供你的世界观设定，再自动改写角色和细纲。"""
    analysis_dir = Path(output)
    if not (analysis_dir / "layer4_synthesis.json").exists():
        console.print("[red]未找到分析数据。请先运行 'novel-decomp run'。[/red]")
        raise typer.Exit(1)

    section_list = [s.strip() for s in sections.split(",") if s.strip()] if sections else None
    out_path = Path(out_dir) if out_dir else analysis_dir / "rewrite"

    # Load custom world: CLI flag > prompts/rewrite_world.md > auto-generated
    custom_world = ""
    if world_file:
        world_path = Path(world_file)
    else:
        world_path = Path("prompts/rewrite_world.md")

    if world_path.exists():
        content = world_path.read_text(encoding="utf-8")
        # Check if user actually filled it in (not just the template)
        if content.strip() and "在这里写下你的世界观设定" not in content:
            custom_world = content
            console.print(f"[cyan]使用世界观: {world_path} ({len(custom_world)}字)[/cyan]")
        elif world_file:
            console.print(f"[yellow]警告: 世界观文件似乎是空模板，将自动生成[/yellow]")

    if not custom_world:
        console.print(f"[cyan]改写分析数据（先AI重建世界观，再改写角色和细纲）...[/cyan]")
        console.print(f"[dim]提示: 编辑 prompts/rewrite_world.md 填入你的世界观，或 --world 指定文件[/dim]")

    from novel_decomp.rewrite.rewriter import rewrite_sections

    try:
        results = asyncio.run(rewrite_sections(
            analysis_dir=analysis_dir,
            output_dir=out_path,
            model=model or "",
            sections=section_list,
            custom_world=custom_world,
        ))
        console.print(f"[green]✓ 完成 ({len(results)} 个文件): {out_path}[/green]")
    except Exception as e:
        console.print(f"[red]失败: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def write(
    output: str = typer.Option(str(OUTPUT_DIR), "--output", "-o", help="数据目录"),
    out_dir: str = typer.Option("", "--out", help="输出目录（默认 data/output/rewrite/novel/）"),
    model: str = typer.Option("", "--model", "-m", help="模型ID"),
    chapter_range: str = typer.Option("", "-c", "--chapters", help="章节范围，如 '1-10'。默认全部"),
):
    """根据细纲和角色档案写正文。"""
    analysis_dir = Path(output)
    rewrite_dir = analysis_dir / "rewrite"

    # Parse chapter range
    start_ch = 0
    end_ch = 0
    if chapter_range:
        parts = chapter_range.split("-")
        start_ch = int(parts[0].strip())
        end_ch = int(parts[1].strip()) if len(parts) > 1 else start_ch

    out_path = Path(out_dir) if out_dir else rewrite_dir / "novel"
    console.print(f"[cyan]写作正文...[/cyan]")
    console.print(f"[dim]角色: {rewrite_dir}/rewrite_角色档案.md[/dim]")
    console.print(f"[dim]细纲: {rewrite_dir}/rewrite_章节细纲.md[/dim]")

    from novel_decomp.writer.writer import write_chapters

    try:
        result = asyncio.run(write_chapters(
            analysis_dir=analysis_dir,
            output_dir=out_path,
            model=model or "",
            start_chapter=start_ch,
            end_chapter=end_ch,
        ))
        console.print(f"[green]✓ 完成: {result}[/green]")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]失败: {e}[/red]")
        raise typer.Exit(1)


# asyncio helper
import asyncio

if __name__ == "__main__":
    app()
