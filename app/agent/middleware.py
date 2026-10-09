"""Our two middleware classes (ADR-006).

AnswerGate: the streaming rule. A model call with tools does not stream, so the answer
call must have no tools. The gate removes the tools after `answer_now`, after the step
budget, or near the deadline.

FactCheckMiddleware: rule F2 in code. It wraps each `task` call to the `fact-checker`,
applies `resolve()` to the ClaimCheck, and gives the main agent only the resolved result.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.agent.schemas import ClaimCheck, bookmark_ref, parse_claim_check
from app.agent.tools import ANSWER_NOW, REFUSALS
from app.factcheck.resolve import Finding, Resolution, Source, Status, Tiers, Verdict, resolve
from app.metrics import AppMetrics
from app.turn import ClaimResult, TurnState, current_turn

FACT_CHECKER = "fact-checker"


def _tail(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """The messages of the current turn: all messages after the last user message."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return list(messages[i + 1:])
    return list(messages)


def gate_reason(messages: Sequence[AnyMessage], *, max_tool_rounds: int | None, answer_by_s: float | None,
                turn: TurnState | None) -> str | None:
    tail = _tail(messages)
    if any(isinstance(m, ToolMessage) and m.name == ANSWER_NOW and m.content not in REFUSALS for m in tail):
        return "answer_now"
    rounds = sum(1 for m in tail if isinstance(m, AIMessage) and m.tool_calls)
    # A refused answer_now (the live-fetch or the figure rule) does not use a round of the budget.
    rounds -= sum(1 for m in tail if isinstance(m, ToolMessage) and m.name == ANSWER_NOW and m.content in REFUSALS)
    if max_tool_rounds is not None and rounds >= max_tool_rounds:
        return "budget"
    if answer_by_s is not None and turn is not None and turn.elapsed_s() >= answer_by_s:
        return "deadline"
    return None


# 2026-10-01 (demo D-07): the bookmark page was gone (404), then the deadline gate removed the tools, and the model
# answered with no text. The user got an empty turn. One more call with this instruction gives an answer.
EMPTY_RETRY = ("Write the answer now, in plain text, from the evidence above. If a page could not be read, "
               "say so in one sentence.")


# If the retry also gives no text, the user gets this, not an empty turn.
NO_ANSWER = ("I could not finish the answer in time. The sources above are the evidence that I found. "
             "Please ask again.")


def _answer_text(response: Any) -> str:
    msg = next((m for m in reversed(getattr(response, "result", []) or []) if isinstance(m, AIMessage)), None)
    return msg.text.strip() if msg is not None else ""


def _forced_answer(response: Any, fallback: str | None = None) -> Any:
    """A forced answer never calls a tool: drop the tool calls (the parser can find one in the text, although no
    tools are bound). With no text at all, use the fallback."""
    result = list(getattr(response, "result", []) or [])
    for i in range(len(result) - 1, -1, -1):
        if isinstance(result[i], AIMessage):
            msg = result[i]
            update: dict[str, Any] = {"tool_calls": [], "invalid_tool_calls": []}
            if fallback is not None and not msg.text.strip():
                update["content"] = fallback
            result[i] = msg.model_copy(update=update)
            break
    return replace(response, result=result)


class AnswerGate(AgentMiddleware):
    def __init__(self, *, agent: str, max_tool_rounds: int | None, answer_by_s: float | None,
                 metrics: AppMetrics | None = None) -> None:
        super().__init__()
        self.agent = agent
        self.max_tool_rounds = max_tool_rounds
        self.answer_by_s = answer_by_s
        self.metrics = metrics

    def _count(self, reason: str) -> None:
        if self.metrics is not None:
            self.metrics.gate.labels(self.agent, reason).inc()
        if (turn := current_turn()) is not None:
            turn.emit("gate", agent=self.agent, reason=reason)

    async def awrap_model_call(self, request: ModelRequest,
                               handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> Any:
        reason = gate_reason(request.messages, max_tool_rounds=self.max_tool_rounds, answer_by_s=self.answer_by_s,
                             turn=current_turn())
        if reason is not None and request.tools:
            request = request.override(tools=[], tool_choice=None)
            self._count(reason)
        response = await handler(request)
        if reason is not None and not _answer_text(response):  # a tool call is not an answer here
            self._count("empty")
            response = await handler(request.override(
                messages=[*request.messages, HumanMessage(content=EMPTY_RETRY)], tools=[], tool_choice=None))
            response = _forced_answer(response, fallback=NO_ANSWER)
        elif reason is not None:
            response = _forced_answer(response)
        if reason is None:
            msg = next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)
            if msg is not None and not msg.tool_calls and msg.text.strip():
                self._count("missed")  # the model answered with tools bound: the UI gets one block
        return response


