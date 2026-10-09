"""Fact-check rule F2: when does a live fact win? (ADR-012, product spec section 6).

Code decides, not an LLM judge. The LLM only gives a verdict for each page. This
module applies the rules to the verdicts, the page dates, and the source tiers.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

import tldextract
import yaml

TIERS_FILE = Path(__file__).with_name("tiers.yaml")
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())  # bundled public suffix list, no network


class Verdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    OUTDATED = "OUTDATED"
    NOT_FOUND = "NOT_FOUND"
    UNCLEAR = "UNCLEAR"


class Status(StrEnum):
    VERIFIED = "verified"
    UPDATED = "updated"
    DISPUTED = "disputed"
    NOT_VERIFIED = "not_verified"


@dataclass(frozen=True)
class Source:
    url: str
    date: dt.date | None  # content date; for a bookmark: page date, else the Notion Created date
    text: str = ""  # the quote or the page text (for the near-copy check)


@dataclass(frozen=True)
class Finding:
    source: Source
    verdict: Verdict
    correction: str | None = None  # the corrected value, for CONTRADICTED or OUTDATED


@dataclass(frozen=True)
class Resolution:
    status: Status
    path: str  # supported | tier | two_domains | blocked_by_support | live_conflict | no_rule | no_evidence
    winner: Finding | None
    citations: tuple[Finding, ...]
    reason: str


def registered_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    ext = _EXTRACT(host)
    return ".".join(p for p in (ext.domain, ext.suffix) if p) or host


class Tiers:
    def __init__(self, tier1_hosts: Iterable[str], tier1_github_orgs: Iterable[str],
                 tier2_hosts: Iterable[str], deny_hosts: Iterable[str]) -> None:
        self.tier1 = {h.lower() for h in tier1_hosts}
        self.github_orgs = {o.lower() for o in tier1_github_orgs}
        self.tier2 = {h.lower() for h in tier2_hosts}
        self.deny = {h.lower() for h in deny_hosts}

    @classmethod
    def load(cls, path: Path = TIERS_FILE) -> Tiers:
        data = yaml.safe_load(path.read_text())
        return cls(data.get("tier1_hosts", []), data.get("tier1_github_orgs", []),
                   data.get("tier2_hosts", []), data.get("deny_hosts", []))

    @staticmethod
    def _matches(host: str, names: set[str]) -> bool:
        return any(host == n or host.endswith("." + n) for n in names)

    def tier(self, url: str) -> int | None:
        """Return 1, 2, or 3. Return None for a page on the deny list (rule 11)."""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if self._matches(host, self.deny):
            return None
        if host in ("github.com", "www.github.com"):
            org = parsed.path.strip("/").split("/", 1)[0].lower()
            return 1 if org in self.github_orgs else 3
        if self._matches(host, self.tier1):
            return 1
        if self._matches(host, self.tier2):
            return 2
        return 3


@cache
def default_tiers() -> Tiers:
    return Tiers.load()


def normalize_value(value: str | None) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip().lower()).rstrip(".;,")
    return re.sub(r"(?<![a-z0-9])v(?=\d)", "", text)  # "v0.30.0" and "0.30.0" are the same value


def _shingles(text: str, size: int = 5) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    if len(words) < size:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}


def near_copy(a: str, b: str, threshold: float = 0.8) -> bool:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return False
    return len(sa & sb) / len(sa | sb) >= threshold


def independent_count(findings: Sequence[Finding]) -> int:
    """Count independent sources: different registered domains, and no near copies (rule 6)."""
    groups: list[list[Finding]] = []
    for f in findings:
        domain = registered_domain(f.source.url)
        for group in groups:
            same_domain = registered_domain(group[0].source.url) == domain
            copied = any(near_copy(f.source.text, g.source.text) for g in group)
            if same_domain or copied:
                group.append(f)
                break
        else:
            groups.append([f])
    return len(groups)


def resolve(bookmark: Source, findings: Sequence[Finding], *, today: dt.date,
            tiers: Tiers | None = None) -> Resolution:
    """Apply rules 1 to 11 of the product spec, section 6, to one claim."""
    tiers = tiers or default_tiers()
    tier_of = {id(f): tiers.tier(f.source.url) for f in findings}
    live = [f for f in findings if tier_of[id(f)] is not None]
    bookmark_tier = tiers.tier(bookmark.url) or 3

    def tier(f: Finding) -> int:
        return tier_of[id(f)] or 3

    def effective_date(f: Finding) -> dt.date | None:
        # Rule 4: a tier-1 page with no date counts as current.
        if f.source.date is None:
            return today if tier(f) == 1 else None
        return f.source.date

    def not_older_than_bookmark(f: Finding) -> bool:
        d = effective_date(f)
        if d is None:
            return False  # rule 7: an unknown date is never newer
        return bookmark.date is None or d >= bookmark.date

    supports = [f for f in live if f.verdict is Verdict.SUPPORTED]
    corrections = [f for f in live if f.verdict in (Verdict.CONTRADICTED, Verdict.OUTDATED)]
    eligible = [f for f in corrections if not_older_than_bookmark(f)]

    candidates: list[tuple[Finding, str]] = [(f, "tier") for f in eligible if tier(f) <= bookmark_tier]
    by_value: dict[str, list[Finding]] = {}
    for f in eligible:
        by_value.setdefault(normalize_value(f.correction), []).append(f)
    for value, group in by_value.items():
        if value and independent_count(group) >= 2:
            best = min(group, key=lambda g: (tier(g), -(effective_date(g) or dt.date.min).toordinal()))
            candidates.append((best, "two_domains"))

    if candidates:
        candidates.sort(key=lambda c: (tier(c[0]), -(effective_date(c[0]) or dt.date.min).toordinal()))
        top, path = candidates[0]
        top_value = normalize_value(top.correction)
        rivals = [c for c, _ in candidates if tier(c) == tier(top) and normalize_value(c.correction) != top_value]
        if rivals:
            return Resolution(Status.DISPUTED, "live_conflict", None, (top, rivals[0]),
                              "two live pages of the same tier give different corrections")
        top_date = effective_date(top)
        blockers = [s for s in supports
                    if tier(s) <= tier(top) and (effective_date(s) or dt.date.min) >= (top_date or dt.date.min)]
        if blockers:
            return Resolution(Status.DISPUTED, "blocked_by_support", None, (top, blockers[0]),
                              "a newer page of the same or a better tier still supports the claim")
        return Resolution(Status.UPDATED, path, top, (top,),
                          "tier rule" if path == "tier" else "two independent domains agree")

    if supports:
        best = min(supports, key=lambda s: (tier(s), -(effective_date(s) or dt.date.min).toordinal()))
        return Resolution(Status.VERIFIED, "supported", best, (best,), "a live page supports the claim")
    if corrections:
        return Resolution(Status.DISPUTED, "no_rule", None, tuple(corrections),
                          "a live page disagrees, but no rule lets it win")
    return Resolution(Status.NOT_VERIFIED, "no_evidence", None, (), "the live pages do not show the claim")
