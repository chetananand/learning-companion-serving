"""Build the load question sets from the corpus, and drive them through the Companion API (08-question-bank, part B).

We do not write the load prompts by hand. The sets come from the bookmark titles in Qdrant:
  b1: 200 unique questions (quick mode)          -> the M1 draft calls
  b2: 20 popular questions (the demo questions)  -> repeated in M2
  b3: 50 verified-mode questions                 -> the S4 verify loops (M2, M4)
A capture run (APP_CAPTURE_PATH set on the API) then saves each LLM request body for the replayer.

Run: python -m app.loadgen.questions build --out /data/prompts
     python -m app.loadgen.questions drive --sets /data/prompts/b1.jsonl --api http://companion-api...:8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from pathlib import Path
from typing import Any

import httpx

from app.demo_check import QUESTIONS

B1_TEMPLATES = ['What is the main idea of my bookmark "{t}"?', 'Summarize what "{t}" says, from my bookmarks.',
                'Which key facts does "{t}" give?']
B3_TEMPLATES = ['Which versions, numbers, or flags does "{t}" give, and are they still current?',
                'Is the advice in "{t}" still up to date?']
VOLATILE = re.compile(r"\b(v?\d+\.\d+(\.\d+)?|20\d\d)\b")


def build_sets(bookmarks: list[dict[str, Any]], seed: int = 11) -> dict[str, list[dict[str, str]]]:
    """bookmarks: one dictionary for each bookmark with `title` and `text` (a sample of its chunks)."""
    rng = random.Random(seed)
    usable = [b for b in bookmarks if len(b.get("title", "")) >= 8 and b.get("text")]
    rng.shuffle(usable)
    b1 = [{"set": "b1", "mode": "quick", "question": rng.choice(B1_TEMPLATES).format(t=b["title"][:120])}
          for b in usable[:200]]
    demo = [q for q in QUESTIONS if q.id <= "D-10"]
    b2 = [{"set": "b2", "mode": "quick", "question": q.text} for q in demo]
    b2 += [{"set": "b2", "mode": "quick", "question": B1_TEMPLATES[0].format(t=b["title"][:120])}
           for b in usable[200:210]]
    technical = [b for b in usable if len(VOLATILE.findall(b["text"])) >= 2]
    b3 = [{"set": "b3", "mode": "verified", "question": rng.choice(B3_TEMPLATES).format(t=b["title"][:120])}
          for b in technical[:50]]
    return {"b1": b1, "b2": b2, "b3": b3}


async def drive(api: str, questions: list[dict[str, str]], *, concurrency: int = 2,
                transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    """Send each question as one turn. The session id carries the set label for the capture."""
    sem = asyncio.Semaphore(concurrency)
    out: list[dict[str, Any]] = []
    timeout = httpx.Timeout(180.0, connect=5.0)
    async with httpx.AsyncClient(base_url=api, timeout=timeout, transport=transport) as client:

        async def one(i: int, q: dict[str, str]) -> None:
            body = {"question": q["question"], "mode": q["mode"], "session_id": f"{q['set']}-{i:04d}",
                    "tenant": "owner"}
            async with sem:
                done: dict[str, Any] = {}
                async with client.stream("POST", "/v1/turns", json=body) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            event = json.loads(line[5:])
                            if event["event"] in ("done", "error"):
                                done = {**done, **event}
                out.append({"i": i, "set": q["set"], **done})

        await asyncio.gather(*(one(i, q) for i, q in enumerate(questions)))
    return out


async def corpus(qdrant_url: str, collection: str) -> list[dict[str, Any]]:  # pragma: no cover - needs Qdrant
    from qdrant_client import AsyncQdrantClient

    client = AsyncQdrantClient(url=qdrant_url, timeout=60)
    by: dict[str, dict[str, Any]] = {}
    offset = None
    while True:
        points, offset = await client.scroll(collection, limit=512, offset=offset, with_payload=True)
        for p in points:
            pl = p.payload or {}
            if pl.get("dead") or pl.get("source") == "title":
                continue
            b = by.setdefault(pl["bookmark_id"], {"title": pl.get("title", ""), "text": ""})
            if len(b["text"]) < 4000:
                b["text"] += "\n" + pl.get("text", "")
        if offset is None:
            break
    await client.close()
    return list(by.values())


def main() -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Load question sets: build from the corpus, drive through the API")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", required=True)
    d = sub.add_parser("drive")
    d.add_argument("--sets", nargs="+", required=True)
    d.add_argument("--api", default="http://companion-api.companion.svc.cluster.local:8000")
    d.add_argument("--concurrency", type=int, default=2)
    a = ap.parse_args()
    if a.cmd == "build":
        from app.config import get_settings

        s = get_settings()
        sets = build_sets(asyncio.run(corpus(s.qdrant_url, s.collection)))
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        for name, rows in sets.items():
            (out / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            print(name, len(rows))
        return 0
    questions = [json.loads(line) for path in a.sets for line in Path(path).read_text().splitlines() if line.strip()]
    results = asyncio.run(drive(a.api, questions, concurrency=a.concurrency))
    ok = sum(1 for r in results if r.get("outcome") == "ok")
    print(f"{ok}/{len(results)} turns ok")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
