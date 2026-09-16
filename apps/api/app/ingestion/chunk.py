"""Structure-aware chunking (BUILD_FLOW Phase 2).

- Heading boundaries first: markdown (`#`, `##`) and numbered (`1.2 Title`) headings
  start new sections; paragraphs are packed into chunks up to the token target.
- Oversized sections are subdivided with token overlap between adjacent chunks.
- Defaults: target 300-400 tokens/chunk, 50-token overlap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TARGET_TOKENS = 350
MIN_TARGET_TOKENS = 300
MAX_TARGET_TOKENS = 400
OVERLAP_TOKENS = 50

_HEADING_RE = re.compile(r"^(#{1,4}\s+.+|\d+(?:\.\d+)*\.?\s+[A-Z].{2,}|[A-Z][A-Z0-9 ,&'’\-:;]{8,})$")


@dataclass
class Section:
    heading: str | None
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class Chunk:
    content: str
    section_heading: str | None
    token_count: int
    page_start: int | None = None
    page_end: int | None = None


def estimate_tokens(text: str) -> int:
    """Cheap estimator (~4 chars/token). Pass an explicit counter for exactness."""
    return max(1, len(text) // 4)


def split_sections(text: str) -> list[Section]:
    """Split cleaned text into sections on heading lines; preamble has heading None."""
    sections = [Section(heading=None)]
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if _HEADING_RE.match(line):
            heading = re.sub(r"^#{1,4}\s+", "", line).strip()
            sections.append(Section(heading=heading or None))
        else:
            sections[-1].paragraphs.append(line)
    return [s for s in sections if s.paragraphs or s.heading]


def _split_with_overlap(
    tokens_words: list[str],
    target: int,
    overlap: int,
    count_tokens,
) -> list[str]:
    """Greedy word-window split on an oversized paragraph with overlap."""
    out: list[str] = []
    start = 0
    n = len(tokens_words)
    while start < n:
        end = start
        # Grow window until token target reached.
        while end < n and count_tokens(" ".join(tokens_words[start : end + 1])) <= target:
            end += 1
        if end == start:  # single word exceeds target; emit it alone
            end = start + 1
        out.append(" ".join(tokens_words[start:end]))
        if end >= n:
            break
        # Step back by overlap (in words, approximated via token ratio).
        step = max(1, (end - start) - max(1, overlap * (end - start) // max(1, count_tokens(" ".join(tokens_words[start:end])))))
        start = start + step
        if start >= end:  # safety: always progress
            start = end - max(0, min(overlap, end - 1)) if end > 1 else end
    return out


def chunk_text(
    text: str,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    count_tokens=estimate_tokens,
) -> list[Chunk]:
    """Chunk cleaned text. Returns chunks with content, heading, token counts."""
    chunks: list[Chunk] = []
    for section in split_sections(text):
        buf: list[str] = []
        buf_tokens = 0
        for para in section.paragraphs:
            para_tokens = count_tokens(para)
            if para_tokens > target_tokens:
                if buf:
                    content = "\n".join(buf)
                    chunks.append(Chunk(content=content, section_heading=section.heading, token_count=count_tokens(content)))
                    buf, buf_tokens = [], 0
                for piece in _split_with_overlap(para.split(), target_tokens, overlap_tokens, count_tokens):
                    chunks.append(
                        Chunk(content=piece, section_heading=section.heading, token_count=count_tokens(piece))
                    )
                continue
            if buf and buf_tokens + para_tokens > target_tokens:
                content = "\n".join(buf)
                chunks.append(Chunk(content=content, section_heading=section.heading, token_count=count_tokens(content)))
                # Overlap: carry the last paragraph forward when it fits the overlap budget.
                carry = [buf[-1]] if count_tokens(buf[-1]) <= overlap_tokens else []
                buf = carry
                buf_tokens = sum(count_tokens(p) for p in buf)
            buf.append(para)
            buf_tokens += para_tokens
        if buf:
            content = "\n".join(buf)
            chunks.append(Chunk(content=content, section_heading=section.heading, token_count=count_tokens(content)))
    return chunks
