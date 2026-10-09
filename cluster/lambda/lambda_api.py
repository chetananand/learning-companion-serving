"""Minimal Lambda Cloud API client (standard library only).

The key comes from the LAMBDA_API_KEY environment variable, or from ~/.env.
Nothing in this module prints the key.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import urllib.error
import urllib.request

BASE_URL = os.environ.get("LAMBDA_API_BASE", "https://cloud.lambda.ai/api/v1")
_KEY_LINE = re.compile(r"^\s*(?:export\s+)?LAMBDA_API_KEY\s*=\s*(.*)$")


class LambdaApiError(RuntimeError):
    pass


def api_key() -> str:
    key = os.environ.get("LAMBDA_API_KEY")
    if key:
        return key
    env_file = pathlib.Path(os.environ.get("LAMBDA_ENV_FILE", pathlib.Path.home() / ".env"))
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            match = _KEY_LINE.match(line)
            if match:
                return match.group(1).strip().strip("'\"")
    raise LambdaApiError("LAMBDA_API_KEY is not set and is not in ~/.env")


def _request(method: str, path: str, body: dict | None = None, timeout: float = 20.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "learning-companion/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise LambdaApiError(f"{method} {path}: HTTP {exc.code}: {detail}") from None


def get(path: str) -> dict:
    return _request("GET", path)


def capacity() -> dict[str, list[str]]:
    """Return {instance_type_name: [regions with capacity now]}."""
    data = get("/instance-types")["data"]
    return {
        name: [r["name"] for r in item.get("regions_with_capacity_available", [])]
        for name, item in data.items()
    }


def prices_usd_per_hour() -> dict[str, float]:
    data = get("/instance-types")["data"]
    return {name: item["instance_type"]["price_cents_per_hour"] / 100 for name, item in data.items()}
