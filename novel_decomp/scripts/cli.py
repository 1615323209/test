"""CLI entry point for the novel decomposition pipeline.

Commands:
    novel-decomp run       # Full pipeline from start
    novel-decomp resume    # Resume from last checkpoint
    novel-decomp export    # Export results (markdown, human-review samples)
    novel-decomp estimate  # Cost estimate without running
    novel-decomp status    # Show pipeline status
"""

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from novel_decomp.config import (
    DEFAULT_MODEL, CHEAP_MODEL, DEFAULT_BATCH_SIZE,
    MAX_BATCH_TOKENS, DATA_DIR, OUTPUT_DIR, CHECKPOINT_DIR,
    ANTHROPIC_API_KEY, PROVIDER, get_price, get_provider_info,
)
from novel_decomp.anthropic_client import AnthropicClient
from novel_decomp.cache.disk_cache import DiskCache
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

    # Extract novel metadata
    novel_metadata = _extract_metadata(novel_path, chapters)

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
):
    """导出分析结果。"""
    output_dir = Path(output)

    if human_review:
        console.print(f"[cyan]导出人工验证样本 ({sample_size} 章)...[/cyan]")
        from novel_decomp.export.sampling import export_human_review_sample
        # Try to find layer2 data
        l2_dir = output_dir / "layer2"
        if not l2_dir.exists():
            console.print("[red]未找到 Layer 2 分析数据。请先运行 'novel-decomp run'。[/red]")
            raise typer.Exit(1)
        # Try to find original novel
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

    # Full export
    console.print("[cyan]导出分析报告...[/cyan]")
    l4_path = output_dir / "layer4_synthesis.json"
    if not l4_path.exists():
        console.print("[red]未找到 Layer 4 合成数据。请先运行完整的流水线。[/red]")
        raise typer.Exit(1)

    import json
    data = json.loads(l4_path.read_text(encoding="utf-8"))

    from novel_decomp.export.markdown import export_all
    # Get novel metadata from checkpoint
    from novel_decomp.pipeline.checkpoint import CheckpointManager
    cp_mgr = CheckpointManager(CHECKPOINT_DIR)
    state = cp_mgr.load() or {}
    novel_meta = state.get("layers", {}).get("1", {})
    title = novel_meta.get("novel_title", "未知小说")
    author = novel_meta.get("novel_author", "未知作者")

    files = export_all(data, output_dir / "reports", novel_title=title, author=author)
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


def _extract_metadata(novel_path: Path, chapters: list) -> dict:
    """Extract novel metadata from the raw file and chapters.

    Args:
        novel_path: Path to novel file.
        chapters: Extracted chapter list.

    Returns:
        Dict with title, author, synopsis, etc.
    """
    metadata = {
        "title": novel_path.stem,
        "author": "未知",
        "synopsis": "",
        "total_chapters": len([c for c in chapters if not c.is_afterword]),
    }

    # Try to extract from the raw file header
    try:
        text = novel_path.read_text(encoding="utf-8")
        lines = text.split("\n")[:50]

        import re
        for line in lines:
            line = line.strip()
            if line.startswith("书名") or line.startswith("书名："):
                metadata["title"] = re.sub(r"书名[：:]?\s*", "", line)
            elif line.startswith("作者") or line.startswith("作者："):
                metadata["author"] = re.sub(r"作者[：:]?\s*", "", line)
            elif line.startswith("简介") or line.startswith("简介："):
                synopsis_lines = [re.sub(r"简介[：:]?\s*", "", line)]
                # Collect next lines until we hit a chapter marker or blank line
                for next_line in lines[lines.index(line)+1:]:
                    if re.match(r"^第\d+章", next_line.strip()):
                        break
                    if next_line.strip():
                        synopsis_lines.append(next_line.strip())
                metadata["synopsis"] = " ".join(synopsis_lines)[:500]
                break
    except Exception:
        pass

    return metadata


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


# asyncio helper
import asyncio

if __name__ == "__main__":
    app()
