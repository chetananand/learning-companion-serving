"""Thin adapters for the warm-controller: the Kubernetes API and Prometheus (httpx only)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


@dataclass(frozen=True)
class Pod:
    name: str
    ip: str
    ready: bool
    deleting: bool
    role: str  # prefill | decode | prefill-decode
    labels: dict[str, str] = field(default_factory=dict)


class KubeClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, token: str, namespace: str) -> None:
        self.http, self.base, self.token, self.ns = http, base_url.rstrip("/"), token, namespace

    @classmethod
    def in_cluster(cls, namespace: str) -> KubeClient:
        host, port = os.environ["KUBERNETES_SERVICE_HOST"], os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        http = httpx.AsyncClient(verify=str(SA_DIR / "ca.crt"), timeout=10.0)
        return cls(http, f"https://{host}:{port}", (SA_DIR / "token").read_text().strip(), namespace)

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if content_type:
            h["Content-Type"] = content_type
        return h

    async def list_pods(self, selector: str, role_label: str = "llm-d.ai/role") -> list[Pod]:
        resp = await self.http.get(f"{self.base}/api/v1/namespaces/{self.ns}/pods",
                                   params={"labelSelector": selector}, headers=self._headers())
        resp.raise_for_status()
        pods = []
        for item in resp.json().get("items", []):
            meta, status = item["metadata"], item.get("status", {})
            ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []))
            labels = meta.get("labels", {})
            pods.append(Pod(name=meta["name"], ip=status.get("podIP", ""), ready=ready,
                            deleting="deletionTimestamp" in meta, role=labels.get(role_label, ""), labels=labels))
        return pods

    async def patch_labels(self, pod: str, labels: dict[str, str | None]) -> None:
        resp = await self.http.patch(f"{self.base}/api/v1/namespaces/{self.ns}/pods/{pod}",
                                     json={"metadata": {"labels": labels}},
                                     headers=self._headers("application/merge-patch+json"))
        resp.raise_for_status()


class Prometheus:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self.http, self.base = http, base_url.rstrip("/")

    async def scalar(self, query: str) -> float | None:
        """One instant query. Return None for no data or an error (unknown is not good news)."""
        try:
            resp = await self.http.get(f"{self.base}/api/v1/query", params={"query": query}, timeout=5.0)
            resp.raise_for_status()
            result = resp.json()["data"]["result"]
        except (httpx.HTTPError, KeyError, ValueError):
            return None
        if not result:
            return None
        value = float(result[0]["value"][1])
        return None if value != value else value  # NaN is no data
