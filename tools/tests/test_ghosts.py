"""Tests of the E7 ghost count."""

import datetime as dt
import json

from tools.ghosts import count, records

T = dt.datetime(2026, 9, 30, 10, 5, tzinfo=dt.UTC)


def line(minute, session, cached, prefill="-", pod="10.0.0.8:8000", status="200"):
    return json.dumps({"start_time": f"2026-09-30T10:{minute:02d}:00Z", "session": session, "status": status,
                       "decode_endpoint": pod, "prefill_host": prefill, "input_tokens": "9000",
                       "cached_input_tokens": str(cached), "first_byte_ms": "900"})


def test_ghosts_after_a_clear():
    lines = [
        line(1, "s1", 4096), line(2, "s2", 4096), line(3, "s3", 0),  # s1 and s2 were warm on the pod
        line(6, "s1", 0),                                   # ghost: no split, nothing cached
        line(6, "s2", 8900, prefill="10.0.0.7:8000"),       # split: the router knew, not a ghost
        line(7, "s3", 0),                                   # s3 was never warm there: not eligible
        line(7, "s1", 0, pod="10.0.0.9:8000"),              # another pod: not eligible
        line(8, "s1", 0, status="503"),                     # a failed request: ignored
    ]
    out = count(records(lines), "10.0.0.8", T)
    assert (out["warm_sessions"], out["eligible"], out["split"], out["ghosts"]) == (2, 2, 1, 1)
    assert out["ghost_ratio"] == 0.5 and out["ghost_input_tokens"] == 9000
