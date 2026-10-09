"""The page check (ADR-011, rule 2): web text goes to `guard-injection` before it enters a prompt.

The classifier splits each text into windows of 512 tokens and gives a score for each window.
We replace each window above the threshold with a marker. The check fails closed: if the
classifier is not available, the caller gets an exception and uses no page text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

MARKER = "[removed by the page check: possible prompt injection]"


class PageCheckUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CheckedText:
    text: str
    removed: int
    score: float


def remove_spans(text: str, spans: Sequence[tuple[int, int]]) -> tuple[str, int]:
    """Merge the overlapping spans and replace each merged span with the marker."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out, last = [], 0
    for start, end in merged:
        out.append(text[last:start])
        out.append(MARKER)
        last = end
    out.append(text[last:])
    return "".join(out), len(merged)


class PageGuard:
    def __init__(self, base_url: str, *, timeout_s: float = 5.0, max_chars: int = 40000,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport)
        self.max_chars = max_chars

    async def aclose(self) -> None:
        await self.http.aclose()

    async def check_many(self, texts: Sequence[str]) -> list[CheckedText]:
        if not texts:
            return []
        texts = [t[: self.max_chars] for t in texts]
        try:
            resp = await self.http.post("/v1/classify", json={"texts": list(texts)})
        except httpx.HTTPError as exc:
            raise PageCheckUnavailable(f"guard-injection: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise PageCheckUnavailable(f"guard-injection: HTTP {resp.status_code}")
        data = resp.json()
        threshold = float(data.get("threshold", 0.5))
        results = data.get("results") or []
        if len(results) != len(texts):
            raise PageCheckUnavailable("guard-injection: wrong number of results")
        out = []
        for text, res in zip(texts, results, strict=True):
            spans = [(w["start"], w["end"]) for w in res.get("windows", []) if w["score"] >= threshold]
            clean, removed = remove_spans(text, spans) if spans else (text, 0)
            out.append(CheckedText(clean, removed, float(res.get("score", 0.0))))
        return out

    async def check(self, text: str) -> CheckedText:
        return (await self.check_many([text]))[0]
