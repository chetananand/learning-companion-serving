"""Watch Lambda capacity for our instance shapes. Read-only: it never launches.

Usage:
    python3 cluster/lambda/capacity_watch.py --interval 300 --max-hours 12

It writes one line for each poll to metrics/lambda-capacity.jsonl (the stock
history for the report). It exits with code 0 when a watched shape has
capacity, and with code 3 when the time limit ends.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lambda_api import LambdaApiError, capacity  # noqa: E402

DEFAULT_TYPES = "gpu_2x_a6000,gpu_2x_h100_sxm5,gpu_4x_h100_sxm5"
REPO = pathlib.Path(__file__).resolve().parents[2]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--types", default=DEFAULT_TYPES)
    p.add_argument("--interval", type=int, default=300, help="seconds between polls")
    p.add_argument("--max-hours", type=float, default=12.0)
    p.add_argument("--once", action="store_true", help="poll one time and exit")
    p.add_argument("--log", default=str(REPO / "metrics" / "lambda-capacity.jsonl"))
    args = p.parse_args()

    watched = [t.strip() for t in args.types.split(",") if t.strip()]
    log = pathlib.Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.max_hours * 3600

    while True:
        now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            cap = capacity()
            found = {t: cap.get(t, []) for t in watched if cap.get(t)}
            record = {"ts": now, "watched": {t: cap.get(t, []) for t in watched},
                      "any_type_with_capacity": sorted(k for k, v in cap.items() if v),
                      "regions": {k: sorted(v) for k, v in sorted(cap.items()) if v}}
            with log.open("a") as fh:
                fh.write(json.dumps(record) + "\n")
            if found:
                print(f"{now} CAPACITY: {json.dumps(found)}", flush=True)
                return 0
            print(f"{now} no capacity for {', '.join(watched)}", flush=True)
        except (LambdaApiError, OSError) as exc:
            print(f"{now} poll failed: {exc}", flush=True)
        if args.once or time.monotonic() > deadline:
            return 3
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
