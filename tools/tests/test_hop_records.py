"""Tests of the hop records made from the Envoy access log."""

import json

from tools.hop_records import backend_of, host, parse


def line(**kw):
    base = {"start_time": "2026-09-30T10:00:00Z", "request_id": "r", "status": "200", "input_tokens": "9000",
            "cached_input_tokens": "2048", "output_tokens": "300", "decode_endpoint": "10.42.1.8:8000",
            "prefill_host": "10.42.1.7:8000", "upstream_host": "10.42.1.8:8000"}
    return json.dumps({**base, **kw})


def test_split_requests_become_hops():
    lines = [line(), line(prefill_host="-"), line(prefill_host="10.42.1.8:8000"), line(status="429"),
             "not json", '{"other": 1}']
    hops, summary = parse(lines, "lmcache")
    assert len(hops) == 1
    h = hops[0]
    assert (h["src"], h["dst"], h["tokens"], h["prefix_tokens"], h["backend"]) == ("10.42.1.7", "10.42.1.8", 9000,
                                                                                    2048, "lmcache")
    assert summary == {"requests": 4, "ok": 3, "split": 1, "same_pod": 2, "split_ratio": 0.3333,
                       "tokens_prefilled_remote": 6952}


def test_host():
    assert host("10.0.0.1:8000,10.0.0.2:8000") == "10.0.0.1" and host("-") == "" and host(None) == ""


def test_backend_comes_from_the_run_record():
    def pod(name, args):
        return {"name": name, "containers": [{"name": "modelserver", "command": ["vllm", "serve"], "args": args}]}
    lmcache = {"variant": {"name": "baseline"}, "pods": [
        pod("vllm-decode-0", ["--kv-transfer-config", '{"kv_connector":"LMCacheMPConnector"}'])]}
    nixl = {"variant": {"name": "nixl-hop", "engine_options": "--preset nixl-hop"}, "pods": []}
    assert backend_of(lmcache) == "lmcache" and backend_of(nixl) == "nixl" and backend_of({}) == "unknown"
