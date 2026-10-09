"""Lambda Cloud control for our two nodes (ADR-004, ADR-010).

Read-only commands run at once: plan, list. Commands that cost money or remove things
(launch, terminate, fs-create) need --approve AND a typed confirmation. The owner approves each
one (action A7). Claude never runs them without that approval.

Examples:
    python3 cluster/lambda/lambda_ctl.py plan
    python3 cluster/lambda/lambda_ctl.py list
    python3 cluster/lambda/lambda_ctl.py launch --node node2 --region us-east-1 --approve
    printf 'companion-node2\n' | python3 cluster/lambda/lambda_ctl.py terminate --name companion-node2 --approve
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lambda_api import LambdaApiError, _request, capacity, get, prices_usd_per_hour  # noqa: E402

SSH_KEY_NAME = "Lambda AI"  # ~/.ssh/lambda_ai
NODES = {
    "node1-t2": {"type": "gpu_2x_h100_sxm5", "name": "companion-node1", "role": "engine (2 x H100)"},
    "node1-t4": {"type": "gpu_4x_h100_sxm5", "name": "companion-node1", "role": "engine, scale session (4 x H100)"},
    "node2": {"type": "gpu_2x_a6000", "name": "companion-node2", "role": "control, data, guard (2 x A6000)"},
    # 2026-09-29 about 09:42 PDT, the owner: "1x H100 now". No 2 x A6000 had stock, and the node 2 GPU pods need 52 GB.
    "node2-h100": {"type": "gpu_1x_h100_sxm5", "name": "companion-node2",
                   "role": "control, data, guard (1 x H100 80 GB, when no 2 x A6000 has stock)"},
    # 2026-09-30 about 20:20 PDT, the owner: "yes, start now" (ADR-005, revision 2). All pods on one 4 x H100 node,
    # if no node 2 shape has stock on 2026-10-01. The bring-up is cluster/one.sh.
    "one-t4": {"type": "gpu_4x_h100_sxm5", "name": "companion-one",
               "role": "all pods on one node (4 x H100), when no node 2 shape has stock"},
    # 2026-10-01 about 18:23 PDT, the owner: "i say take it" (8 x A100 80 GB had stock, no H100 shape all evening).
    "one-a100": {"type": "gpu_8x_a100_80gb_sxm4", "name": "companion-one",
                 "role": "all pods on one node (8 x A100 80 GB), the owner's approval of 2026-10-01"},
}
NODE2_SHAPES = ("gpu_2x_a6000", "gpu_1x_h100_sxm5")
NODE1_SHAPES = ("gpu_2x_h100_sxm5", "gpu_4x_h100_sxm5")
STATE = pathlib.Path(__file__).resolve().parents[1] / "state"
LAUNCHES = STATE / "launches.jsonl"
# The approvals (the owner): 2026-09-27 (G0, G1) and 2026-09-28 16:10 PDT (the day plan below: "we can't take
# chances so I approve it", launches until 22:00). Each Pacific day lists the nodes that may launch. The spend
# guard (spend_guard.py) stops every GPU at 23:00 and enforces the hour and USD limits.
APPROVED_DAYS = {
    "2026-09-28": {"node2"},                                # G0
    "2026-09-29": {"node2", "node2-h100", "node1-t2"},      # session 1 (node2-h100: approved about 09:42 PDT)
    # 2026-09-29 about 21:50 PDT, the owner: "yes, approve the 1x H100 fallback for Wed and Thu but let us try to finish
    # E9 also". Wednesday: the 3 must-do items (demo fixes, E7 again, E3 with 32 seqs), then E9 if it fits.
    "2026-09-30": {"node2", "node2-h100", "node1-t2", "node1-t4"},
    "2026-10-01": {"node2", "node2-h100", "node1-t2", "node1-t4", "one-t4", "one-a100"},  # the 3 must-do items, E9
}
# The owner's exception to "node 1 only when node 2 is active" (asked 2026-09-30 13:07 PDT, "Take it now" at 16:15 PDT):
# one launch of 4 x H100 before node 2, with a wait of 45 min for node 2. It was used at 17:04 PDT (no node 2 came,
# node 1 stopped at 17:49 PDT). A new use needs a new approval: add its Pacific day here.
BEFORE_NODE2_DAYS: set[str] = set()
LAUNCH_WINDOW = (7, 22)  # Pacific: launches only from 07:00 to 22:00, and every GPU stops at 23:00


def log_event(event: dict) -> None:
    STATE.mkdir(exist_ok=True)
    with LAUNCHES.open("a") as fh:
        fh.write(json.dumps(event) + "\n")


HOLD = STATE / "HOLD"  # while this file exists, every launch waits (the file gives the reason)


def launch_rule_problem(node_key: str, local: dt.datetime, node2_active: bool,
                        before_node2: bool = False) -> str | None:
    """The day plan, the launch window, and the order of the nodes. Return the reason to refuse, or None."""
    day = local.date().isoformat()
    allowed = APPROVED_DAYS.get(day, set())
    if node_key not in allowed:
        return f"{node_key} is not approved for {day} (approved that day: {', '.join(sorted(allowed)) or 'nothing'})"
    if not LAUNCH_WINDOW[0] <= local.hour < LAUNCH_WINDOW[1]:
        return "launches only from 07:00 to 22:00 Pacific (every GPU stops at 23:00)"
    if before_node2 and day not in BEFORE_NODE2_DAYS:
        return "the exception --before-node2 is not approved for this day (it was for one launch on 2026-09-30)"
    if node_key.startswith("node1") and not node2_active and not before_node2:
        return "node 1 launches only when node 2 is active (the H100 must not wait for node 2)"
    return None


def layout_problem(node_key: str, active_names: set[str], cap: dict[str, list[str]]) -> str | None:
    """The one-node layout and the two-node layout never run together. The one node launches only when no
    region has stock for both a node 2 shape and a node 1 shape (the owner's condition). Return the reason or None."""
    if node_key == "one-a100":  # the owner approved it without the stock condition of one-t4
        if active_names & {NODES["node1-t2"]["name"], NODES["node2"]["name"]}:
            return "a node of the two-node layout is active"
        return None
    if node_key == "one-t4":
        if active_names & {NODES["node1-t2"]["name"], NODES["node2"]["name"]}:
            return "a node of the two-node layout is active"
        node2 = {r for t in NODE2_SHAPES for r in cap.get(t, [])}
        node1 = {r for t in NODE1_SHAPES for r in cap.get(t, [])}
        if pair := sorted(node2 & node1):
            return f"the two-node layout has stock in {', '.join(pair)}: launch node 2, then node 1"
    elif NODES["one-t4"]["name"] in active_names:
        return "the one-node layout is active"
    return None


def approval_problem(node_key: str, before_node2: bool = False) -> str | None:
    """Check the rules of the approvals. Return the reason to refuse, or None."""
    if HOLD.exists():
        return f"launches are on hold: {HOLD.read_text().strip()}"
    active = {i.get("name") for i in get("/instances")["data"] if i.get("status") in ("active", "booting")}
    node2_active = NODES["node2"]["name"] in active if node_key.startswith("node1") else True
    if (why := layout_problem(node_key, active, capacity())):
        return why
    if (why := launch_rule_problem(node_key, dt.datetime.now(ZoneInfo("America/Los_Angeles")), node2_active,
                                   before_node2)):
        return why
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import spend_guard

    now = time.time()
    blocks = spend_guard.load_blocks(set(), now)
    used_usd = sum(b.usd(now) for b in blocks)
    h100_h = sum(b.hours(now) for b in blocks if b.shape == spend_guard.H100)
    h100x4_h = sum(b.hours(now) for b in blocks if b.shape == spend_guard.H100X4)
    if used_usd >= spend_guard.LIMITS["total_usd"] - 10:
        return f"the approved budget is almost used: {used_usd:.2f} of {spend_guard.LIMITS['total_usd']} USD"
    if node_key == "node1-t2" and h100_h >= spend_guard.LIMITS["h100_total_h"] - 0.5:
        return f"the approved H100 hours are almost used: {h100_h:.2f} h"
    if node_key in ("node1-t4", "one-t4") and h100x4_h >= spend_guard.LIMITS["h100x4_total_h"] - 0.5:
        return f"the approved 4 x H100 hours are almost used: {h100x4_h:.2f} h"
    return None


def confirm(expected: str) -> bool:
    typed = input(f"Type '{expected}' to confirm: ").strip()
    return typed == expected


def cmd_plan(_: argparse.Namespace) -> int:
    cap, price = capacity(), prices_usd_per_hour()
    for key, node in NODES.items():
        regions = cap.get(node["type"], [])
        print(f"{key:9s} {node['type']:18s} {price.get(node['type'], 0):6.2f} USD/h  "
              f"capacity now: {', '.join(regions) or 'NONE'}  ({node['role']})")
    both = set(cap.get(NODES["node2"]["type"], [])) & set(cap.get(NODES["node1-t2"]["type"], []))
    print(f"regions with both node2 and node1-t2 now: {', '.join(sorted(both)) or 'NONE'}")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    for inst in get("/instances")["data"]:
        print(f"{inst.get('name')} id={inst['id']} {inst['instance_type']['name']} {inst['region']['name']} "
              f"ip={inst.get('ip')} private_ip={inst.get('private_ip')} status={inst['status']}")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    node = NODES[args.node]
    price = prices_usd_per_hour().get(node["type"], 0.0)
    body = {"region_name": args.region, "instance_type_name": node["type"], "ssh_key_names": [SSH_KEY_NAME],
            "quantity": 1, "name": node["name"]}
    if args.filesystem:
        body["file_system_names"] = [args.filesystem]
    print(f"LAUNCH {node['type']} ({node['role']}) in {args.region} at {price:.2f} USD/h:")
    print(json.dumps(body, indent=2))
    if (why := approval_problem(args.node, getattr(args, "before_node2", False))) is not None:
        print(f"Not launched: {why}.")
        return 1
    if not args.approve or not confirm(node["type"]):
        print("Not launched (dry run).")
        return 1
    data = _request("POST", "/instance-operations/launch", body)["data"]
    STATE.mkdir(exist_ok=True)
    (STATE / f"{node['name']}.json").write_text(json.dumps(data, indent=2))
    log_event({"event": "launch", "ts": time.time(), "instance_ids": data.get("instance_ids", []),
               "name": node["name"], "shape": node["type"], "region": args.region, "usd_per_h": price})
    print(f"Launched: {data}")
    return 0


def cmd_terminate(args: argparse.Namespace) -> int:
    matches = [i for i in get("/instances")["data"] if i.get("name") == args.name or i["id"] == args.name]
    if not matches:
        print(f"No instance named {args.name}.")
        return 1
    ids = [i["id"] for i in matches]
    print(f"TERMINATE {args.name}: {ids}")
    if not args.approve or not confirm(args.name):
        print("Not terminated (dry run).")
        return 1
    print(_request("POST", "/instance-operations/terminate", {"instance_ids": ids}))
    log_event({"event": "terminate", "ts": time.time(), "instance_ids": ids, "by": "lambda_ctl"})
    return 0


def cmd_fs_create(args: argparse.Namespace) -> int:
    body = {"name": args.name, "region": args.region}
    print(f"CREATE FILESYSTEM {body} (0.20 USD for each GB each month)")
    if not args.approve or not confirm(args.name):
        print("Not created (dry run).")
        return 1
    print(_request("POST", "/file-systems", body))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan").set_defaults(fn=cmd_plan)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("launch")
    p.add_argument("--node", choices=sorted(NODES), required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--filesystem")
    p.add_argument("--approve", action="store_true")
    p.add_argument("--before-node2", action="store_true", help="the owner's exception (BEFORE_NODE2_DAYS)")
    p.set_defaults(fn=cmd_launch)
    p = sub.add_parser("terminate")
    p.add_argument("--name", required=True)
    p.add_argument("--approve", action="store_true")
    p.set_defaults(fn=cmd_terminate)
    p = sub.add_parser("fs-create")
    p.add_argument("--name", default="companion-state")
    p.add_argument("--region", required=True)
    p.add_argument("--approve", action="store_true")
    p.set_defaults(fn=cmd_fs_create)
    args = ap.parse_args()
    try:
        return args.fn(args)
    except LambdaApiError as exc:
        print(f"Lambda API error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
