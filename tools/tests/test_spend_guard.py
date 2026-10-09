"""Tests of the spend guard rules (the approval in advance, DEBATE-LOG point 13)."""

import json
import sys
from pathlib import Path

LAMBDA = Path(__file__).resolve().parents[2] / "cluster" / "lambda"
sys.path.insert(0, str(LAMBDA))
import spend_guard as sg  # noqa: E402

HOUR = 3600.0
NOW = 1_800_000_000.0


def block(shape, hours, usd_per_h, ended=False, iid="i1"):
    start = NOW - hours * HOUR
    return sg.Block(iid, "n", shape, start, NOW if ended else None, usd_per_h)


def reasons(blocks, local_hour=15):
    return [(b.instance_id, why) for b, why in sg.decide(blocks, NOW, local_hour)]


def test_h100_stops_at_the_hour_limit():
    limit = sg.LIMITS["h100_total_h"]
    assert reasons([block(sg.H100, limit - 0.1, 8.38)]) == []
    [(iid, why)] = reasons([block(sg.H100, limit, 8.38)])
    assert iid == "i1" and "H100 hours" in why


def test_h100_stops_when_the_total_spend_reaches_the_limit():
    old = block(sg.A6000, 20.0, 2.18, ended=True, iid="old")         # 43.60 USD
    e9 = block(sg.H100X4, 2.9, 16.36, ended=True, iid="e9")          # 47.44 USD
    assert reasons([old, e9, block(sg.H100, 10.0, 8.38, iid="h")]) == []  # 174.84 USD
    # A more expensive shape in the past (outside the H100 hours): the total passes 275 USD.
    big = block("gpu_8x_h100_sxm5", 3.2, 31.92, ended=True, iid="big")  # 102.14 USD
    out = reasons([old, e9, big, block(sg.H100, 10.0, 8.38, iid="h2")])  # 276.98 USD
    assert out == [("h2", out[0][1])] and "total spend" in out[0][1]


def test_4x_h100_for_e9_stops_at_its_hour_limit():
    limit = sg.LIMITS["h100x4_total_h"]
    assert reasons([block(sg.H100X4, limit - 0.1, 16.36)]) == []
    assert "4 x H100 hours" in reasons([block(sg.H100X4, limit, 16.36)])[0][1]


def test_a6000_rules():
    assert reasons([block(sg.A6000, 9.9, 2.18)]) == []
    assert "node 2 block" in reasons([block(sg.A6000, 10.0, 2.18)])[0][1]
    done = block(sg.A6000, sg.LIMITS["a6000_total_h"] - 4.0, 2.18, ended=True, iid="done")
    assert "node 2 hours" in reasons([done, block(sg.A6000, 4.0, 2.18, iid="now")])[0][1]


def test_a_1x_h100_node2_has_the_node2_limits_and_not_the_h100_hours():
    assert reasons([block(sg.NODE2_H100, 9.9, 4.29)]) == []
    assert "node 2 block" in reasons([block(sg.NODE2_H100, 10.0, 4.29)])[0][1]
    assert "overnight" in reasons([block(sg.NODE2_H100, 1.0, 4.29)], local_hour=23)[0][1]
    near = block(sg.H100, sg.LIMITS["h100_total_h"] - 0.5, 8.38, iid="h")
    assert reasons([near, block(sg.NODE2_H100, 5.0, 4.29, iid="n2")]) == []  # node 2 hours are not H100 hours
    done = block(sg.A6000, sg.LIMITS["a6000_total_h"] - 4.0, 2.18, ended=True, iid="done")
    assert "node 2 hours" in reasons([done, block(sg.NODE2_H100, 4.0, 4.29, iid="now")])[0][1]


