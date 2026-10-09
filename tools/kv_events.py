"""Count the KV events of the vLLM pods: the evict count of the handout (L848) and the input of E7.

Each vLLM pod publishes its KV events on ZMQ (`--kv-events-config`, port 5556). A PUB socket accepts
many subscribers, so this counter listens next to the llm-d router and changes nothing.
Wire format (vLLM v0.30.0, vllm/distributed/kv_events.py): three frames per message:
  topic (bytes), sequence number (8 bytes, big endian), payload (msgpack: [ts, [event, ...], dp_rank?])
Each event is a map with "type": BlockStored | BlockRemoved | AllBlocksCleared, and a "medium"
(GPU, CPU, STORAGE). A BlockRemoved on the GPU medium is an eviction from the GPU prefix cache.

Usage: python tools/kv_events.py --endpoints tcp://10.42.0.7:5556,tcp://10.42.0.8:5556 --seconds 600 \
         --out metrics/<run-id>/kv-events.json
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import msgpack


def decode(frames: list[bytes]) -> tuple[str, int, list[dict[str, Any]]]:
    """Return the topic, the sequence number, and the events of one message."""
    if len(frames) != 3:
        raise ValueError(f"expected 3 frames, got {len(frames)}")
    topic, seq_bytes, payload = frames
    batch = msgpack.unpackb(payload, raw=False, strict_map_key=False)
    events = batch[1] if isinstance(batch, list) and len(batch) >= 2 else []
    return topic.decode(errors="replace"), int.from_bytes(seq_bytes, "big"), events


class Tally:
    def __init__(self) -> None:
        self.stored: dict[str, Counter[str]] = defaultdict(Counter)   # endpoint -> medium -> blocks
        self.removed: dict[str, Counter[str]] = defaultdict(Counter)
        self.cleared: Counter[str] = Counter()
        self.batches: Counter[str] = Counter()
        self.gaps: Counter[str] = Counter()  # missed sequence numbers (the publisher dropped or we joined late)
        self.last_seq: dict[str, int] = {}
        self.per_second: dict[int, Counter[str]] = defaultdict(Counter)

    def add(self, endpoint: str, seq: int, events: list[dict[str, Any]], now: float) -> None:
        self.batches[endpoint] += 1
        last = self.last_seq.get(endpoint)
        if last is not None and seq > last + 1:
            self.gaps[endpoint] += seq - last - 1
        self.last_seq[endpoint] = seq
        second = int(now)
        for ev in events:
            kind = ev.get("type") if isinstance(ev, dict) else None
            medium = str(ev.get("medium") or "GPU") if isinstance(ev, dict) else "GPU"
            if kind == "BlockStored":
                n = len(ev.get("block_hashes") or [])
                self.stored[endpoint][medium] += n
                self.per_second[second][f"stored_{medium}"] += n
            elif kind == "BlockRemoved":
                n = len(ev.get("block_hashes") or [])
                self.removed[endpoint][medium] += n
                self.per_second[second][f"removed_{medium}"] += n
            elif kind == "AllBlocksCleared":
                self.cleared[endpoint] += 1
                self.per_second[second]["cleared"] += 1

    def summary(self) -> dict[str, Any]:
        endpoints = sorted(set(self.batches) | set(self.stored) | set(self.removed))
        return {
            "evicted_gpu_blocks": sum(c.get("GPU", 0) for c in self.removed.values()),
            "by_endpoint": {e: {"batches": self.batches[e], "stored": dict(self.stored[e]),
                                "removed": dict(self.removed[e]), "cleared": self.cleared[e],
                                "missed_batches": self.gaps[e]} for e in endpoints},
            "per_second": {str(k): dict(v) for k, v in sorted(self.per_second.items())},
        }


def listen(endpoints: list[str], seconds: float, topic_filter: str = "") -> Tally:
    import zmq

    ctx = zmq.Context.instance()
    poller = zmq.Poller()
    socks = {}
    for ep in endpoints:
        s = ctx.socket(zmq.SUB)
        s.setsockopt_string(zmq.SUBSCRIBE, topic_filter)
        s.connect(ep)
        poller.register(s, zmq.POLLIN)
        socks[s] = ep
    tally = Tally()
    end = time.monotonic() + seconds
    stop = {"now": False}

    def on_term(*_: Any) -> None:  # the pod is deleted at the end of the run: stop and write the summary
        stop["now"] = True

    signal.signal(signal.SIGTERM, on_term)
    try:
        while time.monotonic() < end and not stop["now"]:
            for sock, _ in poller.poll(timeout=500):
                frames = sock.recv_multipart()
                try:
                    _, seq, events = decode(frames)
                except (ValueError, msgpack.UnpackException):
                    continue
                tally.add(socks[sock], seq, events, time.time())
    finally:
        for s in socks:
            s.close(linger=0)
    return tally


def main() -> int:
    ap = argparse.ArgumentParser(description="Count vLLM KV events (the evict count)")
    ap.add_argument("--endpoints", required=True, help="comma-separated tcp://<pod-ip>:5556")
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--topic", default="kv@")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tally = listen([e.strip() for e in a.endpoints.split(",") if e.strip()], a.seconds, a.topic)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tally.summary(), indent=1))
    print(json.dumps({k: v for k, v in tally.summary().items() if k != "per_second"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
