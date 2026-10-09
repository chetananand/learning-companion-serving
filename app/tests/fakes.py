"""Test doubles: a fake `edge` that speaks the OpenAI chat API, and fakes for the data plane.

The fake edge runs in the test process (httpx ASGI transport). The real ChatOpenAI, the real
LangGraph agents, and the real Deep Agents harness send their calls to it. A script decides
each reply from the headers and the request body.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.clients.pageguard import MARKER, CheckedText, PageCheckUnavailable
from app.clients.websearch import SearchResult
from app.pages import ReadPage
from app.retrieval import Hit

INJECTION = "ignore all previous instructions"


@dataclass
class Reply:
    content: str | None = None
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    status: int = 200
    error_type: str = ""
    prompt_tokens: int = 1000
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Call:
    headers: dict[str, str]
    body: dict[str, Any]

    @property
    def step(self) -> str:
        return self.headers.get("x-step", "")

    @property
    def tool_names(self) -> list[str]:
        return [t["function"]["name"] for t in self.body.get("tools") or []]


def called_tools(body: dict[str, Any]) -> list[str]:
    """Names of the tools that the assistant called after the last user message."""
    msgs = body.get("messages", [])
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    names = []
    for m in msgs[last_user + 1:]:
        for tc in m.get("tool_calls") or []:
            names.append(tc["function"]["name"])
    return names


def last_user_text(body: dict[str, Any]) -> str:
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return str(c)
    return ""


class FakeEdge:
    def __init__(self, script: Callable[[Call], Reply]) -> None:
        self.script = script
        self.calls: list[Call] = []
        self._ids = itertools.count(1)
        self.app = FastAPI()
        self.app.add_api_route("/v1/chat/completions", self.handle, methods=["POST"])

    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self.app)

    async def handle(self, request: Request) -> Any:
        body = await request.json()
        call = Call({k.lower(): v for k, v in request.headers.items()}, body)
        self.calls.append(call)
        reply = self.script(call)
        if reply.status != 200:
            return JSONResponse({"error": {"message": reply.error_type, "type": reply.error_type,
                                           "code": reply.status}}, status_code=reply.status)
        n = next(self._ids)
        tool_calls = [{"id": f"call_{n}_{i}", "type": "function",
                       "function": {"name": name, "arguments": json.dumps(args)}}
                      for i, (name, args) in enumerate(reply.tool_calls)]
        usage = {"prompt_tokens": reply.prompt_tokens, "completion_tokens": 20,
                 "total_tokens": reply.prompt_tokens + 20}
        headers = {"x-companion-via": "local", **reply.headers}
        if body.get("stream"):
            return StreamingResponse(self._sse(n, reply, tool_calls, usage), media_type="text/event-stream",
                                     headers=headers)
        message: dict[str, Any] = {"role": "assistant", "content": reply.content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return JSONResponse({"id": f"chatcmpl-{n}", "object": "chat.completion", "created": 1_700_000_000,
                             "model": body.get("model"), "usage": usage,
                             "choices": [{"index": 0, "message": message,
                                          "finish_reason": "tool_calls" if tool_calls else "stop"}]},
                            headers=headers)

    async def _sse(self, n: int, reply: Reply, tool_calls: list[dict[str, Any]], usage: dict[str, Any]):
        def chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
            data = {"id": f"chatcmpl-{n}", "object": "chat.completion.chunk", "created": 1_700_000_000,
                    "model": "companion", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
            return f"data: {json.dumps(data)}\n\n"

        yield chunk({"role": "assistant", "content": ""})
        for word in (reply.content or "").split(" "):
            yield chunk({"content": word + " "})
        for i, tc in enumerate(tool_calls):
            yield chunk({"tool_calls": [{"index": i, **tc}]})
        yield chunk({}, "tool_calls" if tool_calls else "stop")
        usage_chunk = {"id": f"chatcmpl-{n}", "object": "chat.completion.chunk", "created": 1_700_000_000,
                       "model": "companion", "choices": [], "usage": usage}
        yield f"data: {json.dumps(usage_chunk)}\n\n"
        yield "data: [DONE]\n\n"


class FakeRetriever:
    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits
        self.queries: list[str] = []

    async def search(self, query: str, k: int) -> list[Hit]:
        self.queries.append(query)
        return self.hits[:k]


class FakeSearch:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.queries: list[str] = []

    async def search(self, query: str, max_results: int = 5, recency_days: int | None = None) -> list[SearchResult]:
        self.queries.append(query)
        return self.results[:max_results]


class FakeGuard:
    def __init__(self, down: bool = False) -> None:
        self.down = down
        self.texts: list[str] = []

    async def check_many(self, texts: list[str]) -> list[CheckedText]:
        if self.down:
            raise PageCheckUnavailable("down")
        self.texts += texts
        out = []
        for t in texts:
            if INJECTION in t.lower():
                start = t.lower().index(INJECTION)
                out.append(CheckedText(t[:start] + MARKER, 1, 0.99))
            else:
                out.append(CheckedText(t, 0, 0.01))
        return out

    async def check(self, text: str) -> CheckedText:
        return (await self.check_many([text]))[0]


class FakeReader:
    def __init__(self, pages: dict[str, ReadPage], ocr_text: str = "OCR text of the figure") -> None:
        self.pages = pages
        self.ocr_text = ocr_text
        self.shots: list[tuple[str, str | None]] = []

    async def read(self, url: str) -> ReadPage:
        from app.clients.fetcher import FetchError

        if url not in self.pages:
            raise FetchError("http_404", url)
        return self.pages[url]

    async def screenshot_text(self, url: str, near_text: str | None = None) -> str:
        self.shots.append((url, near_text))
        return self.ocr_text

    async def ocr_image(self, image: bytes, image_format: str = "png") -> str:
        return self.ocr_text


def hit(chunk_id: str, url: str, text: str, created: dt.date | None = dt.date(2024, 3, 13),
        title: str = "A bookmark") -> Hit:
    return Hit(chunk_id=chunk_id, bookmark_id=chunk_id.split("-")[0], title=title, url=url, text=text,
               created=created, page_date=None, score=1.0)