class FlattenSystem(AgentMiddleware):
    """Send the system prompt as one string. Middleware adds text blocks, and a chat template can expect a string."""

    async def awrap_model_call(self, request: ModelRequest,
                               handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> Any:
        sm = request.system_message
        if sm is not None and not isinstance(sm.content, str):
            request = request.override(system_message=SystemMessage(content=sm.text))
        return await handler(request)


def _tool_message(result: ToolMessage | Command) -> ToolMessage | None:
    if isinstance(result, ToolMessage):
        return result
    update = result.update if isinstance(result.update, dict) else {}
    msgs = update.get("messages") or []
    return next((m for m in reversed(msgs) if isinstance(m, ToolMessage)), None)


def _with_content(result: ToolMessage | Command, content: str) -> ToolMessage | Command:
    if isinstance(result, ToolMessage):
        return result.model_copy(update={"content": content})
    update = dict(result.update) if isinstance(result.update, dict) else {}
    msgs = [m.model_copy(update={"content": content}) if isinstance(m, ToolMessage) else m
            for m in update.get("messages") or []]
    update["messages"] = msgs
    return replace(result, update=update)


def resolution_json(claim_id: str, claim: str, ref: str | None, res: Resolution, turn: TurnState) -> dict[str, Any]:
    winner = res.winner
    live = None
    if winner is not None:
        page = turn.page(winner.source.url)
        live = {"url": winner.source.url, "ref": page.ref if page else None,
                "date": winner.source.date.isoformat() if winner.source.date else None,
                "tier": page.tier if page else None, "value": winner.correction}
    others = [{"url": f.source.url, "verdict": f.verdict.value, "value": f.correction,
               "date": f.source.date.isoformat() if f.source.date else None} for f in res.citations if f is not winner]
    chunk = turn.chunks.get(ref) if ref else None
    return {"claim_id": claim_id, "claim": claim, "status": res.status.value, "path": res.path, "reason": res.reason,
            "checked_on": turn.today.isoformat(), "live": live, "other_sources": others,
            "bookmark": {"ref": ref, "date": chunk.evidence_date.isoformat() if chunk and chunk.evidence_date
                         else None}}


class FactCheckMiddleware(AgentMiddleware):
    def __init__(self, *, max_checks: int, answer_by_s: float, tiers: Tiers | None = None,
                 metrics: AppMetrics | None = None) -> None:
        super().__init__()
        self.max_checks = max_checks
        self.answer_by_s = answer_by_s
        self.tiers = tiers
        self.metrics = metrics

    def findings(self, turn: TurnState, check: ClaimCheck) -> list[Finding]:
        out = []
        for pv in check.pages:
            page = turn.page(pv.url)
            if page is None:
                continue  # the agent did not read this page with fetch_page: it does not count
            out.append(Finding(Source(page.url, page.date, page.text), Verdict(pv.verdict),
                               pv.corrected_value if pv.verdict in ("CONTRADICTED", "OUTDATED") else None))
        return out

    def decide(self, turn: TurnState, check: ClaimCheck | None, description: str) -> tuple[str, str | None, Resolution]:
        first_line = description.strip().splitlines()[0][:300] if description.strip() else ""
        claim = (check.claim if check else "") or first_line
        ref = bookmark_ref(check.bookmark_ref if check else None, description)
        if check is None:
            return claim, ref, Resolution(Status.NOT_VERIFIED, "no_evidence", None, (),
                                          "the fact-checker gave no ClaimCheck")
        chunk = turn.chunks.get(ref) if ref else None
        bookmark = Source(chunk.url, chunk.evidence_date, chunk.text) if chunk else Source("", None, "")
        return claim, ref, resolve(bookmark, self.findings(turn, check), today=turn.today, tiers=self.tiers)

    def _record(self, turn: TurnState, claim_id: str, claim: str, ref: str | None, res: Resolution) -> str:
        turn.claims.append(ClaimResult(claim_id, claim, ref, res))
        payload = resolution_json(claim_id, claim, ref, res, turn)
        turn.emit("claim", **payload)
        if self.metrics is not None:
            self.metrics.verdicts.labels(res.status.value, res.path).inc()
        return json.dumps(payload)

    async def awrap_tool_call(self, request: ToolCallRequest,
                              handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]) -> Any:
        call = request.tool_call
        args = call.get("args") or {}
        if call.get("name") != "task" or args.get("subagent_type") != FACT_CHECKER:
            return await handler(request)
        turn = current_turn()
        if turn is None:
            return await handler(request)
        if turn.fact_checks >= self.max_checks:
            return ToolMessage("The fact-check budget is used. Call answer_now.", tool_call_id=call["id"], name="task")
        turn.fact_checks += 1
        claim_id = f"C{turn.fact_checks}"
        description = str(args.get("description", ""))
        turn.emit("claim_start", claim_id=claim_id, text=description[:300])
        try:
            result = await asyncio.wait_for(handler(request), max(1.0, self.answer_by_s - turn.elapsed_s()))
        except TimeoutError:
            claim, ref, _ = self.decide(turn, None, description)
            res = Resolution(Status.NOT_VERIFIED, "timeout", None, (), "the fact check took too long")
            return ToolMessage(self._record(turn, claim_id, claim, ref, res), tool_call_id=call["id"], name="task")
        msg = _tool_message(result)
        check = parse_claim_check(msg.text if msg is not None else "")
        claim, ref, res = self.decide(turn, check, description)
        return _with_content(result, self._record(turn, claim_id, claim, ref, res))
