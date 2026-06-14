"""Parse a raw novel .txt file into structured RawChapter objects.

Handles:
- Standard chapter format: "第N章 标题"
- Afterwords / author notes (non-numbered, after last chapter)
- Missing chapter detection
- Text cleaning (stripping ads, normalizing whitespace)
"""

import re
from pathlib import Path

from novel_decomp.models.chapter import RawChapter

# Regex for Chinese web novel chapter headers
CHAPTER_PATTERN = re.compile(
    r"^第\s*(\d+)\s*章\s*(.*?)$",
    re.MULTILINE,
)

# Lines to skip (common ad patterns)
AD_PATTERNS = [
    re.compile(p) for p in [
        r"^\s*【.*?】\s*$",
        r"^\s*(求推荐|求收藏|求月票|求订阅).*$",
        r"^\s*ps[：:].*$",
        r"^\s*PS[：:].*$",
        r"^\s*——.*——\s*$",
        r"^\s*本书首发.*$",
        r"^\s*请记住本站.*$",
    ]
]

# Afterword markers
AFTERWORD_PATTERNS = [
    re.compile(p) for p in [
        r"完本感言",
        r"完结感言",
        r"番外",
        r"后记",
        r"致谢",
        r"写在最后",
    ]
]


def _clean_line(line: str) -> str:
    """Clean a single line of text."""
    line = line.strip()
    if not line:
        return ""
    # Skip pure ad lines
    for pat in AD_PATTERNS:
        if pat.match(line):
            return ""
    return line


def extract_chapters(file_path: str | Path) -> list[RawChapter]:
    """Extract all chapters from a novel text file.

    Args:
        file_path: Path to the .txt novel file.

    Returns:
        List of RawChapter objects in order.
    """
    file_path = Path(file_path)
    text = file_path.read_text(encoding="utf-8")

    # Find all chapter headers
    matches = list(CHAPTER_PATTERN.finditer(text))
    if not matches:
        raise ValueError(f"No chapter headers found in {file_path}. "
                         f"Expected format: '第X章 标题'")

    chapters: list[RawChapter] = []
    for i, match in enumerate(matches):
        ch_num = int(match.group(1))
        ch_title = match.group(2).strip() if match.group(2) else ""
        start_pos = match.end()  # Content starts after header

        # Content ends at next chapter header or end of file
        if i + 1 < len(matches):
            end_pos = matches[i + 1].start()
        else:
            end_pos = len(text)

        content = text[start_pos:end_pos]

        # Clean content
        lines = content.split("\n")
        cleaned_lines = []
        for line in lines:
            cl = _clean_line(line)
            cleaned_lines.append(cl)

        # Join and normalize whitespace
        cleaned_content = "\n".join(cleaned_lines)
        # Remove excessive blank lines (3+ → 2)
        cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content)
        cleaned_content = cleaned_content.strip()

        char_count = len(cleaned_content.replace("\n", "").replace(" ", ""))
        # Rough token estimate: Chinese chars ~1 token each, Latin ~0.25
        est_tokens = int(char_count * 0.6)

        chapters.append(RawChapter(
            index=i + 1,
            number=ch_num,
            title=ch_title,
            content=cleaned_content,
            char_count=char_count,
            estimated_tokens=est_tokens,
            is_afterword=False,
            start_line=match.start(),
        ))

    # Detect missing chapters
    expected = set(range(1, chapters[-1].number + 1))
    actual = {ch.number for ch in chapters}
    missing = expected - actual
    if missing:
        print(f"  ⚠ Missing chapters detected: {sorted(missing)}")

    # Detect afterword (text after last chapter)
    last_match_end = matches[-1].end() if matches else 0
    trailing_text = text[last_match_end:].strip()
    is_afterword = False
    if trailing_text and len(trailing_text) > 100:
        for pat in AFTERWORD_PATTERNS:
            if pat.search(trailing_text[:500]):
                is_afterword = True
                break

        chapters.append(RawChapter(
            index=len(chapters) + 1,
            number=chapters[-1].number + 1 if chapters else 1,
            title="完本感言 / 后记",
            content=trailing_text,
            char_count=len(trailing_text),
            estimated_tokens=int(len(trailing_text) * 0.6),
            is_afterword=True,
            start_line=last_match_end,
        ))

    return chapters


def validate_chapters(chapters: list[RawChapter]) -> list[str]:
    """Validate chapter list and return list of issues.

    Args:
        chapters: List of extracted chapters.

    Returns:
        List of issue strings (empty = valid).
    """
    issues = []

    if not chapters:
        issues.append("No chapters found")
        return issues

    # Check for overlapping chapter numbers
    nums = [ch.number for ch in chapters if not ch.is_afterword]
    if len(nums) != len(set(nums)):
        from collections import Counter
        dupes = [n for n, c in Counter(nums).items() if c > 1]
        issues.append(f"Duplicate chapter numbers: {dupes}")

    # Check chapter sizes
    for ch in chapters:
        if ch.char_count < 200 and not ch.is_afterword:
            issues.append(f"Chapter {ch.number} is very short ({ch.char_count} chars)")
        if ch.char_count > 20_000:
            issues.append(f"Chapter {ch.number} is very long ({ch.char_count} chars)")

    # Check monotonic numbering
    prev = 0
    for ch in chapters:
        if ch.is_afterword:
            continue
        if ch.number <= prev:
            issues.append(f"Non-monotonic chapter numbering: {prev} → {ch.number}")
        prev = ch.number

    return issues
