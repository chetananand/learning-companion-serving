"""Save raw /metrics scrapes of the live cluster (H-13, H-123): the text that DESIGN.md pastes.

It reads each target through the pod proxy of the Kubernetes API server (`kubectl get --raw`), so it needs no
port-forward and no curl in the pods. It keeps only our metric families, so each file stays short.
Usage (the tunnel is up): python3 tools/scrape_now.py <dest-dir>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# (name, namespace, label selector, port or None for the port named "metrics", path, metric families)
TARGETS = [
    ("vllm-prefill", "companion", "app=vllm-prefill", 8200, "/metrics", ("vllm:",)),
    ("vllm-decode", "companion", "app=vllm-decode", 8200, "/metrics", ("vllm:",)),
    ("barrier", "companion", "app=vllm-prefill", 8000, "/barrier/metrics", ("barrier_",)),
    ("edge", "companion", "app=edge", 8080, "/metrics", ("orch_",)),
    ("warm-controller", "companion", "app=warm-controller", 9100, "/metrics", ("orch_",)),
    ("lmcache", "companion", "app=lmcache-server", 8080, "/metrics", ("lmcache",)),
    ("router", "companion", "app.kubernetes.io/instance=companion-router", None, "/metrics",
     ("inference_", "llm_d", "router_")),
]


def keep(text: str, families: tuple[str, ...]) -> str:
    """The HELP, TYPE, and sample lines of the given metric families."""
    out = []
    for line in text.splitlines():
        name = line.split(" ", 3)[2] if line.startswith(("# HELP ", "# TYPE ")) and line.count(" ") >= 2 else line
        if name.startswith(families):
            out.append(line)
    return "\n".join(out) + "\n"


def kubectl(*args: str) -> str:
    return subprocess.run(["kubectl", *args], check=True, capture_output=True, text=True, timeout=30).stdout


def metrics_port(pod: dict) -> int | None:
    for c in pod["spec"]["containers"]:
        for p in c.get("ports", []):
            if p.get("name") == "metrics" or p.get("containerPort") == 9090:
                return p["containerPort"]
    return None


def main() -> int:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "metrics/scrape")
    dest.mkdir(parents=True, exist_ok=True)
    saved = 0
    for name, ns, selector, port, path, families in TARGETS:
        try:
            pods = json.loads(kubectl("-n", ns, "get", "pods", "-l", selector, "-o", "json"))["items"]
        except subprocess.SubprocessError as exc:
            print(f"{name}: cannot list pods: {exc}")
            continue
        for pod in pods:
            pname = pod["metadata"]["name"]
            p = port or metrics_port(pod)
            if p is None or pod["status"].get("phase") != "Running":
                continue
            out = dest / f"scrape-{name}-{pname}.txt"
            try:
                raw = kubectl("get", "--raw", f"/api/v1/namespaces/{ns}/pods/{pname}:{p}/proxy{path}")
                out.write_text(f"# {name} {ns}/{pname}:{p}{path}\n" + keep(raw, families))
                saved += 1
            except subprocess.SubprocessError as exc:
                out.write_text(f"# {name} {ns}/{pname}:{p}{path}: scrape failed: {exc}\n")
    print(f"saved {saved} scrapes in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
