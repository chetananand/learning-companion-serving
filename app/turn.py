"""The state of one user turn: headers, deadline, evidence registries, and UI events.

LangGraph runs each node and each tool call in an asyncio task. A task copies the
context of its parent, so the contextvar below reaches every tool call, every
middleware hook, and the httpx hook of the turn. The TurnState object itself is
shared, so a tool can add evidence that a middleware reads later.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag

from app.factcheck.resolve import Resolution


def normalize_url(url: str) -> str:
    """Remove the fragment and the trailing slash, so one page has one key."""
    return urldefrag(url.strip())[0].rstrip("/")


@dataclass(frozen=True)
class Chunk:
    """One `search_bookmarks` result. `ref` is the citation label (B1 to B8)."""

    ref: str
    chunk_id: str
    bookmark_id: str
    title: str
    url: str
    text: str
    saved: dt.date | None  # the Notion `Created` date
    page_date: dt.date | None  # the date in the page content, if the page gives one
    score: float = 0.0

    @property
    def evidence_date(self) -> dt.date | None:
        """Rule 7: the page date if the page gives one, else the Notion `Created` date."""
        return self.page_date or self.saved


@dataclass(frozen=True)
class Page:
    """One page that `fetch_page` or `screenshot_page` read. Only these pages count as evidence."""

    ref: str
    url: str
    domain: str
    tier: int | None
    date: dt.date | None
    text: str  # the text after the page check
    via: str  # html | ocr
    removed_windows: int = 0


@dataclass(frozen=True)
class ClaimResult:
    claim_id: str
    claim: str
    bookmark_ref: str | None
    resolution: Resolution


@dataclass
class TurnState:
    session_id: str
    tenant: str = "owner"
    request_class: str = "interactive"  # interactive | batch
    mode: str = "quick"  # quick | verified | sweep
    allow_overflow: bool = True
    has_images: bool = False
    deadline_s: float = 30.0
    prompt_version: str = ""
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started: float = field(default_factory=time.monotonic)
    today: dt.date = field(default_factory=lambda: dt.datetime.now(dt.UTC).date())
    chunks: dict[str, Chunk] = field(default_factory=dict)
    search_urls: set[str] = field(default_factory=set)
    pages: dict[str, Page] = field(default_factory=dict)
    claims: list[ClaimResult] = field(default_factory=list)
    fact_checks: int = 0
    events: asyncio.Queue[dict[str, Any]] | None = None
    via: set[str] = field(default_factory=set)  # local, overflow (from the edge response header)
    question: str = ""  # the user question: the live-fetch rule of answer_now reads it
    live_fetch_asked: bool = False  # answer_now refused once for a live page (session 1)
    figure_asked: bool = False  # answer_now refused once for a figure (Wednesday, D-08)

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    def remaining_ms(self) -> int:
        return max(0, int((self.deadline_s - self.elapsed_s()) * 1000))

    def emit(self, event: str, /, **data: Any) -> None:
        if self.events is not None:
            self.events.put_nowait({"event": event, **data})

    # Evidence registries

    def add_chunks(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self.chunks[c.ref] = c

    def next_chunk_ref(self) -> int:
        return len(self.chunks) + 1

    def chunk_for_url(self, url: str) -> Chunk | None:
        key = normalize_url(url)
        return next((c for c in self.chunks.values() if normalize_url(c.url) == key), None)

    def allowed_url(self, url: str) -> bool:
        """Safety rule 2: fetch only URLs from the search results or from the bookmarks."""
        key = normalize_url(url)
        return key in self.search_urls or any(normalize_url(c.url) == key for c in self.chunks.values())

    def add_page(self, page: Page) -> None:
        self.pages[normalize_url(page.url)] = page

    def page(self, url: str) -> Page | None:
        return self.pages.get(normalize_url(url))

    def next_page_ref(self) -> str:
        return f"W{len(self.pages) + 1}"


_TURN: contextvars.ContextVar[TurnState | None] = contextvars.ContextVar("companion_turn", default=None)


def current_turn() -> TurnState | None:
    return _TURN.get()


@contextmanager
def turn_scope(turn: TurnState) -> Iterator[TurnState]:
    token = _TURN.set(turn)
    try:
        yield turn
    finally:
        _TURN.reset(token)
