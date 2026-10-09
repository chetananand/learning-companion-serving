"""Tests of the data-plane clients: SIE, the page check, Tavily, the fetcher, and the page reader."""

from __future__ import annotations

import json
import time

import httpx
import msgpack
import numpy as np
import pytest

from app.clients.fetcher import FetchError, PageFetcher
from app.clients.pageguard import MARKER, PageCheckUnavailable, PageGuard, remove_spans
from app.clients.sie import MSGPACK, SieClient, SieError, unpack
from app.clients.websearch import MemoryCache, SearchError, TavilySearch
from app.pages import PageReader, focus_text, split_windows

# SIE


def np_value(arr: np.ndarray) -> dict:
    return {b"nd": True, b"type": arr.dtype.str, b"kind": b"", b"shape": list(arr.shape), b"data": arr.tobytes()}


def sie_transport(handler):
    def wrapped(request: httpx.Request) -> httpx.Response:
        body = unpack(request.content)
        status, out = handler(request.url.path, body)
        return httpx.Response(status, content=msgpack.packb(out, use_bin_type=True),
                              headers={"content-type": MSGPACK, "retry-after": "0"})
    return httpx.MockTransport(wrapped)


async def test_sie_embed_decodes_numpy_values_and_sends_msgpack():
    seen = {}

    def handler(path, body):
        seen["path"], seen["body"] = path, body
        vecs = [np_value(np.array([0.5, 0.25], dtype="<f4")) for _ in body["items"]]
        return 200, {"items": [{"dense": {"values": v}} for v in vecs]}

    sie = SieClient("http://sie", transport=sie_transport(handler))
    out = await sie.embed("BAAI/bge-m3", ["a", "b"], is_query=True)
    assert out == [[0.5, 0.25], [0.5, 0.25]]
    assert seen["path"] == "/v1/encode/BAAI/bge-m3"
    assert seen["body"]["params"] == {"output_types": ["dense"], "options": {"is_query": True}}


async def test_sie_rerank_maps_scores_back_to_the_input_order():
    def handler(path, body):
        assert path == "/v1/score/Qwen/Qwen3-Reranker-4B" and body["query"] == {"text": "q"}
        return 200, {"model": "r", "scores": [{"item_id": "1", "score": 0.9, "rank": 0},
                                              {"item_id": "0", "score": 0.1, "rank": 1}]}

    sie = SieClient("http://sie", transport=sie_transport(handler))
    assert await sie.rerank("Qwen/Qwen3-Reranker-4B", "q", ["x", "y"]) == [0.1, 0.9]


async def test_sie_ocr_returns_the_markdown_entity_and_retries_while_loading():
    calls = []

    def handler(path, body):
        calls.append(path)
        if len(calls) == 1:
            return 503, {"detail": "loading"}
        assert body["items"][0]["images"][0]["format"] == "png"
        return 200, {"items": [{"entities": [{"text": "# Title\n\nText", "label": "markdown", "score": 1.0}]}]}

    sie = SieClient("http://sie", transport=sie_transport(handler))
    assert await sie.ocr("lightonai/LightOnOCR-2-1B", b"\x89PNG") == "# Title\n\nText"
    assert len(calls) == 2


async def test_sie_network_error_becomes_a_sie_error():
    """G0: an unreachable SIE pod (a restart) raised httpx.ConnectError, and the ingest job crashed."""
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    sie = SieClient("http://sie", transport=httpx.MockTransport(refuse))
    with pytest.raises(SieError, match="ConnectError"):
        await sie.ocr("lightonai/LightOnOCR-2-1B", b"\x89PNG")


async def test_sie_rejects_an_object_array():
    bad = {b"nd": True, b"type": "|O", b"kind": b"O", b"shape": [1], b"data": b"x"}
    with pytest.raises(SieError):
        unpack(msgpack.packb({"items": [{"dense": {"values": bad}}]}, use_bin_type=True))


# The page check


def test_remove_spans_merges_overlapping_windows():
    text = "0123456789abcdefghij"
    out, n = remove_spans(text, [(2, 6), (5, 9), (15, 17)])
    assert n == 2 and out == f"01{MARKER}9abcde{MARKER}hij"


