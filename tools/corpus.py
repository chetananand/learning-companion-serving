"""Real text for the synthetic probes (E4, E5, E14): the chunks in the captured app calls, else the docs.

In the cluster, the probes run as Jobs with the app image, which has no docs/ folder. There the capture
file (APP_CAPTURE_PATH) gives real bookmark chunks and page text, as the app sent them to the model.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def corpus_text(capture: str | None = None, max_chars: int = 800_000) -> str:
    """Long real text. From the capture file when it exists, else from the project docs (tests, laptop)."""
    if capture and Path(capture).exists():
        seen: set[str] = set()
        parts: list[str] = []
        size = 0
        with Path(capture).open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                for m in (json.loads(line).get("body") or {}).get("messages", []):
                    c = m.get("content")
                    if isinstance(c, str) and len(c) > 500 and c not in seen:
                        seen.add(c)
                        parts.append(c)
                        size += len(c)
                if size >= max_chars:
                    break
        if parts:
            return "\n\n".join(parts)
    docs = sorted(p for p in (ROOT / "docs").rglob("*.md") if "source" not in p.parts)
    return "\n\n".join(p.read_text() for p in docs)[:max_chars]


def long_text(tokens: int, seed: int, text: str, chars_per_token: int = 4) -> str:
    """About `tokens` tokens of `text`, from a start point that the seed sets."""
    size = tokens * chars_per_token
    if not text:
        raise ValueError("no source text")
    start = random.Random(seed).randrange(0, max(1, len(text) - size))
    chunk = text[start:start + size]
    while len(chunk) < size:
        chunk += "\n" + text[: size - len(chunk)]
    return chunk
