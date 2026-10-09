"""Tests of the E4 and E5 probes against a fake P/D pair (httpx mock transport)."""

from __future__ import annotations

import argparse
import json

import httpx

from tools import pd_probe as pp


class FakePD:
    """The decode sidecar (:8000), vLLM on the decode pod (:8200), and its /metrics with NIXL counters."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, str], dict]] = []
        self.nixl_bytes = 0.0
        self.nixl_count = 0
        self.held: set[str] = set()  # the system prompts in the prefix cache of the decode pod

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/metrics":
            return httpx.Response(200, text=(
                f'vllm:nixl_bytes_transferred_sum{{engine="0"}} {self.nixl_bytes}\n'
                f'vllm:nixl_bytes_transferred_count{{engine="0"}} {self.nixl_count}\n'
                f'vllm:nixl_bytes_transferred_bucket{{engine="0",le="+Inf"}} {self.nixl_count}\n'))
        payload = json.loads(request.content)
        self.calls.append((request.url.port, dict(request.headers), payload))
        first = payload["messages"][0]
        system = first["content"] if first["role"] == "system" else ""
        prompt_tokens = sum(len(m["content"]) for m in payload["messages"]) // 4
        cached = len(system) // 4 if system in self.held else 0
        if request.url.port == 8200:
            self.held.add(system)
        if "x-prefiller-host-port" in request.headers:
            self.nixl_bytes += (prompt_tokens - cached) * 100
            self.nixl_count += 1
        chunks = [{"choices": [{"delta": {"content": w}}]} for w in ("one ", "two ", "three")]
        chunks.append({"choices": [], "usage": {"prompt_tokens": prompt_tokens,
                                                "prompt_tokens_details": {"cached_tokens": cached}}})
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})


async def test_hop_splits_and_counts_the_bytes_of_each_group():
    fake = FakePD()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    a = argparse.Namespace(decode="10.0.0.2", prefill="10.0.0.1", capture=None, prefix_tokens=400,
                           suffix_tokens=100, requests=3)
    r = await pp.hop(client, a)
    port, headers, _ = fake.calls[0]
    assert port == 8200 and "x-prefiller-host-port" not in headers  # the seed goes straight to vLLM
    assert all(p == 8000 and h["x-prefiller-host-port"] == "10.0.0.1:8000" for p, h, _ in fake.calls[1:])
    shared, unshared = r["groups"]["shared"], r["groups"]["unshared"]
    assert shared["transfers"] == 3 and unshared["transfers"] == 3
    assert 0 < shared["bytes"] < unshared["bytes"] / 3  # the decode pod does not pull the prefix it holds
    assert all(x["cached_tokens"] > 0 for x in shared["requests"])
    assert all(x["cached_tokens"] == 0 for x in unshared["requests"])


async def test_split_probes_both_modes_and_measures_the_stream_gaps():
    fake = FakePD()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    a = argparse.Namespace(decode="10.0.0.2", prefill="10.0.0.1", capture=None, sizes=[100, 200], streams=2,
                           stream_tokens=50, rounds=1, warm_s=0.05, gap_s=0.0)
    r = await pp.split(client, a)
    assert set(r["summary"]) == {"100-split", "100-local", "200-split", "200-local"}
    probes = r["probes"]
    assert [p["mode"] for p in probes] == ["split", "local", "split", "local"]
    assert all(p["status"] == 200 and p["ttft_s"] is not None for p in probes)
    probe_calls = [(h, b) for _, h, b in fake.calls if not b["ignore_eos"]]
    assert ["x-prefiller-host-port" in h for h, _ in probe_calls] == [True, False, True, False]
    streams = [h for _, h, b in fake.calls if b["ignore_eos"]]
    assert streams and not any("x-prefiller-host-port" in h for h in streams)  # the streams stay on decode
    assert r["baseline_itl"]["n"] > 0


def test_nixl_totals_skip_buckets_and_count_transfers():
    d = {'vllm:nixl_bytes_transferred_sum{e="0"}': 500.0, 'vllm:nixl_bytes_transferred_count{e="0"}': 2.0,
         'vllm:nixl_bytes_transferred_bucket{le="+Inf"}': 2.0, 'vllm:prefix_cache_hits_total{e="0"}': 7.0}
    assert pp.nixl_totals(d) == {"bytes": 500.0, "transfers": 2.0}


def test_window_gaps():
    gaps = [(1.0, 0.03), (2.0, 0.2), (3.0, 0.04)]
    assert pp.window_gaps(gaps, 1.5, 2.5) == [0.2]


def test_lmcache_totals_skip_the_created_series():
    d = {'lmcache_mp_transfer_phase_bytes_total{phase="store"}': 1000.0,
         'lmcache_mp_transfer_phase_bytes_total{phase="retrieve"}': 900.0,
         'lmcache_mp_transfer_phase_bytes_created{phase="store"}': 1.79e9,
         'lmcache_mp_lookup_hit_tokens_total': 5376.0}
    assert pp.lmcache_totals(d) == {"bytes": 1900.0, "hit_tokens": 5376.0}
