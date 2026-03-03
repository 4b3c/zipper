"""Shared text utilities."""

import re


def smart_split(text: str, limit: int = 1990) -> list[str]:
    """Split text into chunks ≤ limit chars at natural break points.

    Avoids splitting inside code fences (``` blocks). Prefers paragraph
    breaks > line breaks > sentence ends > word boundaries.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    while len(text) > limit:
        window = text[:limit]

        # Don't split inside a code fence — find the last ``` before limit
        # If the count of ``` in window is odd, we're inside a fence; pull back
        fence_count = window.count("```")
        if fence_count % 2 == 1:
            # Find the last ``` and split before it
            idx = window.rfind("```")
            if idx > 0:
                chunks.append(text[:idx].rstrip())
                text = text[idx:]
                continue

        # Find best split point, preferring natural breaks
        split_at = None
        for sep in ["\n\n", "\n", ". ", " "]:
            idx = window.rfind(sep)
            if idx > limit // 3:
                split_at = idx
                break

        if split_at is None:
            split_at = limit

        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip("\n") if "\n" in text[:split_at + 2] else text[split_at:].lstrip(" ")

    if text.strip():
        chunks.append(text.strip())

    return [c for c in chunks if c]


def title_to_slug(title: str, fallback: str = "item", max_length: int | None = None) -> str:
    """Convert a title to a URL-safe slug."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    if max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or fallback
