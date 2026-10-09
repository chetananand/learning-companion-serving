"""Tests of the browser service rules, with a fake browser (no Chromium in the unit tests)."""

from __future__ import annotations

import httpx

from app.browser.server import clip_for, create_app, url_allowed


def test_url_rules():
    none = frozenset()
    assert not url_allowed("http://127.0.0.1:8080/", none)
    assert not url_allowed("http://169.254.169.254/latest/meta-data", none)
    assert not url_allowed("http://10.43.0.10/", none)
    assert not url_allowed("file:///etc/passwd", none)
    assert url_allowed("http://test-pages.data.svc.cluster.local/x", frozenset({"test-pages.data.svc.cluster.local"}))
    assert url_allowed("data:image/png;base64,AA", none)  # inline content of a page


def test_clip_is_around_the_element_and_bounded():
    clip = clip_for({"x": 10, "y": 2000, "width": 300, "height": 40}, page_height=10000, width=1280)
    assert clip == {"x": 0, "y": 1400, "width": 1280, "height": 1640}
    top = clip_for({"x": 0, "y": 100, "width": 10, "height": 10}, page_height=500, width=800)
    assert top["y"] == 0 and top["height"] == 500


class FakePage:
    async def goto(self, url, **kw):
        self.url = url

    async def wait_for_load_state(self, *a, **kw):
        return None

    async def evaluate(self, expr):
        return 3000 if "scrollHeight" in expr else 0

    def get_by_text(self, text, exact=False):
        return self

    @property
    def first(self):
        return self

    async def count(self):
        return 0

    async def screenshot(self, **kw):
        return b"\x89PNG-fake"


class FakeContext:
    async def route(self, pattern, handler):
        self.handler = handler

    async def new_page(self):
        return FakePage()

    async def close(self):
        return None


class FakeBrowser:
    async def new_context(self, **kw):
        assert kw["accept_downloads"] is False and kw["service_workers"] == "block"
        return FakeContext()


async def fake_factory():
    async def close():
        return None
    return FakeBrowser(), close


async def test_screenshot_endpoint():
    app = create_app(fake_factory)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://b")
    async with app.router.lifespan_context(app), client as c:
        ok = await c.post("/v1/screenshot", json={"url": "https://93.184.215.14/page"})
        assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
        blocked = await c.post("/v1/screenshot", json={"url": "http://127.0.0.1/"})
        assert blocked.status_code == 400
        assert (await c.get("/readyz")).status_code == 200
