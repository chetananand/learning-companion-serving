"""The freshness sweep (J5, product spec section 8): batch traffic for Track B.

For each bookmark: one LLM call lists the volatile claims (X-Step `sweep`), then the fact-checker
checks each claim (the S4 loop, X-Step `verify`), and code applies rule F2. All calls use the
tenant `sweep` and the class `batch`, so they go to the batch band and never to the overflow.
The job does not write back to Notion. It writes a JSON report and a Markdown report.

Run: python -m app.sweep --count 20
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage, SystemMessage

from app import prompts
from app.agent.middleware import FactCheckMiddleware, FlattenSystem, resolution_json
from app.agent.schemas import ClaimCheck
from app.agent.tools import Toolbox
from app.config import AppSettings
from app.retrieval import Hit
from app.turn import Chunk, ClaimResult, TurnState, turn_scope

VERSIONISH = re.compile(r"\b(v?\d+\.\d+(\.\d+)?|20\d\d|latest|current|new)\b", re.I)


@dataclass(frozen=True)
class SweepItem:
    bookmark_id: str
    title: str
    url: str
    created: dt.date | None
    text: str


def pick(hits: list[Hit], count: int, *, older_than_days: int = 365, today: dt.date | None = None) -> list[SweepItem]:
    """Pick old bookmarks with volatile text: versions, years, or the words latest, current, new."""
    today = today or dt.date.today()
    by_bookmark: dict[str, list[Hit]] = {}
    for h in hits:
        if h.created and (today - h.created).days >= older_than_days and not h.dead:
            by_bookmark.setdefault(h.bookmark_id, []).append(h)
    scored = []
    for bid, group in by_bookmark.items():
        group.sort(key=lambda h: h.chunk_id)
        text = "\n\n".join(h.text for h in group)[:6000]
        scored.append((len(VERSIONISH.findall(text)), bid, group[0], text))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [SweepItem(bid, h.title, h.url, h.created, text) for n, bid, h, text in scored[:count] if n > 0]


def parse_claims(text: str, limit: int = 3) -> list[str]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return []
    return [str(c).strip() for c in data if str(c).strip()][:limit]


class Sweep:
    def __init__(self, settings: AppSettings, toolbox: Toolbox, models: dict[str, Any], *, callbacks=None,
                 concurrency: int = 4) -> None:
        self.s = settings
        self.toolbox = toolbox
        self.extract_model = models["sweep"]
        self.checker = create_agent(models["verify"], tools=toolbox.verify_tools(), system_prompt=prompts.VERIFY,
                                    response_format=ToolStrategy(ClaimCheck), name="sweep-fact-checker",
                                    middleware=[ModelCallLimitMiddleware(run_limit=6, exit_behavior="end"),
                                                FlattenSystem()])
        self.fc = FactCheckMiddleware(max_checks=10**6, answer_by_s=10**6, tiers=toolbox.tiers)
        self.callbacks = callbacks or []
        self.sem = asyncio.Semaphore(concurrency)

    async def one(self, item: SweepItem, run_id: str) -> dict[str, Any]:
        turn = TurnState(session_id=f"sweep-{run_id}-{item.bookmark_id[:8]}", tenant="sweep", request_class="batch",
                         mode="sweep", allow_overflow=False, deadline_s=300.0)
        chunk = Chunk(ref="B1", chunk_id=f"{item.bookmark_id}-sweep", bookmark_id=item.bookmark_id, title=item.title,
                      url=item.url, text=item.text, saved=item.created, page_date=None)
        turn.add_chunks([chunk])
        config = {"callbacks": self.callbacks, "recursion_limit": 30}
        async with self.sem:
            with turn_scope(turn):
                reply = await self.extract_model.ainvoke(
                    [SystemMessage(prompts.SWEEP), HumanMessage(f"[B1] {item.title}\n{item.url}\n\n{item.text}")],
                    config=config)
                claims = parse_claims(reply.text)
                results = await asyncio.gather(*(self.check(turn, c, i, config) for i, c in enumerate(claims, 1)))
        return {"bookmark_id": item.bookmark_id, "title": item.title, "url": item.url,
                "saved": item.created.isoformat() if item.created else None, "claims": list(results)}

    async def check(self, turn: TurnState, claim: str, index: int, config: dict[str, Any]) -> dict[str, Any]:
        description = f"Claim: {claim} [B1]. Question: is this claim still true?"
        try:
            out = await self.checker.ainvoke({"messages": [HumanMessage(description)]}, config=config)
            check = out.get("structured_response")
        except Exception as exc:  # noqa: BLE001 - one failed check must not stop the sweep
            return {"claim_id": f"C{index}", "claim": claim, "status": "not_verified", "path": "error",
                    "reason": type(exc).__name__}
        text, ref, res = self.fc.decide(turn, check if isinstance(check, ClaimCheck) else None, description)
        turn.claims.append(ClaimResult(f"C{index}", text, ref, res))
        return resolution_json(f"C{index}", text, ref, res, turn)

    async def run(self, items: list[SweepItem]) -> dict[str, Any]:
        run_id = uuid.uuid4().hex[:8]
        t0 = time.monotonic()
        rows = await asyncio.gather(*(self.one(item, run_id) for item in items))
        return {"run_id": run_id, "finished": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "elapsed_s": round(time.monotonic() - t0, 1), "bookmarks": rows}


def markdown_report(report: dict[str, Any]) -> str:
    lines = [f"# Freshness sweep {report['run_id']}", "", f"Finished: {report['finished']}", "",
             "| Bookmark | Saved | Claim | Verdict | Rule path | Live value | Live source |",
             "|---|---|---|---|---|---|---|"]
    for b in report["bookmarks"]:
        for c in b["claims"] or [{"claim": "(no volatile claim)", "status": "-", "path": "-"}]:
            live = c.get("live") or {}
            lines.append(f"| [{b['title'][:50]}]({b['url']}) | {b['saved']} | {c['claim'][:90]} | {c['status']} | "
                         f"{c.get('path', '-')} | {live.get('value') or ''} | {live.get('url') or ''} |")
    return "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> dict[str, Any]:  # pragma: no cover - needs the cluster
    from qdrant_client import AsyncQdrantClient

    from app.api import build_companion
    from app.config import get_settings
    from app.llm import make_model
    from app.prompts import PROMPT_VERSION
    from app.retrieval import hit_from_payload

    s = get_settings()
    companion, _, _, close = build_companion(s)
    toolbox = companion.toolbox
    http = companion.models["quick"].http_async_client
    models = {r: make_model(r, s, http, PROMPT_VERSION) for r in ("sweep", "verify")}
    qdrant = AsyncQdrantClient(url=s.qdrant_url, timeout=60)
    hits, offset = [], None
    while True:
        points, offset = await qdrant.scroll(s.collection, limit=512, offset=offset, with_payload=True)
        hits += [hit_from_payload(p.payload or {}) for p in points]
        if offset is None:
            break
    items = pick(hits, args.count)
    report = await Sweep(s, toolbox, models, callbacks=[companion.trace] if companion.trace else []).run(items)
    out = Path(args.report_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"sweep-{report['run_id']}.json").write_text(json.dumps(report, indent=2))
    (out / f"sweep-{report['run_id']}.md").write_text(markdown_report(report))
    (out / "latest.json").write_text(json.dumps(report, indent=2))
    await qdrant.close()
    await close()
    return report


def main() -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description="Freshness sweep over old bookmarks (batch)")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--report-dir", default="/data/sweep")
    report = asyncio.run(main_async(ap.parse_args()))
    print(json.dumps({"run_id": report["run_id"], "bookmarks": len(report["bookmarks"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
