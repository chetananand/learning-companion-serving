"""Client for the browser service (headless Chromium in its own pod, ADR-006)."""

from __future__ import annotations

import httpx


class BrowserError(RuntimeError):
    pass


class BrowserClient:
    def __init__(self, base_url: str, *, timeout_s: float = 25.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def screenshot(self, url: str, near_text: str | None = None, full_page: bool = False) -> bytes:
        """Return a PNG of the page, or of the element that holds `near_text`."""
        try:
            resp = await self.http.post("/v1/screenshot",
                                        json={"url": url, "near_text": near_text, "full_page": full_page})
        except httpx.HTTPError as exc:
            raise BrowserError(f"browser: {type(exc).__name__}") from exc
        if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/png"):
            raise BrowserError(f"browser: HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.content
