"""Live web search for the fact check (ADR-009). One interface, Tavily as the provider.

Results stay in a cache for 24 hours, so eval runs and repeated questions do not pay twice.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published: str | None = None
    score: float | None = None


class SearchError(RuntimeError):
    pass


class Cache(Protocol):
    async def get(self, key: str) -> Any: ...

    async def set(self, key: str, value: str, ex: int | None = None) -> Any: ...


class MemoryCache:
    """A cache for tests and for runs without Redis."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[float, str]] = {}

    async def get(self, key: str) -> str | None:
        item = self.data.get(key)
        if item is None or item[0] < time.time():
            return None
        return item[1]

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = (time.time() + (ex or 10**9), value)


class SearchProvider(Protocol):
    async def search(self, query: str, max_results: int = 5, recency_days: int | None = None) -> list[SearchResult]:
        ...


def _time_range(days: int | None) -> str | None:
    if days is None:
        return None
    for limit, name in ((1, "day"), (7, "week"), (31, "month")):
        if days <= limit:
            return name
    return "year"


class TavilySearch:
    name = "tavily"

    def __init__(self, api_key: str, *, base_url: str = "https://api.tavily.com", timeout_s: float = 15.0,
                 cache: Cache | None = None, cache_ttl_s: int = 24 * 3600,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.api_key = api_key
        self.cache = cache or MemoryCache()
        self.cache_ttl_s = cache_ttl_s
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self.http.aclose()

    @staticmethod
    def cache_key(query: str, max_results: int, recency_days: int | None) -> str:
        raw = json.dumps([" ".join(query.lower().split()), max_results, recency_days])
        return "search:tavily:" + hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def search(self, query: str, max_results: int = 5, recency_days: int | None = None) -> list[SearchResult]:
        key = self.cache_key(query, max_results, recency_days)
        cached = await self.cache.get(key)
        if cached:
            return [SearchResult(**r) for r in json.loads(cached)]
        if not self.api_key:
            raise SearchError("no Tavily API key")
        body: dict[str, Any] = {"query": query, "search_depth": "basic", "max_results": max_results,
                                "include_answer": False, "include_raw_content": False}
        if (tr := _time_range(recency_days)) is not None:
            body["time_range"] = tr
        try:
            resp = await self.http.post("/search", json=body, headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as exc:
            raise SearchError(f"search failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            reason = {429: "usage limit", 432: "plan limit", 433: "plan limit", 401: "bad API key"}.get(
                resp.status_code, "error")
            raise SearchError(f"Tavily HTTP {resp.status_code} ({reason})")
        results = [
            SearchResult(title=(r.get("title") or "").strip(), url=r["url"], snippet=(r.get("content") or "").strip(),
                         published=r.get("published_date"), score=r.get("score"))
            for r in resp.json().get("results", []) if r.get("url")
        ]
        await self.cache.set(key, json.dumps([asdict(r) for r in results]), ex=self.cache_ttl_s)
        return results
