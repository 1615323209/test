"""Adaptive batch builder for Layer 2 chapter analysis.

Builds batches that stay within token limits while keeping chapters
(which are logical units) intact.  Scales batch size per chapter length.
"""

from novel_decomp.models.chapter import RawChapter


def build_batches(
    chapters: list[RawChapter],
    target_chapters_per_batch: int = 20,
    max_batch_tokens: int = 60_000,
) -> list[list[RawChapter]]:
    """Group chapters into batches optimized for LLM context windows.

    Strategy:
    - Start with target_chapters_per_batch chapters
    - If total tokens exceed max_batch_tokens, reduce batch size for that batch
    - If individual chapter is > max_batch_tokens, it gets its own batch
    - Afterwords are appended to the last batch

    Args:
        chapters: List of RawChapter objects.
        target_chapters_per_batch: Ideal number of chapters per batch.
        max_batch_tokens: Maximum estimated tokens per batch (input).

    Returns:
        List of chapter batches.
    """
    # Separate afterword
    regular = [ch for ch in chapters if not ch.is_afterword]
    afterwords = [ch for ch in chapters if ch.is_afterword]

    batches: list[list[RawChapter]] = []
    i = 0

    while i < len(regular):
        # Start with target size
        end = min(i + target_chapters_per_batch, len(regular))
        candidate = regular[i:end]
        total_tokens = sum(ch.estimated_tokens for ch in candidate)

        # If over limit, shrink one chapter at a time
        while total_tokens > max_batch_tokens and len(candidate) > 1:
            end -= 1
            candidate = regular[i:end]
            total_tokens = sum(ch.estimated_tokens for ch in candidate)

        # If a single chapter exceeds limit, warn and put it alone
        if total_tokens > max_batch_tokens:
            print(f"  ⚠ Chapter {candidate[0].number} alone exceeds token limit "
                  f"({total_tokens} > {max_batch_tokens})")

        batches.append(candidate)
        i = end

    # Append afterword to last batch if present
    if afterwords and batches:
        batches[-1].extend(afterwords)
    elif afterwords:
        batches.append(afterwords)

    return batches


def get_batch_stats(batches: list[list[RawChapter]]) -> dict:
    """Compute statistics about batches.

    Args:
        batches: List of chapter batches.

    Returns:
        Dictionary with batch statistics.
    """
    sizes = [len(b) for b in batches]
    tokens = [sum(ch.estimated_tokens for ch in b) for b in batches]

    return {
        "total_batches": len(batches),
        "total_chapters": sum(sizes),
        "avg_chapters_per_batch": sum(sizes) / len(batches) if batches else 0,
        "min_chapters": min(sizes) if sizes else 0,
        "max_chapters": max(sizes) if sizes else 0,
        "avg_tokens_per_batch": sum(tokens) / len(batches) if batches else 0,
        "min_tokens": min(tokens) if tokens else 0,
        "max_tokens": max(tokens) if tokens else 0,
        "total_tokens": sum(tokens),
    }
