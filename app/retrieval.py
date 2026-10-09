"""Hybrid retrieval over the bookmarks (FR-05, ADR-007).

Dense vectors (bge-m3 on SIE) and server-side BM25 in Qdrant, fused with RRF, then
rerank of the top 30 on SIE, and the top k (8) go to the agent.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Any, Protocol

from qdrant_client import AsyncQdrantClient, models

from app.clients.sie import SieClient
from app.config import AppSettings

DENSE = "dense"
BM25 = "bm25"
BM25_MODEL = "Qdrant/bm25"


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    bookmark_id: str
    title: str
    url: str
    text: str
    created: dt.date | None
    page_date: dt.date | None
    score: float = 0.0
    dead: bool = False
    source: str = "page"  # notion | page | ocr | pdf | title
    page_check_removed: int = 0  # windows that the page check removed at ingest (D-12)


def parse_date(value: Any) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def hit_from_payload(payload: dict[str, Any], score: float = 0.0) -> Hit:
    return Hit(
        chunk_id=str(payload.get("chunk_id", "")),
        bookmark_id=str(payload.get("bookmark_id", "")),
        title=str(payload.get("title", "")),
        url=str(payload.get("url", "")),
        text=str(payload.get("text", "")),
        created=parse_date(payload.get("created")),
        page_date=parse_date(payload.get("page_date")),
        score=score,
        dead=bool(payload.get("dead", False)),
        source=str(payload.get("source", "page")),
        page_check_removed=int(payload.get("page_check_removed") or 0),
    )


class Retriever(Protocol):
    async def search(self, query: str, k: int) -> list[Hit]: ...


class HybridRetriever:
    def __init__(self, qdrant: AsyncQdrantClient, sie: SieClient, settings: AppSettings, *,
                 sparse: bool = True) -> None:
        self.qdrant = qdrant
        self.sie = sie
        self.s = settings
        self.sparse = sparse

    async def search(self, query: str, k: int) -> list[Hit]:
        [dense] = await self.sie.embed(self.s.embed_model, [query], is_query=True)
        prefetch = [models.Prefetch(query=dense, using=DENSE, limit=self.s.candidates)]
        if self.sparse:
            prefetch.append(models.Prefetch(query=models.Document(text=query, model=BM25_MODEL), using=BM25,
                                            limit=self.s.candidates))
        res = await self.qdrant.query_points(self.s.collection, prefetch=prefetch,
                                             query=models.FusionQuery(fusion=models.Fusion.RRF),
                                             limit=self.s.candidates, with_payload=True)
        hits = [hit_from_payload(p.payload or {}, p.score) for p in res.points]
        if not hits:
            return []
        scores = await self.sie.rerank(self.s.rerank_model, query, [h.text for h in hits])
        ranked = sorted(zip(scores, range(len(hits)), hits, strict=True), key=lambda x: (-x[0], x[1]))[:k]
        return [replace(h, score=s) for s, _, h in ranked]


async def ensure_collection(qdrant: AsyncQdrantClient, settings: AppSettings, *, sparse: bool = True) -> bool:
    """Create the collection if it does not exist. Return True if it made a new collection."""
    if await qdrant.collection_exists(settings.collection):
        return False
    await qdrant.create_collection(
        settings.collection,
        vectors_config={DENSE: models.VectorParams(size=settings.embed_dim, distance=models.Distance.COSINE)},
        sparse_vectors_config={BM25: models.SparseVectorParams(modifier=models.Modifier.IDF)} if sparse else None,
    )
    for field in ("bookmark_id", "url", "source"):
        await qdrant.create_payload_index(settings.collection, field, models.PayloadSchemaType.KEYWORD)
    return True
