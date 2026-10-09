"""Read a web page for the agent or for the ingest job.

Order (product spec, sections 6 and 7):
1. Fetch the page (robots.txt, pace, timeout, size limit).
2. Extract the main text with trafilatura (Markdown) and the page date (rule 7).
3. If the text has less than 500 characters, take a screenshot and read it with OCR.
4. The page check runs on the text before the text enters a prompt.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import trafilatura
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.clients.browser import BrowserClient, BrowserError
from app.clients.fetcher import FetchError, PageFetcher, RawPage
from app.clients.pageguard import PageGuard
from app.clients.sie import SieClient, SieError
from app.factcheck.dates import page_date


@dataclass(frozen=True)
class ReadPage:
    url: str
    final_url: str
    text: str
    date: dt.date | None
    via: str  # html | ocr | image | pdf
    truncated: bool = False


def extract_text(html: str, url: str | None = None) -> str:
    text = trafilatura.extract(html, url=url, output_format="markdown", include_tables=True,
                               include_comments=False, favor_recall=True)
    return (text or "").strip()


def pdf_text(data: bytes, max_pages: int = 30) -> str:
    """Text of a PDF (the first pages). A scanned PDF gives little text: OCR of PDFs is P2."""
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages[:max_pages]).strip()
    except (PyPdfError, ValueError, KeyError, OSError) as exc:
        raise FetchError("pdf_unreadable", type(exc).__name__) from exc


_ARXIV_PDF = re.compile(r"^/pdf/(?P<id>[^/?#]+?)(?:\.pdf)?$")


def fetch_url(url: str) -> str:
    """The URL to fetch for a bookmark. An arXiv PDF link reads the abstract page instead."""
    parts = urlsplit(url)
    if (parts.hostname or "").endswith("arxiv.org") and (m := _ARXIV_PDF.match(parts.path)):
        return f"https://arxiv.org/abs/{m.group('id')}"
    return url


_WORD = re.compile(r"[a-z0-9][a-z0-9.\-_]*", re.I)


def split_windows(text: str, size_chars: int = 2000, overlap_chars: int = 200) -> list[str]:
    """Split a text into windows of about 500 tokens (4 characters for each token)."""
    text = text.strip()
    if len(text) <= size_chars:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size_chars)
        if end < len(text):
            cut = text.rfind("\n", start + size_chars // 2, end)
            end = cut if cut > start else end
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return [w for w in out if w]


def focus_text(text: str, focus: str | None, max_chars: int) -> str:
    """Keep the windows that share the most words with `focus`, in page order, up to `max_chars`."""
    windows = split_windows(text)
    if sum(len(w) for w in windows) <= max_chars or not focus:
        return "\n\n".join(windows)[:max_chars]
    terms = {w.lower() for w in _WORD.findall(focus) if len(w) > 2}

    def score(w: str) -> int:
        words = {x.lower() for x in _WORD.findall(w)}
        return len(terms & words)

    ranked = sorted(range(len(windows)), key=lambda i: (-score(windows[i]), i))
    keep, total = [], 0
    for i in ranked:
        if total + len(windows[i]) > max_chars and keep:
            continue
        keep.append(i)
        total += len(windows[i])
    return "\n\n[...]\n\n".join(windows[i][:max_chars] for i in sorted(keep))


class PageReader:
    def __init__(self, fetcher: PageFetcher, *, browser: BrowserClient | None, sie_ocr: SieClient | None,
                 ocr_model: str, guard: PageGuard | None, ocr_min_chars: int = 500) -> None:
        self.fetcher = fetcher
        self.browser = browser
        self.sie_ocr = sie_ocr
        self.ocr_model = ocr_model
        self.guard = guard
        self.ocr_min_chars = ocr_min_chars

    async def ocr_image(self, image: bytes, image_format: str = "png") -> str:
        if self.sie_ocr is None:
            raise SieError("no OCR server")
        return await self.sie_ocr.ocr(self.ocr_model, image, image_format)

    async def screenshot_text(self, url: str, near_text: str | None = None) -> str:
        if self.browser is None:
            raise BrowserError("no browser service")
        png = await self.browser.screenshot(url, near_text=near_text, full_page=near_text is None)
        return await self.ocr_image(png)

    async def read(self, url: str) -> ReadPage:
        raw: RawPage = await self.fetcher.fetch(fetch_url(url))
        if raw.content_type.startswith("image/"):
            fmt = raw.content_type.split("/", 1)[1]
            return ReadPage(url, raw.final_url, await self.ocr_image(raw.body, fmt), None, "image", raw.truncated)
        if raw.content_type == "application/pdf" or raw.body[:5] == b"%PDF-":
            return ReadPage(url, raw.final_url, pdf_text(raw.body), None, "pdf", raw.truncated)
        if not raw.is_html:
            raise FetchError("unsupported_type", raw.content_type)
        html = raw.text()
        text = extract_text(html, raw.final_url)
        date = page_date(html)
        via = "html"
        if len(text) < self.ocr_min_chars and self.browser is not None and self.sie_ocr is not None:
            try:
                ocr = await self.screenshot_text(raw.final_url)
            except (BrowserError, SieError):
                ocr = ""
            if len(ocr) > len(text):
                text, via = ocr, "ocr"
        return ReadPage(url, raw.final_url, text, date, via, raw.truncated)
