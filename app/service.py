"""The Companion service: one user turn from the question to the streamed answer.

quick:    the quick agent answers from the bookmarks. The answer streams (SLO-3).
verified: the quick agent writes the draft (it streams, SLO-3). Then the Deep Agent checks
          the volatile claims with the `fact-checker` subagent, code applies rule F2, and the
          final answer streams (SLO-4).

Events go to the caller as dictionaries (the API sends them as server-sent events):
step, source, guard, gate, claim_start, claim, token, error, done.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

import openai
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, RemoveMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.build import build_quick_agent, build_verified_agent
from app.agent.middleware import NO_ANSWER
from app.agent.tools import Toolbox
from app.config import AppSettings
from app.llm import TraceRecorder
from app.metrics import AppMetrics
from app.prompts import PROMPT_VERSION
from app.turn import TurnState, turn_scope

ANSWER_STEPS = {"quick": "quick", "vision": "quick", "agent": "agent"}


@dataclass
class TurnRequest:
    question: str
    mode: Literal["quick", "verified"] = "quick"
    session_id: str | None = None
    tenant: str | None = None
    request_class: Literal["interactive", "batch"] = "interactive"
    allow_overflow: bool | None = None
    images: list[str] = field(default_factory=list)  # data URLs (PNG or JPEG), J4


def classify_error(exc: BaseException) -> dict[str, Any]:
    """Map an exception to the error event. `edge` rejects carry our reason in `error.type`."""
    if isinstance(exc, openai.APIStatusError):
        body = exc.body if isinstance(exc.body, dict) else {}
        err = body.get("error", body) if isinstance(body.get("error", body), dict) else {}
        return {"code": exc.status_code, "reason": err.get("type") or "upstream_error",
                "message": str(err.get("message") or exc.message)[:300],
                "retry_after": exc.response.headers.get("retry-after")}
    if isinstance(exc, openai.APITimeoutError):
        return {"code": 504, "reason": "timeout", "message": "the LLM call took too long"}
    if isinstance(exc, openai.APIConnectionError):
        return {"code": 503, "reason": "edge_unreachable", "message": "the app cannot reach edge"}
    if isinstance(exc, TimeoutError):
        return {"code": 504, "reason": "turn_timeout", "message": "the turn took too long"}
    return {"code": 500, "reason": "app_error", "message": f"{type(exc).__name__}: {str(exc)[:200]}"}


def session_context(turn: TurnState) -> str:
    return f"Today: {turn.today.isoformat()}. Mode: {turn.mode}."


def user_content(turn: TurnState, question: str, images: list[str]) -> str | list[dict[str, Any]]:
    text = f"{session_context(turn)}\n\nQuestion: {question.strip()}"
    if not images:
        return text
    return [{"type": "text", "text": text}, *({"type": "image_url", "image_url": {"url": u}} for u in images)]


def verify_content(turn: TurnState, question: str, draft: str) -> str:
    sources = []
    for c in sorted(turn.chunks.values(), key=lambda c: int(c.ref[1:])):
        saved = c.saved.isoformat() if c.saved else "unknown"
        page = c.page_date.isoformat() if c.page_date else "unknown"
        sources.append(f"[{c.ref}] {c.title} | {c.url} | saved {saved} | page date {page}")
    return (f"{session_context(turn)}\n\nQuestion: {question.strip()}\n\nDraft answer:\n{draft.strip()}\n\n"
            "Bookmark sources:\n" + ("\n".join(sources) or "(none)"))


def answer_text(msg: BaseMessage, meta: dict[str, Any], steps: set[str]) -> str:
    """Return the text of an answer token, or "" for tool calls, tool results, and other agents."""
    if not isinstance(msg, (AIMessage, AIMessageChunk)):
        return ""
    if getattr(msg, "tool_calls", None) or getattr(msg, "tool_call_chunks", None):
        return ""
    if meta.get("step") not in steps:
        return ""
    return msg.text


class Companion:
    def __init__(self, settings: AppSettings, toolbox: Toolbox, models: dict[str, Any], metrics: AppMetrics,
                 trace: TraceRecorder | None = None, checkpointer: Any = None) -> None:
        self.s = settings
        self.toolbox = toolbox
        self.models = models
        self.metrics = metrics
        self.trace = trace
        self.checkpointer = checkpointer or InMemorySaver()
        self.quick = build_quick_agent(models["quick"], toolbox, settings, metrics, self.checkpointer)
        self.verifier = build_verified_agent(models["agent"], models["verify"], toolbox, settings, metrics)

    def _config(self, thread_id: str, recursion_limit: int) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit,
                "callbacks": [self.trace] if self.trace else []}

    async def _stream_answer(self, graph: Any, inputs: dict[str, Any], config: dict[str, Any], turn: TurnState,
                             phase: str, steps: set[str], t0: float) -> str:
        parts: list[str] = []
        async for msg, meta in graph.astream(inputs, config, stream_mode="messages"):
            text = answer_text(msg, meta, steps)
            if not text:
                continue
            if not parts:
                self.metrics.turn_seconds.labels(turn.mode, f"first_{phase}_token").observe(time.monotonic() - t0)
            parts.append(text)
            turn.emit("token", phase=phase, text=text)
        if not "".join(parts).strip():  # 2026-10-01 (demo D-07): no text. The user must not get an empty turn.
            parts.append(NO_ANSWER)
            turn.emit("token", phase=phase, text=NO_ANSWER)
        return "".join(parts)

    async def _replace_draft(self, turn: TurnState, final: str) -> None:
        """Keep the verified answer, not the draft, in the session history."""
        config = {"configurable": {"thread_id": turn.session_id}}
        state = await self.quick.aget_state(config)
        msgs = state.values.get("messages", []) if state and state.values else []
        draft = next((m for m in reversed(msgs) if isinstance(m, AIMessage) and not m.tool_calls), None)
        if draft is None or draft.id is None:
            return
        update = {"messages": [RemoveMessage(id=draft.id), AIMessage(content=final.strip())]}
        await self.quick.aupdate_state(config, update, as_node="model")

    async def _run(self, turn: TurnState, req: TurnRequest) -> None:
        t0 = time.monotonic()
        draft = await self._stream_answer(
            self.quick, {"messages": [HumanMessage(content=user_content(turn, req.question, req.images))]},
            self._config(turn.session_id, 25), turn, "draft" if turn.mode == "verified" else "answer",
            {"quick", "vision"}, t0)
        if turn.mode == "verified" and draft.strip():
            final = await self._stream_answer(
                self.verifier, {"messages": [HumanMessage(content=verify_content(turn, req.question, draft))]},
                self._config(f"{turn.session_id}:{turn.turn_id}", 80), turn, "final", {"agent"}, t0)
            if final.strip():
                await self._replace_draft(turn, final)
        self.metrics.turn_seconds.labels(turn.mode, "complete").observe(time.monotonic() - t0)

    async def run(self, req: TurnRequest) -> AsyncIterator[dict[str, Any]]:
        s = self.s
        turn = TurnState(session_id=req.session_id or uuid.uuid4().hex, tenant=req.tenant or s.tenant,
                         request_class=req.request_class, mode=req.mode,
                         allow_overflow=s.allow_overflow if req.allow_overflow is None else req.allow_overflow,
                         has_images=bool(req.images), prompt_version=PROMPT_VERSION, question=req.question,
                         deadline_s=s.verified_deadline_s if req.mode == "verified" else s.quick_deadline_s,
                         events=asyncio.Queue())
        events = turn.events
        assert events is not None
        turn.emit("start", turn_id=turn.turn_id, session_id=turn.session_id, mode=turn.mode,
                  prompt_version=PROMPT_VERSION)

        async def work() -> None:
            outcome = "ok"
            with turn_scope(turn):
                try:
                    await asyncio.wait_for(self._run(turn, req), timeout=turn.deadline_s + 30)
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    raise
                except Exception as exc:  # the UI shows the reason, the metrics count it
                    err = classify_error(exc)
                    outcome = str(err["reason"])
                    turn.emit("error", **err)
                finally:
                    self.metrics.turns.labels(turn.mode, outcome).inc()
                    turn.emit("done", turn_id=turn.turn_id, session_id=turn.session_id, outcome=outcome,
                              via=sorted(turn.via), elapsed_ms=int(turn.elapsed_s() * 1000),
                              claims=len(turn.claims))
                    events.put_nowait(None)

        task = asyncio.create_task(work())
        try:
            while (event := await events.get()) is not None:
                yield event
        finally:
            if not task.done():
                task.cancel()  # the client left: stop the agent, so the LLM streams close (H-72)
