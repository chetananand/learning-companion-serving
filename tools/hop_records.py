"""Make the hop records (H-77) from the Envoy access log of the Agent Router.

A hop is a request that the llm-d router split: the prefill pod (source) is not the decode pod
(destination). For each hop we write the source, the destination, the token count, the prefix
(cached tokens), and the backend: the KV connector of the run, from metrics/<run-id>/run.json (lmcache or
nixl). Non-split requests count in the summary only. Until 2026-10-03 the backend was always "nixl" (fault 38).

Usage: kubectl -n companion logs -l gateway.envoyproxy.io/owning-gateway-name=companion-gateway \
         --since-time=<RFC3339> --tail=-1 | python3 tools/hop_records.py --out metrics/<run-id>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

EMPTY = {"", "-", None}


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def host(value: str | None) -> str:
    """"10.42.1.7:8000" -> "10.42.1.7". The destination header can hold a list: keep the first entry."""
    if value in EMPTY:
        return ""
    first = str(value).split(",")[0].strip()
    return first.rsplit(":", 1)[0] if ":" in first else first


def backend_of(run: dict[str, Any]) -> str:
    """The KV connector that moved the bytes of a hop in this run: lmcache, nixl, or unknown (from run.json)."""
    variant = run.get("variant") or {}
    if "nixl" in " ".join(str(variant.get(k) or "") for k in ("name", "engine_options")).lower():
        return "nixl"
    args = " ".join(" ".join((c.get("command") or []) + (c.get("args") or []))
                    for pod in run.get("pods", []) if str(pod.get("name", "")).startswith("vllm-")
                    for c in pod.get("containers", []))
    if "LMCacheMPConnector" in args or "LMCacheConnector" in args:
        return "lmcache"
    if "NixlConnector" in args:
        return "nixl"
    return "unknown"


def parse(lines: Iterable[str], backend: str = "unknown") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hops: list[dict[str, Any]] = []
    total = split = local = rejected = 0
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if "decode_endpoint" not in rec:
            continue
        total += 1
        status = _int(rec.get("status")) or 0
        if status != 200:
            rejected += 1
            continue
        src, dst = host(rec.get("prefill_host")), host(rec.get("decode_endpoint") or rec.get("upstream_host"))
        if src and dst and src != dst:
            split += 1
            hops.append({"ts": rec.get("start_time"), "request_id": rec.get("request_id"), "src": src, "dst": dst,
                         "tokens": _int(rec.get("input_tokens")), "prefix_tokens": _int(rec.get("cached_input_tokens")),
                         "output_tokens": _int(rec.get("output_tokens")), "backend": backend,
                         "tenant": rec.get("tenant"), "step": rec.get("step"),
                         "duration_ms": _int(rec.get("duration_ms")), "first_byte_ms": _int(rec.get("first_byte_ms"))})
        else:
            local += 1
    moved = [h["tokens"] - (h["prefix_tokens"] or 0) for h in hops if h["tokens"] is not None]
    summary = {"requests": total, "ok": total - rejected, "split": split, "same_pod": local,
               "split_ratio": round(split / max(1, total - rejected), 4),
               "tokens_prefilled_remote": sum(moved)}
    return hops, summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", help="lmcache or nixl; the default comes from <out>/run.json")
    a = ap.parse_args()
    out = Path(a.out)
    record = out / "run.json"
    backend = a.backend or (backend_of(json.loads(record.read_text())) if record.exists() else "unknown")
    hops, summary = parse(sys.stdin, backend)
    out.mkdir(parents=True, exist_ok=True)
    (out / "hops.jsonl").write_text("".join(json.dumps(h) + "\n" for h in hops))
    (out / "hops-summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
