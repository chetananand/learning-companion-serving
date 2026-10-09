"""Token counts for the guard rules. The model tokenizer if it is present, else an estimate."""

from __future__ import annotations

import os
from collections.abc import Callable


def estimate_tokens(text: str) -> int:
    """About 4 characters for each token. Used only when the tokenizer file is not present."""
    return (len(text) + 3) // 4 if text else 0


def make_counter(tokenizer_path: str | None = None) -> Callable[[str], int]:
    path = tokenizer_path or os.environ.get("EDGE_TOKENIZER_PATH", "")
    if path and os.path.exists(path):
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(path)
        return lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids) if text else 0
    return estimate_tokens