def test_no_gpu_overnight():
    for hour in (23, 0, 3, 6):
        for shape, price in ((sg.H100, 8.38), (sg.A6000, 2.18)):
            assert "overnight" in reasons([block(shape, 1.0, price)], local_hour=hour)[0][1]
    for hour in (7, 12, 22):
        assert reasons([block(sg.H100, 1.0, 8.38), block(sg.A6000, 1.0, 2.18, iid="a")], local_hour=hour) == []


def test_a_shape_outside_the_approval_stops():
    assert "not in the approval" in reasons([block("gpu_8x_h100_sxm5", 0.1, 31.92)])[0][1]


def test_load_blocks_reads_launch_and_terminate_records(tmp_path, monkeypatch):
    f = tmp_path / "launches.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"event": "launch", "ts": NOW - 2 * HOUR, "instance_ids": ["a"], "name": "companion-node2",
         "shape": sg.A6000, "usd_per_h": 2.18},
        {"event": "terminate", "ts": NOW - HOUR, "instance_ids": ["a"]},
        {"event": "launch", "ts": NOW - HOUR, "instance_ids": ["b"], "name": "companion-node1",
         "shape": sg.H100, "usd_per_h": 8.38},
        {"event": "launch", "ts": NOW - HOUR, "instance_ids": ["c"], "name": "companion-node2",
         "shape": sg.A6000, "usd_per_h": 2.18},
    ]) + "\n")
    monkeypatch.setattr(sg, "LAUNCHES", f)
    blocks = {b.instance_id: b for b in sg.load_blocks({"b"}, NOW)}
    assert blocks["a"].hours(NOW) == 1.0 and blocks["b"].end is None
    assert blocks["c"].end == NOW  # gone without a record: counted up to now
    assert round(sum(b.usd(NOW) for b in blocks.values()), 2) == round(2.18 + 8.38 + 2.18, 2)


def test_one_approved_node2_block_has_11_hours():
    """2026-09-29: the owner approved a longer node 2 block (until 22:30 PDT). Other blocks keep 10 hours."""
    iid = "f420b3a8d85f42ac8cc69d59784c3db0"
    assert sg.NODE2_BLOCK_H[iid] == 12.77  # until 22:30 PDT (the owner, 2026-09-29 about 17:55 PDT)
    assert reasons([block(sg.NODE2_H100, 12.5, 4.29, iid=iid)]) == []
    assert "node 2 block" in reasons([block(sg.NODE2_H100, 12.77, 4.29, iid=iid)])[0][1]
    assert "node 2 block" in reasons([block(sg.NODE2_H100, 10.5, 4.29, iid="other")])[0][1]


def test_minutes_left_takes_the_first_stop_rule():
    import datetime as dt
    la = sg.PACIFIC
    now = 1_000_000.0
    used_4x = sg.Block("old", "companion-node1", sg.H100X4, now - 0.75 * 3600 - 7200, now - 7200, 16.36)  # 2026-09-30
    one = sg.Block("one", "companion-one", sg.H100X4, now - 3600, None, 16.36)  # runs for 1 h
    blocks = [used_4x, one]
    # 6 h limit - 0.75 h - 1 h = 4.25 h left (255 min), before the 23:00 stop at 08:00 Pacific
    assert sg.minutes_left(blocks, sg.H100X4, now, dt.datetime(2026, 10, 1, 8, 0, tzinfo=la)) == 255
    # at 21:00 Pacific, the night stop comes first (120 min)
    assert sg.minutes_left(blocks, sg.H100X4, now, dt.datetime(2026, 10, 1, 21, 0, tzinfo=la)) == 120
    # the USD limit comes first when little budget is left
    # 29 h x 8.38 + 1 h x 16.36 = 259.38 USD: 15.62 USD left at 16.36 USD/h = 57 min
    rich = [sg.Block("x", "companion-node1", sg.H100, now - 30 * 3600, now - 3600, 8.38), one]
    assert sg.minutes_left(rich, sg.H100X4, now, dt.datetime(2026, 10, 1, 8, 0, tzinfo=la)) == 57
