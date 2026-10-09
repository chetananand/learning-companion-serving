"""Read the Notion Bookmarks data source (FR-01). Read-only: GET and query calls only.

API version 2025-09-03: POST /v1/data_sources/{id}/query and GET /v1/blocks/{id}/children.
The client keeps about 3 requests each second (the Notion limit) and obeys Retry-After on 429.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://api.notion.com/v1"


@dataclass(frozen=True)
class Bookmark:
    id: str
    title: str
    url: str | None
    created: dt.date | None
    updated: str  # last_edited_time, compared as a string (FR-03)
    tags: tuple[str, ...] = ()
    github: str | None = None
    youtube: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False)


def _plain(rich: list[dict[str, Any]] | None) -> str:
    return "".join(r.get("plain_text", "") for r in rich or [])


def _date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_row(page: dict[str, Any]) -> Bookmark:
    props = page.get("properties", {})

    def prop(name: str) -> dict[str, Any]:
        return props.get(name) or {}

    title = _plain(prop("Name").get("title"))
    created = prop("Created").get("created_time") or page.get("created_time")
    updated = prop("Updated").get("last_edited_time") or page.get("last_edited_time") or ""
    tags = tuple(o.get("name", "") for o in prop("Tags").get("multi_select") or [])
    return Bookmark(id=page["id"], title=title.strip() or "(no title)", url=prop("URL").get("url") or None,
                    created=_date(created), updated=updated, tags=tags,
                    github=_plain(prop("Github").get("rich_text")) or None, youtube=prop("Youtube").get("url"))


TEXT_BLOCKS = {"paragraph": "", "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
               "bulleted_list_item": "- ", "numbered_list_item": "1. ", "to_do": "- [ ] ", "toggle": "",
               "quote": "> ", "callout": ""}


def block_text(block: dict[str, Any]) -> str:
    kind = block.get("type", "")
    data = block.get(kind) or {}
    if kind in TEXT_BLOCKS:
        return TEXT_BLOCKS[kind] + _plain(data.get("rich_text"))
    if kind == "code":
        return f"```{data.get('language', '')}\n{_plain(data.get('rich_text'))}\n```"
    if kind in ("bookmark", "embed", "link_preview"):
        return data.get("url", "")
    if kind in ("image", "video", "file", "pdf"):
        return _plain(data.get("caption"))
    if kind == "child_page":
        return f"## {data.get('title', '')}"
    if kind == "table_row":
        return " | ".join(_plain(cell) for cell in data.get("cells", []))
    if kind == "equation":
        return data.get("expression", "")
    return ""


class NotionClient:
    def __init__(self, api_key: str, *, version: str = "2025-09-03", interval_s: float = 0.35,
                 transport: httpx.AsyncBaseTransport | None = None, base_url: str = BASE_URL) -> None:
        if not api_key:
            raise ValueError("NOTION_API_KEY is empty")
        self.http = httpx.AsyncClient(base_url=base_url, timeout=30.0, transport=transport, headers={
            "Authorization": f"Bearer {api_key}", "Notion-Version": version, "Content-Type": "application/json"})
        self.interval_s = interval_s
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _call(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        for _ in range(6):
            async with self._lock:
                wait = self._last + self.interval_s - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last = time.monotonic()
            resp = await self.http.request(method, path, **kw)
            if resp.status_code == 429 or resp.status_code >= 500:
                await asyncio.sleep(min(float(resp.headers.get("retry-after", "2") or 2), 30.0))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Notion {path}: too many retries")

    async def query_rows(self, data_source_id: str) -> AsyncIterator[Bookmark]:
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            data = await self._call("POST", f"/data_sources/{data_source_id}/query", json=body)
            for page in data.get("results", []):
                if page.get("object") == "page" and not page.get("in_trash") and not page.get("archived"):
                    yield parse_row(page)
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")

    async def page_text(self, block_id: str, *, depth: int = 2) -> str:
        lines: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = await self._call("GET", f"/blocks/{block_id}/children", params=params)
            for block in data.get("results", []):
                if text := block_text(block).strip():
                    lines.append(text)
                nested = block.get("has_children") and depth > 1 and block.get("type") != "child_page"
                if nested and (child := await self.page_text(block["id"], depth=depth - 1)):
                    lines.append(child)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return "\n\n".join(lines)
