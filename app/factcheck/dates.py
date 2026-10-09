"""Page dates for the fact check (product spec section 6, rule 7).

Order: JSON-LD dateModified or datePublished, then the meta tags, then a visible
date near the title. We never use the HTTP Last-Modified header, because dynamic
pages send the current time.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from html.parser import HTMLParser

_META_KEYS = (
    "article:modified_time", "og:updated_time", "dateModified",
    "article:published_time", "datePublished", "date", "dc.date", "dcterms.modified", "dcterms.created",
)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})")
_LONG = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
                   re.I)
_DMY = re.compile(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?,?\s+(\d{4})\b",
                  re.I)


def parse_date(text: str | None) -> dt.date | None:
    if not text:
        return None
    text = text.strip()
    for pattern, order in ((_ISO, "ymd"), (_LONG, "mdy"), (_DMY, "dmy")):
        m = pattern.search(text)
        if not m:
            continue
        try:
            if order == "ymd":
                return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if order == "mdy":
                return dt.date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
            return dt.date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        except ValueError:
            continue
    return None


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.meta: dict[str, str] = {}
        self.time_values: list[str] = []
        self.text: list[str] = []
        self._in_jsonld = False
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self.jsonld.append("")
        elif tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop") or ""
            if key and a.get("content"):
                self.meta.setdefault(key.lower(), a["content"])
        elif tag == "time" and a.get("datetime"):
            self.time_values.append(a["datetime"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
        elif tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self.jsonld[-1] += data
        elif not self._skip and data.strip():
            self.text.append(data.strip())


def _walk_jsonld(node: object, key: str) -> str | None:
    if isinstance(node, dict):
        if isinstance(node.get(key), str):
            return node[key]
        for value in node.values():
            found = _walk_jsonld(value, key)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_jsonld(item, key)
            if found:
                return found
    return None


def page_date(html: str, *, visible_chars: int = 2000) -> dt.date | None:
    """Return the content date of a page, or None when the page gives no date."""
    c = _Collector()
    c.feed(html)
    for key in ("dateModified", "datePublished"):
        for block in c.jsonld:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            found = parse_date(_walk_jsonld(data, key))
            if found:
                return found
    for key in _META_KEYS:
        found = parse_date(c.meta.get(key.lower()))
        if found:
            return found
    for value in c.time_values:
        found = parse_date(value)
        if found:
            return found
    return parse_date(" ".join(c.text)[:visible_chars])
