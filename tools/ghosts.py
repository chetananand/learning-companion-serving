"""Count the ghosts of E7 from the Envoy access log (handout: "what becomes a ghost if I skip it?").

E7 clears the prefix cache of decode pod P at time T (vLLM POST /reset_prefix_cache). A ghost is a
request that the router sent to P for a prefix that P no longer had:
  - its session had cached tokens on P before T (so the router had a reason to send it there),
  - after T, the router sent it to P with no P/D split (the decider believed the prefix was cached),
  - P had less than one block cached for it (it computed the whole prompt again).
With the exact prefix index (KV events), the router learns about the clear, so the decider splits
and the ghosts stay near zero. With the approximate index, it does not.

Usage: python3 tools/ghosts.py --log metrics/<run>/envoy-access.log --pod 10.42.0.8 --after 2026-09-30T10:05:00Z \
         --out metrics/<run>/ghosts.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

BLOCK = 64
EMPTY = {"", "-", None}


def _ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _host(value: Any) -> str:
    if value in EMPTY:
        return ""
    first = str(value).split(",")[0].strip()
    return first.rsplit(":", 1)[0] if ":" in first else first


def records(lines: Iterable[str]) -> list[dict[str, Any]]:
    out = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if "decode_endpoint" in rec and str(rec.get("status")) == "200" and rec.get("start_time"):
            out.append(rec)
    return sorted(out, key=lambda r: r["start_time"])


def count(recs: list[dict[str, Any]], pod: str, after: dt.datetime, block: int = BLOCK) -> dict[str, Any]:
    warm_sessions: set[str] = set()
    after_recs = []
    for r in recs:
        on_pod = _host(r.get("decode_endpoint")) == pod
        if _ts(r["start_time"]) < after:
            if on_pod and _int(r.get("cached_input_tokens")) >= block and r.get("session") not in EMPTY:
                warm_sessions.add(r["session"])
        else:
            after_recs.append(r)
    eligible = [r for r in after_recs if r.get("session") in warm_sessions and _host(r.get("decode_endpoint")) == pod]
    split = [r for r in eligible if _host(r.get("prefill_host")) not in ("", pod)]
    ghosts = [r for r in eligible if r not in split and _int(r.get("cached_input_tokens")) < block]
    fb = [_int(r.get("first_byte_ms")) for r in ghosts]
    return {"pod": pod, "after": after.isoformat(), "warm_sessions": len(warm_sessions), "eligible": len(eligible),
            "split": len(split), "ghosts": len(ghosts), "ghost_ratio": round(len(ghosts) / max(1, len(eligible)), 4),
            "ghost_first_byte_ms_mean": round(sum(fb) / len(fb), 1) if fb else None,
            "ghost_input_tokens": sum(_int(r.get("input_tokens")) for r in ghosts)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Count the E7 ghosts from the Envoy access log")
    ap.add_argument("--log", required=True)
    ap.add_argument("--pod", required=True, help="the pod IP whose prefix cache was cleared")
    ap.add_argument("--after", required=True, help="the time of the clear, ISO 8601")
    ap.add_argument("--out")
    a = ap.parse_args()
    result = count(records(Path(a.log).read_text().splitlines()), a.pod, _ts(a.after))
    text = json.dumps(result, indent=1)
    if a.out:
        Path(a.out).write_text(text)
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
