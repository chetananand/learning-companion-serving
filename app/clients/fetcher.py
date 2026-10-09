"""The page fetcher (product spec, section 6, safety rules 2, 3, and 6).

- Only http and https. No address in a private, loopback, or link-local network (no SSRF).
  An allow list of hosts can open one host for the in-cluster test page (D-12).
- Obey robots.txt (RFC 9309: a 4xx robots file allows all, a 5xx or no answer disallows all).
- One request each second to a host at most. An 8 s timeout. A 2 MB limit.
- GET only. No forms, no cookies, no log in.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx


class FetchError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason

    @property
    def dead(self) -> bool:
        """A dead link (FR-04): the page is gone or the host does not exist."""
        return self.reason in ("http_404", "http_410", "dns")


@dataclass(frozen=True)
class RawPage:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    truncated: bool

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type or self.content_type == ""

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def _blocked_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
            or addr.is_reserved or addr.is_unspecified)


class PageFetcher:
    def __init__(self, *, user_agent: str, timeout_s: float = 8.0, max_bytes: int = 2 * 1024 * 1024,
                 host_interval_s: float = 1.0, allow_hosts: frozenset[str] = frozenset(),
                 transport: httpx.AsyncBaseTransport | None = None, resolve_dns: bool = True,
                 max_redirects: int = 5) -> None:
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.host_interval_s = host_interval_s
        self.allow_hosts = allow_hosts
        self.resolve_dns = resolve_dns
        self.max_redirects = max_redirects
        self.http = httpx.AsyncClient(timeout=timeout_s, transport=transport, follow_redirects=False,
                                      headers={"User-Agent": user_agent, "Accept": "text/html,*/*;q=0.8"})
        self._robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _check_address(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise FetchError("blocked_scheme", parts.scheme)
        host = (parts.hostname or "").lower()
        if not host:
            raise FetchError("bad_url", url)
        if host in self.allow_hosts:
            return host
        try:
            if _blocked_ip(host):
                raise FetchError("blocked_address", host)
            return host  # a literal public address
        except ValueError:
            pass  # a name, not an address
        if self.resolve_dns:
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
            except socket.gaierror as exc:
                raise FetchError("dns", host) from exc
            if any(_blocked_ip(info[4][0]) for info in infos):
                raise FetchError("blocked_address", host)
        return host

    async def _pace(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._host_last.get(host, 0.0) + self.host_interval_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    async def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(origin)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
        try:
            await self._pace(parts.hostname or "")
            resp = await self.http.get(origin + "/robots.txt")
            if resp.status_code >= 500:
                parser = None  # RFC 9309: a server error disallows all
            elif resp.status_code >= 400:
                parser.parse([])  # no robots file: allow all
            else:
                parser.parse(resp.text[:500_000].splitlines())
        except httpx.HTTPError:
            parser = None
        self._robots[origin] = (time.monotonic() + 3600, parser)
        return parser

    async def allowed_by_robots(self, url: str) -> bool:
        parser = await self._robots_for(url)
        return parser is not None and parser.can_fetch(self.user_agent, url)

    async def fetch(self, url: str) -> RawPage:
        current = url
        for _ in range(self.max_redirects + 1):
            host = await self._check_address(current)
            if host not in self.allow_hosts and not await self.allowed_by_robots(current):
                raise FetchError("robots", current)
            await self._pace(host)
            try:
                async with asyncio.timeout(self.timeout_s):
                    async with self.http.stream("GET", current) as resp:
                        if resp.is_redirect:
                            location = resp.headers.get("location", "")
                            if not location:
                                raise FetchError("bad_redirect", current)
                            current = urljoin(current, location)
                            continue
                        if resp.status_code >= 400:
                            raise FetchError(f"http_{resp.status_code}", current)
                        body, truncated = bytearray(), False
                        async for piece in resp.aiter_bytes():
                            body.extend(piece)
                            if len(body) >= self.max_bytes:
                                truncated = True
                                break
                        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                        return RawPage(url, current, resp.status_code, ctype, bytes(body[: self.max_bytes]), truncated)
            except TimeoutError as exc:
                raise FetchError("timeout", current) from exc
            except httpx.HTTPError as exc:
                raise FetchError("network", f"{type(exc).__name__} {current}") from exc
        raise FetchError("too_many_redirects", url)