async def test_page_guard_removes_windows_above_the_threshold():
    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["texts"]
        return httpx.Response(200, json={"threshold": 0.5, "results": [
            {"malicious": True, "score": 0.97, "windows": [{"start": 6, "end": 12, "score": 0.97}]}
            if "evil" in t else {"malicious": False, "score": 0.02, "windows": [{"start": 0, "end": len(t),
                                                                                 "score": 0.02}]}
            for t in texts]})

    guard = PageGuard("http://guard", transport=httpx.MockTransport(handler))
    good, bad = await guard.check_many(["fine text", "hello evil!! end"])
    assert (good.text, good.removed) == ("fine text", 0)
    assert bad.removed == 1 and bad.text == f"hello {MARKER} end"


@pytest.mark.parametrize("failure", ["timeout", "500"])
async def test_page_guard_fails_closed(failure):
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("slow")
        return httpx.Response(500)

    guard = PageGuard("http://guard", transport=httpx.MockTransport(handler))
    with pytest.raises(PageCheckUnavailable):
        await guard.check("text")


# Tavily


async def test_tavily_sends_a_basic_search_and_caches_the_result():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers["authorization"], json.loads(request.content)))
        return httpx.Response(200, json={"results": [{"title": "vLLM", "url": "https://docs.vllm.ai/",
                                                      "content": "vLLM 0.30.0", "score": 0.9}]})

    search = TavilySearch("tvly-test", cache=MemoryCache(), transport=httpx.MockTransport(handler))
    first = await search.search("vllm   Latest version", 5, recency_days=30)
    second = await search.search("VLLM latest version", 5, recency_days=30)
    assert first == second and first[0].url == "https://docs.vllm.ai/"
    assert len(seen) == 1  # the second search came from the cache
    auth, body = seen[0]
    assert auth == "Bearer tvly-test"
    assert body == {"query": "vllm   Latest version", "search_depth": "basic", "max_results": 5,
                    "include_answer": False, "include_raw_content": False, "time_range": "month"}


@pytest.mark.parametrize("status,reason", [(429, "usage limit"), (432, "plan limit"), (401, "bad API key")])
async def test_tavily_errors_have_a_reason(status, reason):
    search = TavilySearch("k", transport=httpx.MockTransport(lambda r: httpx.Response(status)))
    with pytest.raises(SearchError, match=reason):
        await search.search("q")


# The fetcher


def site(pages: dict[str, tuple[int, str, dict[str, str]]], log: list[str] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(str(request.url))
        status, body, headers = pages.get(str(request.url), (404, "", {}))
        return httpx.Response(status, text=body, headers={"content-type": "text/html", **headers})
    return httpx.MockTransport(handler)


def fetcher(transport, **kw) -> PageFetcher:
    return PageFetcher(user_agent="TestBot", transport=transport, resolve_dns=False, host_interval_s=0.0, **kw)


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://10.0.0.5/", "http://169.254.169.254/latest",
                                 "file:///etc/passwd", "http://[::1]/"])
async def test_fetcher_blocks_private_addresses_and_other_schemes(url):
    with pytest.raises(FetchError) as err:
        await fetcher(site({})).fetch(url)
    assert err.value.reason in ("blocked_address", "blocked_scheme")


async def test_fetcher_obeys_robots_txt():
    pages = {"https://a.com/robots.txt": (200, "User-agent: *\nDisallow: /private", {}),
             "https://a.com/public": (200, "<p>ok</p>", {})}
    f = fetcher(site(pages))
    assert (await f.fetch("https://a.com/public")).body == b"<p>ok</p>"
    with pytest.raises(FetchError, match="robots"):
        await f.fetch("https://a.com/private/page")


async def test_robots_404_allows_and_robots_500_disallows():
    ok = {"https://a.com/p": (200, "hi", {})}
    assert (await fetcher(site(ok)).fetch("https://a.com/p")).status == 200
    down = {"https://b.com/robots.txt": (503, "", {}), "https://b.com/p": (200, "hi", {})}
    with pytest.raises(FetchError, match="robots"):
        await fetcher(site(down)).fetch("https://b.com/p")


async def test_fetcher_follows_redirects_and_checks_each_hop():
    pages = {"https://a.com/old": (301, "", {"location": "https://a.com/new"}),
             "https://a.com/new": (200, "new", {}),
             "https://a.com/evil": (302, "", {"location": "http://127.0.0.1/admin"})}
    f = fetcher(site(pages))
    page = await f.fetch("https://a.com/old")
    assert page.final_url == "https://a.com/new" and page.body == b"new"
    with pytest.raises(FetchError, match="blocked_address"):
        await f.fetch("https://a.com/evil")


