"""Model objects for each role, the header hook, and the trace recorder (FR-13, FR-14).

Every LLM call goes to `edge` (H-34). Each role has its own `ChatOpenAI` object, so each
call sends its own `X-Step`. The httpx hook adds the headers of the current turn.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.outputs import LLMResult
from langchain_openai import ChatOpenAI

from app.config import AppSettings
from app.turn import current_turn

ROLES = ("quick", "agent", "verify", "vision", "sweep")
VIA_HEADER = "x-companion-via"


async def add_turn_headers(request: httpx.Request) -> None:
    """httpx request hook: the request contract of the product spec, section 9."""
    request.headers["X-Request-Id"] = uuid.uuid4().hex
    turn = current_turn()
    if turn is None:
        return
    request.headers["X-Tenant-Id"] = turn.tenant
    request.headers["X-Session-Id"] = turn.session_id
    request.headers["X-Request-Class"] = turn.request_class
    request.headers["X-Deadline-Ms"] = str(turn.remaining_ms())
    request.headers["X-Allow-Overflow"] = "true" if turn.allow_overflow else "false"
    if turn.has_images and request.headers.get("X-Step") == "quick":
        request.headers["X-Step"] = "vision"


async def record_via(response: httpx.Response) -> None:
    """httpx response hook: remember if `edge` served the call locally or through the overflow."""
    turn = current_turn()
    via = response.headers.get(VIA_HEADER)
    if turn is not None and via:
        turn.via.add(via)


def make_http_client(settings: AppSettings, transport: httpx.AsyncBaseTransport | None = None,
                     capture: Any = None) -> httpx.AsyncClient:
    """The HTTP client of all model objects. `capture` (app.loadgen.capture.Capture) saves each body."""
    hooks: list[Any] = [add_turn_headers]
    if capture is not None:
        hooks.append(capture.hook)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.request_timeout_s, connect=5.0),
        event_hooks={"request": hooks, "response": [record_via]},
        transport=transport,
    )


def model_profile(settings: AppSettings) -> dict[str, Any]:
    """The limits of the served model, as `edge` applies them (ADR-006, Deep Agents setting 3)."""
    return {
        "max_input_tokens": settings.max_input_tokens,
        "max_output_tokens": settings.max_output_tokens,
        "text_inputs": True,
        "image_inputs": settings.supports_images,
        "tool_calling": True,
        "structured_output": True,
    }


def make_model(role: str, settings: AppSettings, http_client: httpx.AsyncClient, prompt_version: str,
               callbacks: list[Any] | None = None) -> ChatOpenAI:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    max_tokens = {"quick": settings.max_tokens_quick, "vision": settings.max_tokens_quick,
                  "agent": settings.max_tokens_agent, "verify": settings.max_tokens_verify,
                  "sweep": settings.max_tokens_verify}[role]
    return ChatOpenAI(
        model=settings.served_model,
        base_url=settings.edge_url,
        api_key=settings.edge_api_key,
        http_async_client=http_client,
        default_headers={"X-Step": role, "X-Prompt-Version": prompt_version},
        max_tokens=max_tokens,
        temperature=0.0 if role in ("verify", "sweep") else 0.2,
        disable_streaming="tool_calling",  # ADR-006: tool steps never stream
        stream_usage=True,
        max_retries=settings.max_retries,
        timeout=settings.request_timeout_s,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # thinking stays off (section 5)
        profile=model_profile(settings),
        metadata={"step": role},
        callbacks=callbacks,
    )


def default_token_counter() -> Callable[[str], int]:
    return lambda text: max(1, len(text) // 4)


class TraceRecorder(AsyncCallbackHandler):
    """Write the shape of each LLM call to a JSON lines file (FR-14). It writes no prompt text."""

    def __init__(self, path: str | Path | None, count_tokens: Callable[[str], int] | None = None,
                 sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.path = Path(path) if path else None
        self.count = count_tokens or default_token_counter()
        self.sink = sink
        self._open: dict[uuid.UUID, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    async def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *,
                                  run_id: uuid.UUID, metadata: dict[str, Any] | None = None,
                                  **kwargs: Any) -> None:
        params = kwargs.get("invocation_params") or {}
        msgs = messages[0] if messages else []
        system = next((m for m in msgs if isinstance(m, SystemMessage)), None)
        system_text = system.text if system is not None else ""
        tools = params.get("tools") or []
        tools_text = json.dumps(tools, sort_keys=True)
        turn = current_turn()
        self._open[run_id] = {
            "ts": time.time(),
            "t0": time.monotonic(),
            "first": None,
            "step": (metadata or {}).get("step", "unknown"),
            "turn_id": turn.turn_id if turn else None,
            "session_id": turn.session_id if turn else None,
            "tenant": turn.tenant if turn else None,
            "class": turn.request_class if turn else None,
            "mode": turn.mode if turn else None,
            "shared_prefix_tokens": self.count(system_text) + (self.count(tools_text) if tools else 0),
            "prefix_hash": hashlib.sha256((system_text + tools_text).encode()).hexdigest()[:16],
            "tools_bound": len(tools),
            "messages": len(msgs),
        }

    async def on_llm_new_token(self, token: str, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        rec = self._open.get(run_id)
        if rec is not None and rec["first"] is None and token:
            rec["first"] = time.monotonic()

    async def on_llm_end(self, response: LLMResult, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        rec = self._open.pop(run_id, None)
        if rec is None:
            return
        gen = response.generations[0][0] if response.generations and response.generations[0] else None
        msg = getattr(gen, "message", None)
        usage = getattr(msg, "usage_metadata", None) or {}
        details = usage.get("input_token_details") or {}
        tool_calls = [tc["name"] for tc in (getattr(msg, "tool_calls", None) or [])]
        await self._write(rec, {
            "prompt_tokens": usage.get("input_tokens"),
            "cached_tokens": details.get("cache_read"),
            "output_tokens": usage.get("output_tokens"),
            "tool_calls": tool_calls,
            "streamed": rec["first"] is not None,
            "error": None,
        })

    async def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        rec = self._open.pop(run_id, None)
        if rec is not None:
            await self._write(rec, {"error": type(error).__name__, "detail": str(error)[:200]})

    async def _write(self, rec: dict[str, Any], extra: dict[str, Any]) -> None:
        t0, first = rec.pop("t0"), rec.pop("first")
        now = time.monotonic()
        out = {**rec, **extra, "latency_s": round(now - t0, 4),
               "ttft_s": round(first - t0, 4) if first is not None else None}
        if self.sink is not None:
            self.sink(out)
        if self.path is None:
            return
        line = json.dumps(out, sort_keys=True) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        assert self.path is not None
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
