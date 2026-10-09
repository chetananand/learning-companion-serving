"""Split Markdown into chunks of 500 to 800 tokens at headings, with 64 tokens of overlap (ADR-007).

A token is about 4 characters for this purpose. The chunker does not need the exact count:
the budget of a prompt comes from `edge`, which counts with the model tokenizer.
"""

from __future__ import annotations

import re

CHARS_PER_TOKEN = 4
_HEADING = re.compile(r"^#{1,6}\s")


def sections(markdown: str) -> list[str]:
    """Split at Markdown headings. Each section starts with its heading."""
    out: list[list[str]] = [[]]
    for line in markdown.splitlines():
        if _HEADING.match(line) and any(x.strip() for x in out[-1]):
            out.append([])
        out[-1].append(line)
    return [s for s in ("\n".join(p).strip() for p in out) if s]


def _hard_split(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def chunk_markdown(markdown: str, *, min_tokens: int = 500, max_tokens: int = 800,
                   overlap_tokens: int = 64) -> list[str]:
    max_chars, min_chars = max_tokens * CHARS_PER_TOKEN, min_tokens * CHARS_PER_TOKEN
    overlap = overlap_tokens * CHARS_PER_TOKEN
    pieces: list[str] = []
    for section in sections(markdown):
        paras = [p.strip() for p in re.split(r"\n\s*\n", section) if p.strip()]
        for p in paras:
            pieces.extend(_hard_split(p, max_chars) if len(p) > max_chars else [p])
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        starts_section = bool(_HEADING.match(piece))
        if current and (len(current) + len(piece) + 2 > max_chars or (starts_section and len(current) >= min_chars)):
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            cut = tail.find(" ")
            current = (tail[cut + 1:] if cut >= 0 else tail) + "\n\n" + piece if tail else piece
            if len(current) > max_chars:
                current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current.strip():
        chunks.append(current)
    return [c.strip() for c in chunks if c.strip()]
