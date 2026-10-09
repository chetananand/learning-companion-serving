"""Build replay scripts from capture files, and pick scripts for the traffic mixes (06-experiments.md, section 2).

A script is an ordered list of LLM calls. The replayer sends the calls of one script one after the
other, with one new session id, as the agent did. The capture labels come from the session ids of
the capture run (app.loadgen.questions): `b1-` unique questions, `b2-` popular questions,
`b3-` verified agent sessions, `sweep-` the freshness sweep.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Call:
    step: str
    body: dict[str, Any]

    @property
    def stream(self) -> bool:
        return bool(self.body.get("stream"))

    @property
    def has_tools(self) -> bool:
        return bool(self.body.get("tools"))


@dataclass
class Script:
    id: str
    kind: str  # draft | quick | verified | verify_loop | sweep
    request_class: str
    label: str  # b1 | b2 | b3 | sweep | other
    calls: list[Call] = field(default_factory=list)


def load_capture(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    records = []
    for path in paths:
        with Path(path).open(encoding="utf-8") as fh:
            records += [json.loads(line) for line in fh if line.strip()]
    return sorted(records, key=lambda r: r["ts"])


def label_of(session_id: str | None) -> str:
    sid = session_id or ""
    for prefix in ("b1", "b2", "b3", "sweep"):
        if sid.startswith(prefix + "-"):
            return prefix
    return "other"


def _first_user(body: dict[str, Any]) -> str:
    for m in body.get("messages", []):
        if m.get("role") == "user":
            return json.dumps(m.get("content"), sort_keys=True)
    return ""


def turn_scripts(records: list[dict[str, Any]]) -> list[Script]:
    """One script for each captured turn: every call of the turn, in time order."""
    by_turn: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_turn.setdefault(r.get("turn_id") or f"none-{id(r)}", []).append(r)
    out = []
    for turn_id, recs in by_turn.items():
        mode = recs[0].get("mode") or "quick"
        out.append(Script(f"turn-{turn_id}", "sweep" if mode == "sweep" else mode, recs[0].get("class", "interactive"),
                          label_of(recs[0].get("session_id")), [Call(r["step"], r["body"]) for r in recs]))
    return out


def draft_scripts(records: list[dict[str, Any]]) -> list[Script]:
    """M1: each answer call (S3) alone: streamed, no tools, a different chunk set each time."""
    out = []
    for i, r in enumerate(records):
        call = Call(r["step"], r["body"])
        if r["step"] in ("quick", "vision") and call.stream and not call.has_tools:
            out.append(Script(f"draft-{i}", "draft", "interactive", label_of(r.get("session_id")), [call]))
    return out


def verify_loops(records: list[dict[str, Any]]) -> list[Script]:
    """The S4 loop of each fact check: the `verify` calls of one subagent run, in order."""
    loops: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if r["step"] == "verify":
            key = hashlib.sha256(f"{r.get('turn_id')}|{_first_user(r['body'])}".encode()).hexdigest()[:16]
            loops.setdefault(key, []).append(r)
    return [Script(f"verify-{k}", "verify_loop", recs[0].get("class", "interactive"),
                   label_of(recs[0].get("session_id")), [Call(r["step"], r["body"]) for r in recs])
            for k, recs in loops.items()]


Picker = Callable[[random.Random], Script]


def _choice(pool: list[Script], name: str) -> Picker:
    if not pool:
        raise ValueError(f"the capture has no scripts for {name}")
    return lambda rng: rng.choice(pool)


def _weighted(parts: list[tuple[float, Picker]]) -> Picker:
    total = sum(w for w, _ in parts)

    def pick(rng: random.Random) -> Script:
        x = rng.random() * total
        for w, picker in parts:
            x -= w
            if x <= 0:
                return picker(rng)
        return parts[-1][1](rng)

    return pick


def mix_picker(name: str, records: list[dict[str, Any]]) -> Picker:
    """The traffic mixes. M3 is M1 plus the stale-metrics proxy, which is a cluster setting."""
    turns = turn_scripts(records)
    interactive = [s for s in turns if s.request_class == "interactive" and s.kind in ("quick", "verified")]
    if name in ("m1", "m3"):
        return _choice(draft_scripts(records), name)
    if name == "m2":
        popular = [s for s in interactive if s.label == "b2"] or interactive
        return _weighted([(0.6, _choice(verify_loops(records), "m2 verify loops")),
                          (0.4, _choice(popular, "m2 popular questions"))])
    if name in ("m4", "soak"):
        batch = [s for s in turns if s.kind == "sweep"]
        long_agent = sorted([s for s in interactive if s.kind == "verified"], key=lambda s: -len(s.calls))
        long_agent = long_agent[: max(1, len(long_agent) // 4)]
        return _weighted([(0.7, _choice(interactive, "m4 interactive")), (0.2, _choice(batch, "m4 batch")),
                          (0.1, _choice(long_agent, "m4 long agent sessions"))])
    raise ValueError(f"unknown mix {name}")