async def test_fetcher_cuts_the_body_at_the_size_limit_and_marks_dead_links():
    pages = {"https://a.com/big": (200, "x" * 5000, {})}
    page = await fetcher(site(pages), max_bytes=1000).fetch("https://a.com/big")
    assert len(page.body) == 1000 and page.truncated
    with pytest.raises(FetchError) as err:
        await fetcher(site(pages)).fetch("https://a.com/gone")
    assert err.value.reason == "http_404" and err.value.dead


async def test_fetcher_paces_requests_to_one_host():
    pages = {"https://a.com/1": (200, "1", {}), "https://a.com/2": (200, "2", {})}
    f = PageFetcher(user_agent="TestBot", transport=site(pages), resolve_dns=False, host_interval_s=0.2)
    t0 = time.monotonic()
    await f.fetch("https://a.com/1")
    await f.fetch("https://a.com/2")
    assert time.monotonic() - t0 >= 0.4  # robots.txt, page 1, page 2: two waits of 0.2 s


async def test_allow_list_opens_the_in_cluster_test_host():
    pages = {"http://test-pages.data.svc.cluster.local/injected": (200, "<p>page</p>", {})}
    f = fetcher(site(pages), allow_hosts=frozenset({"test-pages.data.svc.cluster.local"}))
    assert (await f.fetch("http://test-pages.data.svc.cluster.local/injected")).status == 200


# The page reader


def test_split_and_focus_keep_the_matching_windows_in_page_order():
    text = "\n".join([f"para {i} about cats " * 20 for i in range(10)] + ["vLLM version 0.30.0 is current " * 5])
    windows = split_windows(text, size_chars=500, overlap_chars=50)
    assert len(windows) > 5 and all(len(w) <= 500 for w in windows)
    out = focus_text(text, "Which vLLM version?", max_chars=600)
    assert "0.30.0" in out and len(out) <= 1300


async def test_reader_uses_ocr_when_the_text_is_short():
    class Browser:
        async def screenshot(self, url, near_text=None, full_page=False):
            return b"png"

    class Ocr:
        async def ocr(self, model, image, image_format="png"):
            return "A figure: Gemma 3 27B uses 5:1 local to global attention. " * 20

    html = ("<html><head><meta property='article:published_time' content='2026-03-01'></head>"
            "<body><p>Hi</p></body></html>")
    pages = {"https://gallery.com/robots.txt": (404, "", {}), "https://gallery.com/": (200, html, {})}
    reader = PageReader(fetcher(site(pages)), browser=Browser(), sie_ocr=Ocr(), ocr_model="m", guard=None)
    page = await reader.read("https://gallery.com/")
    assert page.via == "ocr" and "5:1 local" in page.text
    assert page.date is not None and page.date.isoformat() == "2026-03-01"


def test_arxiv_pdf_links_read_the_abstract_page():
    from app.pages import fetch_url

    assert fetch_url("https://arxiv.org/pdf/2609.05364") == "https://arxiv.org/abs/2609.05364"
    assert fetch_url("https://arxiv.org/pdf/2609.05364v2.pdf") == "https://arxiv.org/abs/2609.05364v2"
    assert fetch_url("https://example.com/pdf/x") == "https://example.com/pdf/x"


async def test_reader_reads_a_pdf():
    import io

    from pypdf import PdfWriter
    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(buf)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=buf.getvalue(), headers={"content-type": "application/pdf"})

    reader = PageReader(fetcher(httpx.MockTransport(handler)), browser=None, sie_ocr=None, ocr_model="m", guard=None)
    page = await reader.read("https://docs.example.com/a.pdf")
    assert page.via == "pdf" and page.text == ""  # a blank page has no text
    bad = PageReader(fetcher(httpx.MockTransport(lambda r: httpx.Response(
        200 if r.url.path != "/robots.txt" else 404, content=b"%PDF-broken",
        headers={"content-type": "application/pdf"}))), browser=None, sie_ocr=None, ocr_model="m", guard=None)
    with pytest.raises(FetchError, match="pdf_unreadable"):
        await bad.read("https://docs.example.com/b.pdf")
