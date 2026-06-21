"""AI novel writer — generates prose chapters from outlines and character data.

Takes chapter outline (细纲) and character profiles, writes full prose chapters.
"""

import json
import asyncio
from pathlib import Path
from typing import Optional

from novel_decomp.config import (
    DEFAULT_MODEL, OUTPUT_DIR, PROMPTS_DIR, create_client,
)


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


async def write_chapters(
    analysis_dir: str | Path = "",
    char_dir: str | Path = "",
    outline_dir: str | Path = "",
    output_dir: str | Path = "",
    *,
    model: str = "",
    start_chapter: int = 0,
    end_chapter: int = 0,
) -> Path:
    """Write full prose chapters from outlines and character profiles.

    Args:
        analysis_dir: Base data directory.
        char_dir: Directory with character profiles (rewrite_角色档案.md).
        outline_dir: Directory with chapter outlines (rewrite_章节细纲.md).
        output_dir: Where to save generated chapters.
        model: LLM model override.
        start_chapter: First chapter to write (0 = all).
        end_chapter: Last chapter to write (0 = all).

    Returns:
        Path to output directory.
    """
    analysis_dir = Path(analysis_dir) if analysis_dir else OUTPUT_DIR
    rewrite_dir = analysis_dir / "rewrite"

    # Load character profiles
    char_path = Path(char_dir) if char_dir else rewrite_dir / "rewrite_角色档案.md"
    if not char_path.exists():
        raise FileNotFoundError(f"Character profiles not found: {char_path}\nRun 'novel-decomp rewrite' first.")
    characters_text = char_path.read_text(encoding="utf-8")

    # Load chapter outline
    outline_path = Path(outline_dir) if outline_dir else rewrite_dir / "rewrite_章节细纲.md"
    if not outline_path.exists():
        raise FileNotFoundError(f"Chapter outline not found: {outline_path}\nRun 'novel-decomp rewrite' first.")
    outline_text = outline_path.read_text(encoding="utf-8")

    # Parse chapters from outline
    chapters = _parse_outline(outline_text)
    if not chapters:
        raise ValueError("No chapters found in outline")

    # Filter by range
    if start_chapter > 0:
        chapters = [c for c in chapters if c["number"] >= start_chapter]
    if end_chapter > 0:
        chapters = [c for c in chapters if c["number"] <= end_chapter]

    out_dir = Path(output_dir) if output_dir else rewrite_dir / "novel"
    out_dir.mkdir(parents=True, exist_ok=True)

    client = create_client(model=model or DEFAULT_MODEL)
    total = len(chapters)
    print(f"  开始写作 {total} 章...")

    for i, ch in enumerate(chapters):
        ch_num = ch["number"]
        outline = ch.get("outline", ch.get("content", ""))
        if not outline:
            print(f"  [{i+1}/{total}] 第{ch_num}章 — 跳过（无内容）")
            continue

        # Get characters for this chapter
        ch_chars = _extract_chapter_characters(ch, characters_text)

        prompt = _load_prompt("write_chapter.md").format(
            outline=outline,
            characters=ch_chars or characters_text[:2000],
        )

        print(f"  [{i+1}/{total}] 第{ch_num}章 {ch.get('title', '')}...", end=" ", flush=True)

        try:
            response = await client.analyze(
                system_prompt="你是专业网文作家。每句话都要推进剧情，拒绝无效描写。对话为主，描写精简到极致。一句一段。只输出正文。",
                user_message=prompt,
                max_tokens=2400,  # DeepSeek: ~1 token ≈ 1 汉字, 目标2000-2300字
                temperature=0.8,
                layer=100,
                batch_id=ch_num,
            )
            text = _extract_text(response)
            text = _format_prose(text)
            word_count = len(text.replace(" ", "").replace("\n", ""))
            out_file = out_dir / f"第{ch_num:04d}章_{ch.get('title', '')}.md"
            out_file.write_text(text.strip(), encoding="utf-8")
            print(f"✓ ({word_count}字)")
        except Exception as e:
            print(f"✗ {e}")

    usage = client.usage_summary
    print(f"\n  写作完成: {out_dir}")
    print(f"  API: {usage['calls']} calls, {usage['total_tokens']:,} tokens, ${usage['estimated_cost_usd']:.2f}")
    return out_dir


def _parse_outline(text: str) -> list[dict]:
    """Parse chapter outline markdown into list of {number, title, content}."""
    chapters = []
    current = None
    lines = text.split("\n")

    for line in lines:
        # Match "# 第X章" or "## 第X章 标题"
        import re
        m = re.match(r'^#{1,2}\s+第(\d+)章\s*(.*)', line)
        if m:
            if current:
                # Strip trailing --- separator
                ct = current["content"].strip()
                if ct.endswith("---"):
                    ct = ct[:-3].strip()
                current["content"] = ct
                chapters.append(current)
            # Store content without the "#" header line
            current = {
                "number": int(m.group(1)),
                "title": m.group(2).strip(),
                "content": "",
            }
        elif current:
            if line.strip() != "---":  # Skip separator lines
                current["content"] += line

    if current:
        chapters.append(current)

    chapters.sort(key=lambda c: c["number"])
    return chapters


def _extract_chapter_characters(ch: dict, all_chars_text: str) -> str:
    """Extract relevant character profiles for a specific chapter."""
    # Parse character sections from the markdown
    sections = all_chars_text.split("## ")
    result = []
    for section in sections[1:]:  # Skip first empty
        # Each section starts with character name
        lines = section.strip().split("\n")
        if not lines:
            continue
        name = lines[0].strip()
        # Include major characters (those with substantial profiles)
        content = "\n".join(lines)
        if len(content) > 200:  # Major character
            result.append(f"## {section.strip()}")
    return "\n\n".join(result[:10])  # Top 10 characters


def _format_prose(text: str) -> str:
    """Format prose: one sentence per line, dialogue on its own line.

    Splits on Chinese punctuation (。！？), then re-joins with double newlines.
    Dialogue markers (「」『』""'') stay attached to their sentences.
    """
    import re
    # Collapse existing whitespace
    text = re.sub(r'\s+', '', text)
    # Split on sentence-ending punctuation, keeping the punctuation
    sentences = re.split(r'(?<=[。！？])', text)
    # Filter empty, join with double newlines
    result = []
    for s in sentences:
        s = s.strip()
        if s:
            result.append(s)
    return '\n\n'.join(result)


def _extract_text(response) -> str:
    if hasattr(response, 'choices'):
        return response.choices[0].message.content or ""
    elif hasattr(response, 'content'):
        blocks = [b.text for b in response.content if hasattr(b, 'text')]
        return "\n".join(blocks)
    return str(response)
