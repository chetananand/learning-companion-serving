"""Tests of the launch rules of lambda_ctl.py: the day plan, the launch window, and the order of the nodes."""

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

LAMBDA = Path(__file__).resolve().parents[2] / "cluster" / "lambda"
sys.path.insert(0, str(LAMBDA))
import lambda_ctl as lc  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")


def at(day: str, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}").replace(tzinfo=PT)


def test_the_day_plan():
    assert lc.launch_rule_problem("node2", at("2026-09-28", 16), node2_active=False) is None  # G0 today
    assert "not approved for 2026-09-28" in lc.launch_rule_problem("node1-t2", at("2026-09-28", 16), True)
    assert lc.launch_rule_problem("node1-t2", at("2026-09-29", 10), True) is None             # session 1
    assert lc.launch_rule_problem("node1-t2", at("2026-09-30", 10), True) is None             # session 2
    assert lc.launch_rule_problem("node1-t4", at("2026-10-01", 10), True) is None             # E9
    assert "nothing" in lc.launch_rule_problem("node2", at("2026-10-02", 10), False)          # report day


def test_a_1x_h100_node2_is_approved_from_2026_09_29_to_10_01():
    assert lc.NODES["node2-h100"]["name"] == lc.NODES["node2"]["name"]  # node 1 sees it as node 2
    for day in ("2026-09-29", "2026-09-30", "2026-10-01"):
        assert lc.launch_rule_problem("node2-h100", at(day, 10), False) is None
    assert "nothing" in lc.launch_rule_problem("node2-h100", at("2026-10-02", 10), False)  # report day


def test_e9_may_run_on_wednesday_too():
    assert lc.launch_rule_problem("node1-t4", at("2026-09-30", 10), True) is None


def test_the_launch_window_is_07_to_22_pacific():
    assert lc.launch_rule_problem("node2", at("2026-09-28", 21, 59), False) is None
    assert "07:00 to 22:00" in lc.launch_rule_problem("node2", at("2026-09-28", 22), False)
    assert "07:00 to 22:00" in lc.launch_rule_problem("node2", at("2026-09-29", 6, 59), False)


def test_node1_waits_for_node2():
    assert "node 2 is active" in lc.launch_rule_problem("node1-t2", at("2026-09-29", 10), node2_active=False)


def test_the_before_node2_exception_was_for_one_launch_only():
    # Used on 2026-09-30 at 17:04 PDT. A new use needs a new approval (a day in BEFORE_NODE2_DAYS).
    assert not lc.BEFORE_NODE2_DAYS
    for day in ("2026-09-30", "2026-10-01"):
        assert "not approved" in lc.launch_rule_problem("node1-t4", at(day, 18), False, before_node2=True)
        assert lc.launch_rule_problem("node1-t4", at(day, 18), True) is None  # with node 2 active: the day plan


def test_the_one_node_layout_is_approved_on_thursday_only_and_needs_no_node2():
    assert lc.launch_rule_problem("one-t4", at("2026-10-01", 7, 5), node2_active=False) is None
    assert "not approved" in lc.launch_rule_problem("one-t4", at("2026-09-30", 18), node2_active=False)
    assert lc.NODES["one-t4"]["type"] == "gpu_4x_h100_sxm5" and lc.NODES["one-t4"]["name"] == "companion-one"


def test_one_node_only_when_no_region_has_a_two_node_pair():
    only_4x = {"gpu_4x_h100_sxm5": ["us-southeast-1"]}
    assert lc.layout_problem("one-t4", set(), only_4x) is None
    pair = {**only_4x, "gpu_1x_h100_sxm5": ["us-southeast-1"]}
    assert "two-node layout has stock" in lc.layout_problem("one-t4", set(), pair)
    other_region = {**only_4x, "gpu_2x_a6000": ["us-south-2"]}  # no pair in one region: one node is right
    assert lc.layout_problem("one-t4", set(), other_region) is None


def test_the_two_layouts_never_run_together():
    assert "two-node layout is active" in lc.layout_problem("one-t4", {"companion-node2"}, {})
    assert "one-node layout is active" in lc.layout_problem("node2-h100", {"companion-one"}, {})
    assert "one-node layout is active" in lc.layout_problem("node1-t4", {"companion-one"}, {})
    assert lc.layout_problem("node1-t2", {"companion-node2"}, {}) is None
