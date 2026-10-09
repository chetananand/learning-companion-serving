"""Grafana images of each run, for the talk and the report (handout Part 8: walk through the dashboards).

The handout asks for a short walk through eight dashboards. Our dashboards (tools/dashboards.py) cover
them. This tool keeps images of them, because the cluster does not run at talk time:

  render  Grafana renders each dashboard as one PNG (and each panel with --panels) through its image
          renderer (kube-prometheus-stack: grafana.imageRenderer). The time window of the images is the
          window of the run (metrics/<run-id>/window.json from cluster/loadgen.sh), so each image shows
          one run. Output: metrics/<run-id>/grafana/<dashboard>.png, panels/, and index.json.
  check   For each panel, ask Prometheus for its queries in the run window. List the panels with no data.
          Run it at G0 (node 2, the dev model), before any H100 session.

Grafana and Prometheus run on node 2. Thus a session renders its images after node 1 stops.
The Grafana password comes from the Kubernetes secret of the chart (never printed), or GRAFANA_PASSWORD.

Usage (tunnel up: Grafana on 127.0.0.1:3000, Prometheus on 127.0.0.1:9090; KUBECONFIG of node 2):
  python3 tools/grafana_shots.py check --runs metrics/g0-load
  python3 tools/grafana_shots.py render --runs metrics/e2-soak metrics/e3-c-100 [--panels] [--force]
  python3 tools/grafana_shots.py render --session     (every run folder with window.json and no images yet)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = ROOT / "cluster" / "manifests" / "base" / "monitoring" / "dashboards"
METRICS = ROOT / "metrics"
TZ = "America/Los_Angeles"
# The handout walk (Part 8, L732): each item and our dashboard for it, in the order of the talk.
HANDOUT_WALK = [
    ("companion-cluster", "Cluster"),
    ("companion-success", "Success and failures"),
    ("companion-gateway", "Gateway + admission: sheds by reason"),
    ("companion-router", "Router"),
    ("companion-queues", "Queue depth by pod"),
    ("companion-vllm", "vLLM"),
    ("companion-hop", "KV hop store (NIXL and LMCache, our Mooncake)"),
    ("companion-scaling", "Pods / replicas / KEDA: which pool?"),
    ("companion-app", "The Companion app (not in the handout list)"),
]
CELL_PX = 38  # Grafana grid: 30 px for each row unit and 8 px of margin
PNG = b"\x89PNG"

HttpGet = Callable[[str, dict[str, str], float], tuple[int, str, bytes]]


def http_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - our tunnel only
            return resp.status, resp.headers.get("content-type", ""), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("content-type", ""), exc.read()


def load_dashboards(folder: Path = DASHBOARDS) -> list[dict[str, Any]]:
    """The dashboards in the order of the handout walk (a dashboard outside the walk comes last)."""
    order = {uid: i for i, (uid, _) in enumerate(HANDOUT_WALK)}
    dashboards = [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]
    return sorted(dashboards, key=lambda d: order.get(d["uid"], len(order)))


def walk_name(uid: str) -> str:
    return dict(HANDOUT_WALK).get(uid, uid)


def image_name(uid: str) -> str:
    """01-companion-cluster.png: the file names sort in the order of the walk."""
    order = [u for u, _ in HANDOUT_WALK]
    n = order.index(uid) + 1 if uid in order else 99
    return f"{n:02d}-{uid}.png"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def height_px(dashboard: dict[str, Any]) -> int:
    rows = max((p["gridPos"]["y"] + p["gridPos"]["h"] for p in dashboard["panels"]), default=8)
    return rows * CELL_PX + 60


def run_window(run_dir: Path) -> tuple[float, float]:
    """The run window in unix seconds: window.json (loadgen.sh), else the client calls of the run."""
    w = run_dir / "window.json"
    if w.exists():
        data = json.loads(w.read_text())
        return float(data["start"]), float(data["end"])
    client = run_dir / "client.jsonl"
    if client.exists():
        rows = [json.loads(x) for x in client.read_text().splitlines() if x.strip()]
        starts = [r["t_start"] for r in rows if "t_start" in r]
        ends = [r["t_start"] + (r.get("latency_s") or 0) for r in rows if "t_start" in r]
        if starts:
            return min(starts) - 60, max(ends) + 60
    raise ValueError(f"{run_dir}: no window.json and no client.jsonl")


def render_url(base: str, dashboard: dict[str, Any], start: float, end: float, *, panel_id: int | None = None,
               width: int = 1600, height: int | None = None) -> str:
    uid, name = dashboard["uid"], slug(dashboard["title"])
    params = {"orgId": "1", "from": str(int(start * 1000)), "to": str(int(end * 1000)), "tz": TZ}
    if panel_id is None:
        params |= {"width": str(width), "height": str(height or height_px(dashboard)), "kiosk": "true"}
        return f"{base}/render/d/{uid}/{name}?{urllib.parse.urlencode(params)}"
    params |= {"panelId": str(panel_id), "width": "1000", "height": "500"}
    return f"{base}/render/d-solo/{uid}/{name}?{urllib.parse.urlencode(params)}"


def grafana_auth() -> dict[str, str]:
    """Basic auth for the admin user. The password stays in memory only."""
    password = os.environ.get("GRAFANA_PASSWORD")
    if not password:
        out = subprocess.run(["kubectl", "-n", "monitoring", "get", "secret", "kube-prometheus-stack-grafana",
                              "-o", "jsonpath={.data.admin-password}"], capture_output=True, text=True, check=True)
        password = base64.b64decode(out.stdout).decode()
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def render(run_dir: Path, dashboards: list[dict[str, Any]], *, base: str, headers: dict[str, str],
           panels: bool = False, get: HttpGet = http_get, timeout: float = 180.0) -> dict[str, Any]:
    start, end = run_window(run_dir)
    out = run_dir / "grafana"
    (out / "panels").mkdir(parents=True, exist_ok=True)
    index: dict[str, Any] = {"from": start, "to": end, "tz": TZ, "images": [], "errors": []}

    def shot(url: str, path: Path, what: str) -> None:
        t0 = time.monotonic()
        status, ctype, body = get(url, headers, timeout)
        if status == 200 and body.startswith(PNG):
            path.write_bytes(body)
            index["images"].append({"what": what, "file": str(path.relative_to(run_dir)), "url": url,
                                    "seconds": round(time.monotonic() - t0, 1), "bytes": len(body)})
        else:
            index["errors"].append({"what": what, "status": status, "type": ctype, "body": body[:200].decode(
                errors="replace"), "url": url})

    for d in dashboards:
        shot(render_url(base, d, start, end), out / image_name(d["uid"]), walk_name(d["uid"]))
        if panels:
            for p in d["panels"]:
                name = f"{d['uid']}--{p['id']:02d}-{slug(p['title'])}.png"
                shot(render_url(base, d, start, end, panel_id=p["id"]), out / "panels" / name,
                     f"{d['title']} / {p['title']}")
    (out / "index.json").write_text(json.dumps(index, indent=1))
    (out / "walk.md").write_text(walk_markdown(run_dir.name, index))
    return index


def walk_markdown(run_id: str, index: dict[str, Any]) -> str:
    """The handout walk for one run: each dashboard image with its handout name, in the order of the talk."""
    t0 = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(index["from"]))
    t1 = time.strftime("%H:%M:%S UTC", time.gmtime(index["to"]))
    lines = [f"# Grafana walk: {run_id}", "", f"Window: {t0} to {t1}.", ""]
    for img in index["images"]:
        if "/" not in img["file"].removeprefix("grafana/"):
            lines += [f"## {img['what']}", "", f"![{img['what']}]({Path(img['file']).name})", ""]
    for err in index["errors"]:
        lines += [f"- MISSING: {err['what']} (HTTP {err['status']})"]
    return "\n".join(lines) + "\n"


def check(run_dir: Path, dashboards: list[dict[str, Any]], *, prometheus: str, get: HttpGet = http_get,
          timeout: float = 60.0) -> dict[str, Any]:
    """Ask Prometheus for each panel query in the run window. A panel with no series has no data."""
    start, end = run_window(run_dir)
    step = max(5, int((end - start) / 300))
    rows = []
    for d in dashboards:
        for p in d["panels"]:
            series, errors = 0, []
            for t in p.get("targets", []):
                q = urllib.parse.urlencode({"query": t["expr"], "start": start, "end": end, "step": step})
                status, _, body = get(f"{prometheus}/api/v1/query_range?{q}", {}, timeout)
                data = json.loads(body or b"{}") if status == 200 else {}
                if status != 200 or data.get("status") != "success":
                    errors.append(f"HTTP {status}: {body[:120].decode(errors='replace')}")
                    continue
                series += len(data["data"]["result"])
            rows.append({"dashboard": d["title"], "panel": p["title"], "series": series, "errors": errors,
                         "ok": series > 0 and not errors})
    result = {"from": start, "to": end, "panels": len(rows), "no_data": [r for r in rows if not r["ok"]],
              "rows": rows}
    out = run_dir / "grafana"
    out.mkdir(parents=True, exist_ok=True)
    (out / "check.json").write_text(json.dumps(result, indent=1))
    return result


def session_runs(base: Path = METRICS) -> list[Path]:
    """The run folders of this session: they have window.json and no images yet."""
    return sorted(p.parent for p in base.glob("*/window.json") if not (p.parent / "grafana" / "index.json").exists())


def main() -> int:
    # Session 1: with no KUBECONFIG, kubectl read the laptop default, and the Grafana password step failed.
    os.environ.setdefault("KUBECONFIG", str(ROOT / "cluster" / "state" / "kubeconfig"))
    ap = argparse.ArgumentParser(description="Grafana images and the panel data check for each run")
    ap.add_argument("cmd", choices=["render", "check"])
    ap.add_argument("--runs", nargs="*", default=[])
    ap.add_argument("--session", action="store_true", help="every run with window.json and no images yet")
    ap.add_argument("--panels", action="store_true", help="also one image for each panel")
    ap.add_argument("--force", action="store_true", help="render again when images exist")
    ap.add_argument("--dashboards", nargs="*", default=[], help="dashboard uids (default: all)")
    ap.add_argument("--grafana", default="http://127.0.0.1:3000")
    ap.add_argument("--prometheus", default="http://127.0.0.1:9090")
    a = ap.parse_args()
    runs = [Path(r) for r in a.runs] + (session_runs() if a.session else [])
    if not runs:
        sys.exit("no runs: give --runs or --session")
    dashboards = [d for d in load_dashboards() if not a.dashboards or d["uid"] in a.dashboards]
    bad = 0
    if a.cmd == "check":
        for run in runs:
            r = check(run, dashboards, prometheus=a.prometheus)
            print(f"{run.name}: {r['panels'] - len(r['no_data'])}/{r['panels']} panels have data")
            for row in r["no_data"]:
                print(f"  NO DATA  {row['dashboard']} / {row['panel']} {'; '.join(row['errors'])[:160]}")
            bad += len(r["no_data"])
        return 1 if bad else 0
    headers = grafana_auth()
    for run in runs:
        if (run / "grafana" / "index.json").exists() and not a.force:
            print(f"{run.name}: images exist (use --force)")
            continue
        idx = render(run, dashboards, base=a.grafana, headers=headers, panels=a.panels)
        print(f"{run.name}: {len(idx['images'])} images, {len(idx['errors'])} errors")
        for e in idx["errors"][:5]:
            print(f"  ERROR {e['what']}: HTTP {e['status']} {e['body'][:120]}")
        bad += len(idx["errors"])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
