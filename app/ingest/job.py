"""The ingest job (FR-01 to FR-04, index sync in the product spec, section 8).

A batch job on the data plane. It makes no LLM call:
1. Read the rows of the Notion Bookmarks data source, and the Notion page body of each row.
2. Fetch the bookmark URL. Extract the text and the page date. Use OCR when the text is short.
3. Split the text into chunks. The page check runs on each chunk (ADR-011, rule 2).
4. Embed the chunks on SIE and write them to Qdrant (dense vector and server-side BM25).

FR-03: without --refresh, a row with the same `Updated` time is skipped. With --refresh, the job
fetches each page again and writes only the rows with a new content hash.
FR-04: a dead link gets `dead: true`. The job keeps the Notion text and the title of the row.

Run: python -m app.ingest.job --limit 20 --seeds app/ingest/seeds.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from qdrant_client import AsyncQdrantClient, models

from app.clients.fetcher import FetchError
from app.clients.pageguard import PageCheckUnavailable, PageGuard
from app.clients.sie import SieClient, SieError
from app.config import AppSettings, get_settings
from app.factcheck.resolve import registered_domain
from app.ingest.chunker import chunk_markdown
from app.ingest.notion import Bookmark, NotionClient
from app.pages import PageReader
from app.retrieval import BM25, BM25_MODEL, DENSE, ensure_collection

log = logging.getLogger("companion.ingest")
SKIP_FETCH_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
EMBED_BATCH = 16
CHECK_BATCH = 32


@dataclass
class Doc:
    bookmark: Bookmark
    parts: list[tuple[str, str]] = field(default_factory=list)  # (source, markdown)
    page_date: dt.date | None = None
    dead: bool = False
    dead_reason: str | None = None

    @property
    def content_hash(self) -> str:
        h = hashlib.sha256()
        for source, text in self.parts:
            h.update(source.encode() + b"\0" + text.encode() + b"\0")
        return h.hexdigest()[:24]


def load_seeds(path: str | Path) -> list[Bookmark]:
    rows = yaml.safe_load(Path(path).read_text()) or []
    return [Bookmark(id=f"seed-{r['id']}", title=r["title"], url=r.get("url"),
                     created=dt.date.fromisoformat(str(r["created"])) if r.get("created") else None,
                     updated=str(r.get("updated", r.get("created", ""))), tags=tuple(r.get("tags", [])))
            for r in rows]


def chunk_id(bookmark_id: str, source: str, index: int) -> str:
    return f"{bookmark_id}-{source}-{index:03d}"


def point_id(cid: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, cid))


class Ingest:
    def __init__(self, settings: AppSettings, *, qdrant: AsyncQdrantClient, sie: SieClient, reader: PageReader,
                 guard: PageGuard, notion: NotionClient | None = None, sparse: bool = True,
                 concurrency: int = 6) -> None:
        self.s = settings
        self.qdrant = qdrant
        self.sie = sie
        self.reader = reader
        self.guard = guard
        self.notion = notion
        self.sparse = sparse
        self.sem = asyncio.Semaphore(concurrency)
        self.counts: Counter[str] = Counter()
        self.errors: list[dict[str, Any]] = []

    async def existing(self) -> dict[str, dict[str, Any]]:
        """bookmark_id -> {updated, content_hash} of the points in Qdrant."""
        out: dict[str, dict[str, Any]] = {}
        offset = None
        while True:
            points, offset = await self.qdrant.scroll(self.s.collection, limit=512, offset=offset, with_vectors=False,
                                                      with_payload=["bookmark_id", "updated", "content_hash"])
            for p in points:
                pl = p.payload or {}
                out.setdefault(str(pl.get("bookmark_id")), {"updated": pl.get("updated"),
                                                            "content_hash": pl.get("content_hash")})
            if offset is None:
                return out

    async def collect(self, bm: Bookmark) -> Doc:
        doc = Doc(bm)
        if self.notion is not None and not bm.id.startswith("seed-"):
            try:
                if body := (await self.notion.page_text(bm.id)).strip():
                    doc.parts.append(("notion", body))
            except Exception as exc:  # noqa: BLE001 - the page body is optional; the row still counts
                self.errors.append({"id": bm.id, "stage": "notion_body", "error": type(exc).__name__})
        host = (urlsplit(bm.url).hostname or "").lower() if bm.url else ""
        if bm.url and host not in SKIP_FETCH_HOSTS:
            try:
                page = await self.reader.read(bm.url)
                if page.text.strip():
                    doc.parts.append((page.via if page.via != "html" else "page", page.text))
                doc.page_date = page.date
                self.counts["ocr"] += page.via == "ocr"
            except FetchError as exc:
                doc.dead, doc.dead_reason = exc.dead, exc.reason
                self.errors.append({"id": bm.id, "url": bm.url, "stage": "fetch", "error": exc.reason})
            except SieError as exc:
                self.errors.append({"id": bm.id, "url": bm.url, "stage": "ocr", "error": str(exc)[:100]})
        if not doc.parts:  # keep the row searchable by its title (FR-04)
            doc.parts.append(("title", " ".join([bm.title, *bm.tags, bm.url or ""]).strip()))
        return doc

    async def check(self, texts: list[str]) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for i in range(0, len(texts), CHECK_BATCH):
            checked = await self.guard.check_many(texts[i:i + CHECK_BATCH])
            out += [(c.text, c.removed) for c in checked]
        return out

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            out += await self.sie.embed(self.s.embed_model, texts[i:i + EMBED_BATCH])
        return out

    def payload(self, doc: Doc, cid: str, source: str, text: str, removed: int) -> dict[str, Any]:
        bm = doc.bookmark
        return {"chunk_id": cid, "bookmark_id": bm.id, "title": bm.title, "url": bm.url or "",
                "domain": registered_domain(bm.url) if bm.url else "", "text": text,
                "created": bm.created.isoformat() if bm.created else None, "updated": bm.updated,
                "page_date": doc.page_date.isoformat() if doc.page_date else None, "source": source,
                "content_hash": doc.content_hash, "tags": list(bm.tags), "dead": doc.dead,
                "dead_reason": doc.dead_reason, "page_check_removed": removed}

    async def write(self, doc: Doc) -> int:
        bm = doc.bookmark
        items = [(source, i, c) for source, text in doc.parts for i, c in enumerate(chunk_markdown(text))]
        checked = await self.check([c for _, _, c in items])
        embed_texts = [f"{bm.title}\n\n{text}" for text, _ in checked]
        vectors = await self.embed(embed_texts)
        points = []
        for (source, i, _), (text, removed), vec, etext in zip(items, checked, vectors, embed_texts, strict=True):
            cid = chunk_id(bm.id, source, i)
            vector: dict[str, Any] = {DENSE: vec}
            if self.sparse:
                vector[BM25] = models.Document(text=etext, model=BM25_MODEL)
            points.append(models.PointStruct(id=point_id(cid), vector=vector,
                                             payload=self.payload(doc, cid, source, text, removed)))
            self.counts["removed_windows"] += removed
        await self.qdrant.delete(self.s.collection, points_selector=models.FilterSelector(filter=models.Filter(
            must=[models.FieldCondition(key="bookmark_id", match=models.MatchValue(value=bm.id))])))
        await self.qdrant.upsert(self.s.collection, points=points)
        return len(points)

    async def one(self, bm: Bookmark, known: dict[str, Any] | None, refresh: bool) -> None:
        async with self.sem:
            if known and not refresh and known.get("updated") == bm.updated:
                self.counts["skipped_same_updated"] += 1
                return
            doc = await self.collect(bm)
            if known and known.get("content_hash") == doc.content_hash:
                self.counts["unchanged_hash"] += 1
                return
            try:
                n = await self.write(doc)
            except (PageCheckUnavailable, SieError) as exc:
                self.errors.append({"id": bm.id, "stage": "write", "error": f"{type(exc).__name__}: {exc}"[:200]})
                self.counts["failed"] += 1
                return
            self.counts["ingested"] += 1
            self.counts["chunks"] += n
            self.counts["dead"] += doc.dead

    async def run(self, rows: list[Bookmark], *, refresh: bool = False) -> dict[str, Any]:
        t0 = time.monotonic()
        await ensure_collection(self.qdrant, self.s, sparse=self.sparse)
        known = await self.existing()
        self.counts["rows"] = len(rows)
        await asyncio.gather(*(self.one(bm, known.get(bm.id), refresh) for bm in rows))
        return {"counts": dict(self.counts), "errors": self.errors, "elapsed_s": round(time.monotonic() - t0, 1),
                "finished": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    from app.clients.browser import BrowserClient
    from app.clients.fetcher import PageFetcher

    s = get_settings()
    notion = None if args.no_notion else NotionClient(s.notion_api_key, version=s.notion_version)
    rows: list[Bookmark] = []
    if notion is not None:
        rows = [bm async for bm in notion.query_rows(s.notion_data_source_id)]
    if args.seeds:
        rows += load_seeds(args.seeds)
    if args.limit:
        rows = rows[: args.limit]
    qdrant = AsyncQdrantClient(url=s.qdrant_url, timeout=60)
    sie = SieClient(s.sie_embed_url, timeout_s=s.sie_timeout_s)
    guard = PageGuard(s.injection_url, timeout_s=30.0)
    fetcher = PageFetcher(user_agent=s.user_agent, timeout_s=s.fetch_timeout_s, max_bytes=s.fetch_max_bytes,
                          host_interval_s=s.fetch_domain_interval_s, allow_hosts=frozenset(s.fetch_allow_hosts))
    reader = PageReader(fetcher, browser=BrowserClient(s.browser_url), sie_ocr=SieClient(s.sie_ocr_url, timeout_s=90),
                        ocr_model=s.ocr_model, guard=guard, ocr_min_chars=s.ocr_min_chars)
    job = Ingest(s, qdrant=qdrant, sie=sie, reader=reader, guard=guard, notion=notion, concurrency=args.concurrency)
    try:
        report = await job.run(rows, refresh=args.refresh)
    finally:
        for c in (sie, guard, fetcher, reader.browser, reader.sie_ocr, notion):
            if c is not None:
                await c.aclose()
        await qdrant.close()
    out = Path(args.report_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"ingest-{int(time.time())}.json").write_text(json.dumps(report, indent=2))
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="Ingest the Notion bookmarks into Qdrant")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true", help="fetch all pages again; write only changed rows")
    ap.add_argument("--seeds", help="a YAML file with extra rows, for example the D-12 test page")
    ap.add_argument("--no-notion", action="store_true", help="ingest only the seed rows")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--report-dir", default="/data/ingest")
    report = asyncio.run(main_async(ap.parse_args()))
    print(json.dumps(report["counts"], indent=2))
    return 1 if report["counts"].get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
