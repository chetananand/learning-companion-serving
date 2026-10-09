"""The agent tools (product spec, section 5). Each tool records its step for the UI and the metrics.

The tools read and write the TurnState of the current turn: the chunks, the search URLs
(the fetch allow list), and the pages. Only pages in the TurnState count as evidence for rule F2.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.clients.browser import BrowserError
from app.clients.fetcher import FetchError
from app.clients.pageguard import PageCheckUnavailable, PageGuard
from app.clients.sie import SieError
from app.clients.websearch import SearchError, SearchProvider
from app.config import AppSettings
from app.factcheck.resolve import Tiers, default_tiers, registered_domain
from app.metrics import AppMetrics
from app.pages import PageReader, focus_text
from app.prompts import page_block
from app.retrieval import Retriever
from app.turn import Chunk, Page, TurnState, current_turn, normalize_url

ANSWER_NOW = "answer_now"
ANSWER_OK = "Write the answer now, without tools."
# Session 1 demo (D-07, D-09): for a question about the current state, the quick agent answered from old chunks and
# did not read the live page, although rule 2 says so. So its answer_now refuses one time, in code.
LIVE_FETCH = ("Not yet. The question asks about the current state, and the saved chunks can be old. "
              "Call fetch_page for the bookmark URL first. Then call answer_now again.")
TIME_WORDS = re.compile(r"\b(now|current|currently|latest|newest|today|up[- ]to[- ]date|recent|recently)\b", re.I)
# Demo D-08: for a question about a figure, the quick agent answered from the page text and did not read the figure.
FIGURE_SHOT = ("Not yet. The question asks about a figure or an image. Call screenshot_page with the bookmark URL and "
               "a text near the figure. Then call answer_now again.")
FIGURE_WORDS = re.compile(r"\b(figure|figures|diagram|chart|image|screenshot|picture|plot|graph)\b", re.I)
REFUSALS = (LIVE_FETCH, FIGURE_SHOT)  # answer_now results that do not close the tools


def needs_figure(turn: TurnState | None) -> bool:
    """A question about a figure, no figure read yet (OCR), and no refusal for it yet in this turn."""
    return (turn is not None and turn.mode in ("quick", "verified") and not turn.figure_asked
            and not any(p.via == "ocr" for p in turn.pages.values()) and bool(FIGURE_WORDS.search(turn.question)))


def needs_live_page(turn: TurnState | None) -> bool:
    """A question about the current state, no page read yet, and no refusal yet in this turn."""
    return (turn is not None and turn.mode in ("quick", "verified") and not turn.pages
            and not turn.live_fetch_asked and bool(TIME_WORDS.search(turn.question)))
NO_PAGE_CHECK = "Error: the page check is not available, so no page text is used (fail closed)."


def _turn() -> TurnState:
    turn = current_turn()
    if turn is None:
        raise RuntimeError("a tool ran outside of a turn")
    return turn


def format_chunk(c: Chunk) -> str:
    saved = c.saved.isoformat() if c.saved else "unknown"
    page = c.page_date.isoformat() if c.page_date else "unknown"
    return f"[{c.ref}] {c.title}\nurl: {c.url} | saved: {saved} | page date: {page}\n{c.text.strip()}"


class SearchArgs(BaseModel):
    query: str = Field(description="A short search query.")


class FetchArgs(BaseModel):
    url: str = Field(description="A URL from the search results or from the bookmarks.")
    focus: str = Field(default="", description="The claim or the topic. The tool returns the parts that match it.")


class ScreenshotArgs(BaseModel):
    url: str = Field(description="A URL from the search results or from the bookmarks.")
    near_text: str = Field(default="", description="A text near the figure, for example its caption. Empty: page.")


class NoArgs(BaseModel):
    pass


@dataclass
class Toolbox:
    settings: AppSettings
    retriever: Retriever
    search: SearchProvider
    reader: PageReader
    guard: PageGuard
    metrics: AppMetrics
    tiers: Tiers | None = None

    def __post_init__(self) -> None:
        self.tiers = self.tiers or default_tiers()

    @asynccontextmanager
    async def step(self, name: str, detail: str) -> AsyncIterator[None]:
        turn = _turn()
        turn.emit("step", name=name, detail=detail[:200], status="start")
        t0 = time.monotonic()
        status = "done"
        try:
            yield
        except Exception:
            status = "error"  # the caller counts the error with its reason
            raise
        finally:
            dt = time.monotonic() - t0
            self.metrics.tool_seconds.labels(name).observe(dt)
            turn.emit("step", name=name, detail=detail[:200], status=status, ms=int(dt * 1000))

    def error(self, tool: str, reason: str, text: str) -> str:
        self.metrics.tool_errors.labels(tool, reason).inc()
        return text

    # The tools

    async def search_bookmarks(self, query: str) -> str:
        turn = _turn()
        async with self.step("search_bookmarks", query):
            hits = await self.retriever.search(query, self.settings.top_k)
        known = {c.chunk_id: c for c in turn.chunks.values()}
        chunks = []
        for h in sorted(hits, key=lambda h: h.chunk_id):  # layout rule 3: sort by chunk id
            c = known.get(h.chunk_id)
            if c is None:
                c = Chunk(ref=f"B{turn.next_chunk_ref()}", chunk_id=h.chunk_id, bookmark_id=h.bookmark_id,
                          title=h.title, url=h.url, text=h.text, saved=h.created, page_date=h.page_date,
                          score=h.score)
                turn.add_chunks([c])
                turn.emit("source", kind="bookmark", ref=c.ref, title=c.title, url=c.url,
                          saved=c.saved.isoformat() if c.saved else None,
                          page_date=c.page_date.isoformat() if c.page_date else None, dead=h.dead,
                          page_check_removed=h.page_check_removed)
            chunks.append(c)
        if not chunks:
            return "No bookmark matches this query."
        return "\n\n".join(format_chunk(c) for c in chunks)

    async def web_search(self, query: str) -> str:
        turn = _turn()
        try:
            async with self.step("web_search", query):
                results = await self.search.search(query, self.settings.search_max_results)
        except SearchError as exc:
            return self.error("web_search", "search", f"Error: the web search failed ({exc}).")
        if not results:
            return "No results."
        try:
            checked = await self.guard.check_many([f"{r.title}\n{r.snippet}" for r in results])
        except PageCheckUnavailable:
            return self.error("web_search", "page_check", NO_PAGE_CHECK)
        lines = []
        for i, (r, c) in enumerate(zip(results, checked, strict=True), start=1):
            turn.search_urls.add(normalize_url(r.url))
            if c.removed:
                self.metrics.page_check_removed.labels("web_search").inc(c.removed)
            tier = self.tiers.tier(r.url) if self.tiers else None
            lines.append(f"[{i}] url: {r.url} | published: {r.published or 'unknown'} | tier: {tier or 'deny'}\n"
                         f"{c.text.strip()}")
        return "\n\n".join(lines)

    def _register(self, turn: TurnState, url: str, final_url: str, text: str, date, via: str,
                  removed: int) -> Page:
        page = Page(ref=turn.next_page_ref(), url=url, domain=registered_domain(final_url),
                    tier=self.tiers.tier(final_url) if self.tiers else 3, date=date, text=text, via=via,
                    removed_windows=removed)
        turn.add_page(page)
        turn.emit("source", kind="web", ref=page.ref, url=url, domain=page.domain, tier=page.tier,
                  date=date.isoformat() if date else None, via=via, removed_windows=removed)
        return page

    def _block(self, page: Page) -> str:
        return page_block(page.ref, page.url, page.domain, page.date.isoformat() if page.date else None,
                          page.tier, page.via, page.text)

    async def fetch_page(self, url: str, focus: str = "") -> str:
        turn = _turn()
        if not turn.allowed_url(url):
            return self.error("fetch_page", "not_allowed",
                              "Error: this URL is not in the search results or in the bookmarks. Do not fetch it.")
        if (page := turn.page(url)) is not None:
            return self._block(page)
        max_chars = self.settings.page_max_tokens * 4
        try:
            async with self.step("fetch_page", url):
                read = await self.reader.read(url)
                text = focus_text(read.text, focus, max_chars)
                checked = await self.guard.check(text)
        except FetchError as exc:
            return self.error("fetch_page", exc.reason, f"Error: the fetch failed ({exc.reason}).")
        except (BrowserError, SieError) as exc:
            return self.error("fetch_page", "ocr", f"Error: the OCR stage failed ({type(exc).__name__}).")
        except PageCheckUnavailable:
            return self.error("fetch_page", "page_check", NO_PAGE_CHECK)
        if read.via == "ocr":
            turn.emit("step", name="ocr", detail=url, status="done")
        if checked.removed:
            self.metrics.page_check_removed.labels("fetch_page").inc(checked.removed)
            turn.emit("guard", tool="fetch_page", url=url, removed_windows=checked.removed)
        page = self._register(turn, url, read.final_url, checked.text, read.date, read.via, checked.removed)
        return self._block(page)

    async def screenshot_page(self, url: str, near_text: str = "") -> str:
        turn = _turn()
        if not turn.allowed_url(url):
            return self.error("screenshot_page", "not_allowed",
                              "Error: this URL is not in the search results or in the bookmarks.")
        try:
            async with self.step("ocr", f"{url} near: {near_text}" if near_text else url):
                text = await self.reader.screenshot_text(url, near_text or None)
                checked = await self.guard.check(text[: self.settings.page_max_tokens * 4])
        except (BrowserError, SieError) as exc:
            return self.error("screenshot_page", "ocr", f"Error: the screenshot or the OCR failed ({exc}).")
        except PageCheckUnavailable:
            return self.error("screenshot_page", "page_check", NO_PAGE_CHECK)
        if checked.removed:
            self.metrics.page_check_removed.labels("screenshot_page").inc(checked.removed)
            turn.emit("guard", tool="screenshot_page", url=url, removed_windows=checked.removed)
        existing = turn.page(url)
        if existing is None:
            page = self._register(turn, url, url, checked.text, None, "ocr", checked.removed)
        else:  # keep the fetched page as the evidence, and show the figure text
            page = Page(ref=turn.next_page_ref(), url=url, domain=existing.domain, tier=existing.tier,
                        date=existing.date, text=checked.text, via="ocr", removed_windows=checked.removed)
        return self._block(page)

    @staticmethod
    async def answer_now() -> str:
        return ANSWER_OK

    @staticmethod
    async def quick_answer_now() -> str:
        turn = current_turn()
        if needs_live_page(turn):
            assert turn is not None
            turn.live_fetch_asked = True
            turn.emit("gate", agent="quick", reason="live_fetch")
            return LIVE_FETCH
        if needs_figure(turn):
            assert turn is not None
            turn.figure_asked = True
            turn.emit("gate", agent="quick", reason="figure")
            return FIGURE_SHOT
        return ANSWER_OK

    # LangChain tool objects

    def quick_tools(self) -> list[BaseTool]:
        return [self._tool("search_bookmarks"), self._tool("fetch_page"), self._tool("screenshot_page"),
                self._tool(ANSWER_NOW, fn="quick_answer_now")]

    def verify_tools(self) -> list[BaseTool]:
        return [self._tool("web_search"), self._tool("fetch_page"), self._tool("screenshot_page")]

    def agent_tools(self) -> list[BaseTool]:
        return [self._tool(ANSWER_NOW)]

    def _tool(self, name: str, fn: str | None = None) -> BaseTool:
        specs = {
            "search_bookmarks": (SearchArgs, "Search the bookmarks of the user. Returns chunks with the labels B1, "
                                             "B2, and so on, with the URL and the dates."),
            "web_search": (SearchArgs, "Search the live web. Returns results with the URL, the date, and the "
                                       "source tier (1 is best)."),
            "fetch_page": (FetchArgs, "Read one page from the search results or the bookmarks. Returns the "
                                      "parts that match the focus, with the page date and tier."),
            "screenshot_page": (ScreenshotArgs, "Take a screenshot of a page, or of the figure near a text, and "
                                                "read it with OCR. Use it when the facts are in an image."),
            ANSWER_NOW: (NoArgs, "Call this tool when you have enough evidence. Then write the answer."),
        }
        args, description = specs[name]
        return StructuredTool.from_function(coroutine=getattr(self, fn or name), name=name, description=description,
                                            args_schema=args)
