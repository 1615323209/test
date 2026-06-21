"""Human-review sample export — export random chapters for manual verification.

Generates a side-by-side comparison file: original text vs extracted analysis,
so a human reviewer can spot-check the AI's accuracy.
"""

import json
import random
from pathlib import Path


def export_human_review_sample(
    layer2_output_dir: str | Path,
    original_novel_path: str | Path,
    output_dir: str | Path,
    *,
    sample_size: int = 20,
    seed: int = 42,
) -> Path:
    """Export random chapters for human review.

    Creates a Markdown file with:
    - Original chapter text (first 500 chars)
    - Extracted summary
    - Extracted key events
    - Extracted entities
    - Reviewer checklist

    Args:
        layer2_output_dir: Directory containing batch_NNNN.json files.
        original_novel_path: Path to original novel txt for chapter text.
        output_dir: Where to write the review file.
        sample_size: Number of chapters to sample.
        seed: Random seed for reproducibility.

    Returns:
        Path to the generated review file.
    """
    layer2_dir = Path(layer2_output_dir)
    novel_path = Path(original_novel_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    random.seed(seed)

    # Load all batch outputs
    batch_files = sorted(layer2_dir.glob("batch_*.json"))
    if not batch_files:
        raise FileNotFoundError(f"No batch files in {layer2_dir}")

    all_analyses = []
    for bf in batch_files:
        try:
            batch = json.loads(bf.read_text(encoding="utf-8"))
            for ch in batch.get("chapters", []):
                chars = ch.get("characters_appeared", [])
                locs = ch.get("locations_visited", [])
                # Normalize: extract strings from dicts if schema changed
                _chars = [c if isinstance(c, str) else c.get("名称", c.get("name", str(c))) for c in chars]
                _locs = [l if isinstance(l, str) else l.get("名称", l.get("name", str(l))) for l in locs]
                all_analyses.append({
                    "chapter_number": ch.get("chapter_number", 0),
                    "title": ch.get("title", ""),
                    "summary": ch.get("summary", ""),
                    "key_events": [ev.get("description", "") for ev in ch.get("key_events", [])],
                    "pov": ch.get("pov_character", ""),
                    "characters": _chars,
                    "locations": _locs,
                    "batch_id": batch.get("batch_id", 0),
                })
        except (json.JSONDecodeError, OSError):
            continue

    if not all_analyses:
        raise ValueError("No chapter analyses found in batch files")

    # Sample random chapters
    sample_size = min(sample_size, len(all_analyses))
    samples = random.sample(all_analyses, sample_size)
    samples.sort(key=lambda s: s["chapter_number"])

    # Load original novel for chapter text
    from novel_decomp.layer1.extractor import extract_chapters
    all_chapters = extract_chapters(str(novel_path))
    chapter_map = {ch.number: ch for ch in all_chapters}

    # Build review file
    lines = [
        "# 人工验证样本 — 随机章节对照",
        "",
        f"> 随机抽取 {sample_size} 章进行人工验证",
        f"> 随机种子: {seed}",
        "",
        "阅读以下对照内容，逐项检查AI分析的准确性。",
        "",
        "## 检查清单",
        "",
        "- [ ] **章节摘要**: 摘要是否准确概括了章节内容？有没有遗漏关键情节？",
        "- [ ] **关键事件**: 事件描述是否正确？事件类型标注是否合理？",
        "- [ ] **角色识别**: 出场角色是否全部被识别？POV角色是否正确？",
        "- [ ] **地点识别**: 地点是否正确提取？",
         "",
        "",
        "## 评分标准",
        "",
        "每项评分: ✅ 准确 | ⚠️ 部分偏差 | ❌ 严重错误",
        "",
        "---",
        "",
    ]

    for i, sample in enumerate(samples):
        ch_num = sample["chapter_number"]
        original = chapter_map.get(ch_num)

        lines.append(f"## 样本 {i+1}: 第{ch_num}章 {sample['title']}")
        lines.append(f"")
        lines.append(f"### 📝 AI分析结果")
        lines.append(f"")
        lines.append(f"| 项目 | AI分析 | 人工评分 | 备注 |")
        lines.append(f"|------|--------|----------|------|")
        lines.append(f"| POV角色 | {sample.get('pov', '?')} | ➡️ | |")
        lines.append(f"| 摘要 | {sample.get('summary', '')[:150]}... | ➡️ | |")
        lines.append("")
        lines.append(f"**关键事件**:")
        for ev in sample.get("key_events", [])[:5]:
            lines.append(f"- {ev}")
        lines.append("")
        lines.append(f"**出场角色** ({len(sample.get('characters', []))}人): "
                     f"{', '.join(sample.get('characters', [])[:10])}")
        lines.append(f"**出场地点**: {', '.join(sample.get('locations', [])[:5]) or '无'}")
        lines.append("")
        lines.append("")

        # Original text excerpt
        if original:
            text_excerpt = original.content[:500].replace("\n", "\n> ")
            lines.append(f"### 📖 原文摘录 (前500字)")
            lines.append(f"")
            lines.append(f"> {text_excerpt}...")
            lines.append("")
        else:
            lines.append(f"> ⚠ 原始章节文本未找到")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("### 🔍 人工评审")
        lines.append("")
        lines.append("| 检查项 | 评分 | 说明 |")
        lines.append("|--------|------|------|")
        lines.append("| 摘要准确性 | ⬜ | |")
        lines.append("| 关键事件 | ⬜ | |")
        lines.append("| 角色识别 | ⬜ | |")
        lines.append("| 地点识别 | ⬜ | |")
        lines.append("")
        lines.append("")
        lines.append(f"**总体评价**: ___________")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Write file
    output_path = output_dir / f"human_review_sample_{sample_size}chapters.md"
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
