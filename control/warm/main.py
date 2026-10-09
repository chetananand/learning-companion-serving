"""Entry point: python -m control.warm.main (runs in the cluster with a service account)."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from prometheus_client import start_http_server

from control.warm.adapters import KubeClient, Prometheus
from control.warm.controller import ControllerSettings, WarmController, WarmMetrics
from control.warm.probes import PodProbe


def settings_from_env() -> ControllerSettings:
    env = os.environ.get
    prompts = tuple(p for p in env("WARM_SYSTEM_PROMPTS", "").split("\n---\n") if p.strip())
    return ControllerSettings(
        namespace=env("WARM_NAMESPACE", "companion"),
        selector=env("WARM_SELECTOR", "app.kubernetes.io/part-of=companion-vllm"),
        port=int(env("WARM_POD_PORT", "8000")),
        served_model=env("WARM_SERVED_MODEL", "companion"),
        baseline_ttft_4k_s=float(env("WARM_BASELINE_TTFT_4K_S", "0.35")),
        system_prompts=prompts,
        mode=env("WARM_MODE", "warmup"),
        ramp_mode=env("WARM_RAMP_MODE", "ramp"),
    )


async def amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = settings_from_env()
    metrics = WarmMetrics()
    start_http_server(int(os.environ.get("WARM_METRICS_PORT", "9100")), registry=metrics.registry)
    http = httpx.AsyncClient()
    controller = WarmController(KubeClient.in_cluster(s.namespace),
                                Prometheus(http, os.environ.get("WARM_PROMETHEUS_URL",
                                                                "http://prometheus-operated.monitoring:9090")),
                                PodProbe(http, s.served_model), s, metrics)
    await controller.run()


if __name__ == "__main__":
    asyncio.run(amain())
