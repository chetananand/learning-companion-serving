"""Spend guard: enforce the launch limits that the owner approved.

Approvals: 2026-09-27 (G0 and G1 in advance, DEBATE-LOG point 13), 2026-09-27 evening (sessions 1 and 2,
"don't do it overnight"; E9 "47 USD is ok"), and 2026-09-28 16:10 PDT (the day plan in lambda_ctl.py,
a 10-hour node 2 block, launches until 22:00). Limits (see the memory note and docs/budget-ledger.md):
  - no GPU overnight: from 23:00 to 07:00 America/Los_Angeles, every GPU instance terminates.
  - node 1, 2 x H100 SXM: at most 17 hours in total (15 until 2026-09-29 22:00 PDT).
  - 4 x H100 SXM (E9, and the one-node layout of ADR-005 revision 2): at most 6 hours in total (3 until
    2026-09-30 16:15 PDT).
  - node 2, 2 x A6000 or 1 x H100 (2026-09-29, no A6000 stock): at most 10 hours for each block and
    30 hours in total. The 1 x H100 of node 2 does not count in the H100 hours of node 1.
  - all launches under these approvals: at most 275 USD in total (225 until 2026-09-29 22:00 PDT). Over the
    limit, the H100 nodes terminate.
  - any other shape: not approved, so it terminates.

The guard polls the Lambda API each minute. It computes the hours of each instance from the launch
record in cluster/state/launches.jsonl (written by lambda_ctl.py launch) and the price of its shape.
It terminates an instance when a limit is reached, and it logs each check to metrics/spend-guard.jsonl.
A terminate step here needs no typed confirmation: the owner approved these terminate rules in advance.

Usage: python3 cluster/lambda/spend_guard.py [--once] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lambda_api import LambdaApiError, _request, get  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
LAUNCHES = REPO / "cluster" / "state" / "launches.jsonl"
LOG = REPO / "metrics" / "spend-guard.jsonl"
PACIFIC = ZoneInfo("America/Los_Angeles")

H100 = "gpu_2x_h100_sxm5"
H100X4 = "gpu_4x_h100_sxm5"
A6000 = "gpu_2x_a6000"
NODE2_H100 = "gpu_1x_h100_sxm5"  # node 2 when no 2 x A6000 has stock (the owner, 2026-09-29)
NODE2 = (A6000, NODE2_H100)
A100X8 = "gpu_8x_a100_80gb_sxm4"  # the one-node layout on 8 x A100 80 GB (the owner, 2026-10-01 about 18:23 PDT)
# Node 2 blocks with a longer approved limit (instance id -> hours). 2026-09-29 about 11:06 PDT, the owner: "yes" to
# 11 hours for the session 1 + 2 day (node 2 on 1 x H100, launched 09:44 PDT, so the block ends by 20:44 PDT).
# About 17:55 PDT, the owner: "bump up the hard limit to 22:30 PDT today". 09:44 to 22:30 PDT = 12.77 hours.
NODE2_BLOCK_H = {"f420b3a8d85f42ac8cc69d59784c3db0": 12.77}
# 2026-09-29 about 22:00 PDT, the owner: "yes" to 275 USD in total and 17 hours of 2 x H100 (the 3 Wed items and E9).
# 2026-09-30 16:15 PDT, the owner: "Take it now" (4 x H100 for the 3 items and E9): 6 hours of 4 x H100 (was 3.0).
LIMITS = {"h100_total_h": 17.0, "h100x4_total_h": 6.0, "a100x8_total_h": 4.5,
          "a6000_block_h": 10.0, "a6000_total_h": 30.0,
          "total_usd": 275.0, "night_start_hour": 23, "night_end_hour": 7}


def is_night(local_hour: int) -> bool:
    """23:00 to 07:00 Pacific: no GPU runs (the owner: "don't do it overnight")."""
    return local_hour >= LIMITS["night_start_hour"] or local_hour < LIMITS["night_end_hour"]


@dataclass
class Block:
    instance_id: str
    name: str
    shape: str
    start: float  # unix seconds
    end: float | None  # None while it runs
    usd_per_h: float

    def hours(self, now: float) -> float:
        return max(0.0, ((self.end or now) - self.start) / 3600)

    def usd(self, now: float) -> float:
        return self.hours(now) * self.usd_per_h


def load_blocks(running_ids: set[str], now: float) -> list[Block]:
    """Rebuild each block from the launch records. A block that no longer runs ends at its terminate record."""
    blocks: dict[str, Block] = {}
    if not LAUNCHES.exists():
        return []
    for line in LAUNCHES.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec["event"] == "launch":
            for iid in rec["instance_ids"]:
                blocks[iid] = Block(iid, rec["name"], rec["shape"], rec["ts"], None, rec["usd_per_h"])
        elif rec["event"] == "terminate":
            for iid in rec["instance_ids"]:
                if iid in blocks:
                    blocks[iid].end = rec["ts"]
    for b in blocks.values():
        if b.end is None and b.instance_id not in running_ids:
            b.end = now  # it stopped without a record: count it up to now, which is the safe side
    return list(blocks.values())


def decide(blocks: list[Block], now: float, local_hour: int) -> list[tuple[Block, str]]:
    """Return the running blocks to terminate, with the reason."""
    out: list[tuple[Block, str]] = []
    total_usd = sum(b.usd(now) for b in blocks)
    h100_h = sum(b.hours(now) for b in blocks if b.shape == H100)
    h100x4_h = sum(b.hours(now) for b in blocks if b.shape == H100X4)
    node2_h = sum(b.hours(now) for b in blocks if b.shape in NODE2)
    for b in blocks:
        if b.end is not None:
            continue
        if is_night(local_hour):
            out.append((b, f"no GPU overnight: {local_hour}:00 Pacific"))
        elif b.shape == H100 and h100_h >= LIMITS["h100_total_h"]:
            out.append((b, f"H100 hours {h100_h:.2f} >= {LIMITS['h100_total_h']}"))
        elif b.shape == H100X4 and h100x4_h >= LIMITS["h100x4_total_h"]:
            out.append((b, f"4 x H100 hours {h100x4_h:.2f} >= {LIMITS['h100x4_total_h']}"))
        elif b.shape == A100X8 and sum(x.hours(now) for x in blocks if x.shape == A100X8) >= LIMITS["a100x8_total_h"]:
            out.append((b, f"8 x A100 hours >= {LIMITS['a100x8_total_h']}"))
        elif b.shape in (H100, H100X4, A100X8) and total_usd >= LIMITS["total_usd"]:
            out.append((b, f"total spend {total_usd:.2f} USD >= {LIMITS['total_usd']}"))
        elif b.shape in NODE2 and b.hours(now) >= NODE2_BLOCK_H.get(b.instance_id, LIMITS["a6000_block_h"]):
            limit = NODE2_BLOCK_H.get(b.instance_id, LIMITS["a6000_block_h"])
            out.append((b, f"node 2 block {b.hours(now):.2f} h >= {limit}"))
        elif b.shape in NODE2 and node2_h >= LIMITS["a6000_total_h"]:
            out.append((b, f"node 2 hours {node2_h:.2f} >= {LIMITS['a6000_total_h']}"))
        elif b.shape not in (H100, H100X4, A100X8) + NODE2:
            out.append((b, f"shape {b.shape} is not in the approval"))
    return out


def minutes_left(blocks: list[Block], shape: str, now: float, local: dt.datetime) -> int:
    """Minutes until the first stop rule ends a running block of this shape: the night stop, the hour limit of
    the shape, or the USD limit (at the price of all running blocks). The session scripts use it, so that the
    evidence capture ends before the guard stops the node (cluster/sessions/thu-one.sh)."""
    night = local.replace(hour=LIMITS["night_start_hour"], minute=0, second=0, microsecond=0)
    left = [(night - local).total_seconds() / 60 if local < night else 0.0]
    hour_limit = {H100: "h100_total_h", H100X4: "h100x4_total_h", A100X8: "a100x8_total_h"}.get(shape)
    if hour_limit:
        used = sum(b.hours(now) for b in blocks if b.shape == shape)
        left.append((LIMITS[hour_limit] - used) * 60)
    rate = sum(b.usd_per_h for b in blocks if b.end is None)
    if rate > 0:
        left.append((LIMITS["total_usd"] - sum(b.usd(now) for b in blocks)) / rate * 60)
    return max(0, int(min(left)))


def terminate(ids: list[str]) -> dict:
    return _request("POST", "/instance-operations/terminate", {"instance_ids": ids})


def record(event: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(json.dumps(event) + "\n")


def check(dry_run: bool) -> dict:
    now = time.time()
    instances = get("/instances")["data"]
    running = {i["id"]: i for i in instances if i.get("status") in ("active", "booting")}
    blocks = load_blocks(set(running), now)
    local_hour = dt.datetime.now(PACIFIC).hour
    actions = decide(blocks, now, local_hour)
    unknown = [i for i in running.values() if i["id"] not in {b.instance_id for b in blocks}]
    summary = {"ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "local_hour": local_hour,
               "running": [{"id": i["id"], "name": i.get("name"), "shape": i["instance_type"]["name"]}
                           for i in running.values()],
               "unrecorded": [i["id"] for i in unknown],
               "total_usd": round(sum(b.usd(now) for b in blocks), 2),
               "h100_h": round(sum(b.hours(now) for b in blocks if b.shape == H100), 2),
               "h100x4_h": round(sum(b.hours(now) for b in blocks if b.shape == H100X4), 2),
               "a6000_h": round(sum(b.hours(now) for b in blocks if b.shape == A6000), 2),
               "node2_h100_h": round(sum(b.hours(now) for b in blocks if b.shape == NODE2_H100), 2),
               "terminate": [{"id": b.instance_id, "reason": why} for b, why in actions]}
    if actions and not dry_run:
        ids = [b.instance_id for b, _ in actions]
        summary["terminate_result"] = terminate(ids)
        with LAUNCHES.open("a") as fh:
            fh.write(json.dumps({"event": "terminate", "ts": now, "instance_ids": ids,
                                 "by": "spend_guard", "reasons": [why for _, why in actions]}) + "\n")
    record(summary)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--minutes-left", metavar="SHAPE", help="print the minutes until a stop rule ends SHAPE, and exit")
    a = ap.parse_args()
    if a.minutes_left:
        now = time.time()
        running = {i["id"] for i in get("/instances")["data"] if i.get("status") in ("active", "booting")}
        print(minutes_left(load_blocks(running, now), a.minutes_left, now, dt.datetime.now(PACIFIC)))
        return 0
    while True:
        try:
            s = check(a.dry_run)
            flag = "TERMINATE " + json.dumps(s["terminate"]) if s["terminate"] else "ok"
            print(f"{s['ts']} {flag} usd={s['total_usd']} h100_h={s['h100_h']} a6000_h={s['a6000_h']} "
                  f"running={len(s['running'])} unrecorded={s['unrecorded']}", flush=True)
            if s["terminate"] and not a.dry_run:
                print("SPEND GUARD TERMINATED AN INSTANCE", flush=True)
        except (LambdaApiError, OSError) as exc:
            print(f"guard check failed: {exc}", flush=True)
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
