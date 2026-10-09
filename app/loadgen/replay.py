"""The trace replayer (06-experiments.md, section 2): replay captured app calls against `edge`.

- Poisson arrivals of scripts at a target rate (scripts each second), for a set duration.
- A soak mode adds `--ramp` scripts each second for each minute.
- Each script keeps its call order and gets one new session id, so the session affinity stays real.
- Interactive scripts use the tenants `load-1` to `load-32`. Batch scripts use the tenant `sweep`.
- An abort probability closes a stream in the middle (E12).
- Output: `client.jsonl` (one line for each call) and `summary.json` in the run folder.

Run: python -m app.loadgen.replay --capture /data/capture/*.jsonl --mix m4 --rate 2 --duration 600 \
       --target http://edge.companion.svc.cluster.local:8080/v1 --out /data/runs/e3-m4-100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.loadgen.scripts import Call, Picker, Script, load_capture, mix_picker

DEADLINE_MS = {"interactive": 30000, "batch": 120000}
# The load tenants of control/router/policy.yaml (load_tenants.count). Many tenants, so that the load
# reaches the engine limit before the tenant windows (each window stays a real per-user limit).
LOAD_TENANTS = [f"load-{i}" for i in range(1, 33)]


@dataclass
class ReplayConfig:
    target: str
    mix: str
    rate: float
    duration_s: float
    out_dir: Path
    ramp_per_min: float = 0.0
    abort_p: float = 0.0
    tenants: list[str] = field(default_factory=lambda: list(LOAD_TENANTS))
    batch_tenant: str = "sweep"
    seed: int = 7
    max_in_flight: int = 512
    allow_overflow: bool = True
    drain_s: float = 120.0
    extra: list[tuple[str, float]] = field(default_factory=list)  # E10: (tenant, scripts each second)


@dataclass
class CallResult:
    script_id: str
    run_session: str
    call_index: int
    step: str
    request_class: str
    tenant: str
    stream: bool
    t_start: float
    status: int = 0
    reason: str = ""
    via: str = ""
    ttft_s: float | None = None
    latency_s: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    aborted: bool = False


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return round(s[k], 4)


def summarize(results: list[CallResult], wall_s: float) -> dict[str, Any]:
    out: dict[str, Any] = {"calls": len(results), "wall_s": round(wall_s, 1), "by_class": {}}
    for cls in sorted({r.request_class for r in results}):
        rs = [r for r in results if r.request_class == cls]
        ok = [r for r in rs if r.status == 200 and not r.aborted]
        reasons: dict[str, int] = {}
        for r in rs:
            if r.status != 200:
                reasons[f"{r.status} {r.reason}"] = reasons.get(f"{r.status} {r.reason}", 0) + 1
        out["by_class"][cls] = {
            "calls": len(rs), "ok": len(ok), "aborted": sum(r.aborted for r in rs), "sheds": reasons,
            "overflow": sum(r.via == "overflow" for r in rs),
            "ttft_p50_s": percentile([r.ttft_s for r in ok if r.ttft_s is not None], 0.5),
            "ttft_p95_s": percentile([r.ttft_s for r in ok if r.ttft_s is not None], 0.95),
            "latency_p50_s": percentile([r.latency_s for r in ok if r.latency_s is not None], 0.5),
            "latency_p95_s": percentile([r.latency_s for r in ok if r.latency_s is not None], 0.95),
            "output_tokens": sum(r.output_tokens or 0 for r in ok),
            "prompt_tokens": sum(r.prompt_tokens or 0 for r in ok),
        }
    return out


class Replayer:
    def __init__(self, config: ReplayConfig, picker: Picker, *, transport: httpx.AsyncBaseTransport | None = None):
        self.c = config
        self.picker = picker
        self.rng = random.Random(config.seed)
        self.http = httpx.AsyncClient(base_url=config.target.rstrip("/"), transport=transport,
                                      timeout=httpx.Timeout(300.0, connect=5.0),
                                      limits=httpx.Limits(max_connections=config.max_in_flight))
        self.results: list[CallResult] = []
        self._sem = asyncio.Semaphore(config.max_in_flight)
        self._tenant_i = 0
        self.config_out = config.out_dir
        self.config_out.mkdir(parents=True, exist_ok=True)
        self._log = (config.out_dir / "client.jsonl").open("a", encoding="utf-8")

    def _tenant(self, script: Script) -> str:
        if script.request_class == "batch":
            return self.c.batch_tenant
        self._tenant_i += 1
        return self.c.tenants[self._tenant_i % len(self.c.tenants)]

    def headers(self, call: Call, script: Script, tenant: str, session: str, prompt_version: str | None) -> dict:
        return {"X-Request-Id": uuid.uuid4().hex, "X-Tenant-Id": tenant, "X-Session-Id": session,
                "X-Request-Class": script.request_class, "X-Step": call.step,
                "X-Deadline-Ms": str(DEADLINE_MS.get(script.request_class, 30000)),
                "X-Allow-Overflow": "true" if self.c.allow_overflow else "false",
                "X-Prompt-Version": prompt_version or "replay"}

    async def call(self, script: Script, index: int, call: Call, tenant: str, session: str) -> CallResult:
        res = CallResult(script.id, session, index, call.step, script.request_class, tenant, call.stream, time.time())
        t0 = time.monotonic()
        abort_after = self.rng.randint(1, 20) if call.stream and self.rng.random() < self.c.abort_p else None
        headers = self.headers(call, script, tenant, session, None)
        try:
            async with self.http.stream("POST", "/chat/completions", json=call.body, headers=headers) as resp:
                res.status, res.via = resp.status_code, resp.headers.get("x-companion-via", "")
                if resp.status_code != 200:
                    body = await resp.aread()
                    try:
                        res.reason = json.loads(body).get("error", {}).get("type", "")
                    except ValueError:
                        res.reason = body[:80].decode(errors="replace")
                elif call.stream:
                    chunks = 0
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:") or line.strip() == "data: [DONE]":
                            continue
                        data = json.loads(line[5:])
                        choice = (data.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        if res.ttft_s is None and (delta.get("content") or delta.get("tool_calls")):
                            res.ttft_s = round(time.monotonic() - t0, 4)
                        if data.get("usage"):
                            res.prompt_tokens = data["usage"].get("prompt_tokens")
                            res.output_tokens = data["usage"].get("completion_tokens")
                        chunks += 1
                        if abort_after is not None and chunks >= abort_after:
                            res.aborted = True
                            break  # closing the stream makes the proxy abort the request (H-72)
                else:
                    data = json.loads(await resp.aread())
                    usage = data.get("usage") or {}
                    res.prompt_tokens, res.output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
        except httpx.HTTPError as exc:
            res.status, res.reason = 599, type(exc).__name__
        res.latency_s = round(time.monotonic() - t0, 4)
        self._log.write(json.dumps(asdict(res)) + "\n")
        self.results.append(res)
        return res

    async def run_script(self, script: Script, tenant: str | None = None) -> None:
        async with self._sem:
            tenant, session = tenant or self._tenant(script), f"replay-{uuid.uuid4().hex[:12]}"
            for i, call in enumerate(script.calls):
                res = await self.call(script, i, call, tenant, session)
                if res.status != 200 or res.aborted:
                    return  # the agent stops the turn when a call fails

    async def _arrivals(self, t0: float, rate_of: Any, tenant: str | None, tasks: set[asyncio.Task],
                        rng: random.Random) -> None:
        while (elapsed := time.monotonic() - t0) < self.c.duration_s:
            await asyncio.sleep(rng.expovariate(rate_of(elapsed)))
            script = self.picker(rng)
            if tenant is not None and script.request_class != "interactive":
                continue  # an extra tenant stream sends user turns only
            task = asyncio.create_task(self.run_script(script, tenant))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def run(self) -> dict[str, Any]:
        t0, t_start = time.monotonic(), time.time()
        tasks: set[asyncio.Task] = set()
        loops = [self._arrivals(t0, lambda e: self.c.rate + self.c.ramp_per_min * int(e // 60), None, tasks, self.rng)]
        for i, (tenant, rate) in enumerate(self.c.extra):
            loops.append(self._arrivals(t0, lambda e, r=rate: r, tenant, tasks, random.Random(self.c.seed + 1 + i)))
        await asyncio.gather(*loops)
        if tasks:
            await asyncio.wait(tasks, timeout=self.c.drain_s)
            for task in tasks:
                task.cancel()
        summary = summarize(self.results, time.monotonic() - t0)
        summary["t_start"] = t_start  # the soak minutes count from here (notebook knee_rate)
        summary["config"] = {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(self.c).items()}
        (self.c.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        self._log.close()
        await self.http.aclose()
        return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay captured app calls against edge")
    ap.add_argument("--capture", nargs="+", required=True)
    ap.add_argument("--mix", choices=["m1", "m2", "m3", "m4", "soak"], required=True)
    ap.add_argument("--rate", type=float, required=True, help="scripts each second")
    ap.add_argument("--duration", type=float, default=600)
    ap.add_argument("--ramp", type=float, default=0.0, help="more scripts each second, for each minute (soak)")
    ap.add_argument("--abort", type=float, default=0.0, help="probability to close a stream in the middle")
    ap.add_argument("--tenants", default=",".join(LOAD_TENANTS))
    ap.add_argument("--no-overflow", action="store_true")
    ap.add_argument("--extra", action="append", default=[], metavar="TENANT:RATE",
                    help="E10: one more stream of user turns for one tenant, for example noisy:8")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--target", default="http://edge.companion.svc.cluster.local:8080/v1")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = ReplayConfig(target=a.target, mix=a.mix, rate=a.rate, duration_s=a.duration, out_dir=Path(a.out),
                       ramp_per_min=a.ramp, abort_p=a.abort, tenants=a.tenants.split(","), seed=a.seed,
                       allow_overflow=not a.no_overflow,
                       extra=[(e.split(":")[0], float(e.split(":")[1])) for e in a.extra])
    picker = mix_picker(a.mix, load_capture(a.capture))
    summary = asyncio.run(Replayer(cfg, picker).run())
    print(json.dumps(summary["by_class"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
