"""Guard: `inspect(payload) -> Guard` (system design section 6.1, ADR-011).

Stage 1 runs here on the CPU. A request that fails stage 1 reaches no GPU (L536).
Stage 2 calls the `guard` service (NeMo Guardrails `/v1/checks`). Its models run on
node 2 GPU 1, never on a serving GPU. The guard fails closed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from control.edge.config import EdgeSettings

CLASSES = ("interactive", "batch")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DATA_URL = re.compile(r"^data:(image/(?:png|jpeg));base64,(.*)$", re.S)


@dataclass(frozen=True)
class Guard:
    ok: bool
    status: int = 200
    reason: str = "ok"
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    tenant: str = ""
    request_class: str = ""
    prompt_tokens: int = 0
    images: int = 0


def _reject(status: int, reason: str, message: str) -> Guard:
    return Guard(False, status, reason, message)


def message_texts(payload: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    for msg in payload.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(str(p.get("text") or "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return texts


def image_parts(payload: Mapping[str, Any]) -> list[str]:
    urls: list[str] = []
    for msg in payload.get("messages") or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                image = part.get("image_url")
                urls.append(str(image.get("url") if isinstance(image, dict) else image or ""))
    return urls


def last_user_content(payload: Mapping[str, Any]) -> tuple[str, list[str]]:
    """The new user content of a turn: the text and images of the last user message."""
    for msg in reversed(payload.get("messages") or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            single = {"messages": [msg]}
            return "\n".join(message_texts(single)), image_parts(single)
    return "", []


def _valid_tenant(tenant: str, s: EdgeSettings) -> bool:
    return tenant in s.tenants or any(tenant.startswith(p) and len(tenant) > len(p) for p in s.tenant_prefixes)


def _check_tools(tools: Any, count_text: Callable[[str], int], s: EdgeSettings) -> str | None:
    if tools is None:
        return None
    if not isinstance(tools, list):
        return "tools must be a list"
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(fn, dict):
            return "each tool must be {type: function, function: {...}}"
        if not _TOOL_NAME.match(str(fn.get("name", ""))):
            return f"bad tool name {fn.get('name')!r}"
        params = fn.get("parameters", {"type": "object"})
        if not isinstance(params, dict) or params.get("type", "object") != "object":
            return f"tool {fn['name']} parameters must be a JSON Schema object"
    if count_text(json.dumps(tools)) > s.max_tool_schema_tokens:
        return "tool schemas are too large"
    return None


def inspect(payload: Mapping[str, Any], *, headers: Mapping[str, str], count_text: Callable[[str], int],
            settings: EdgeSettings) -> Guard:
    """Stage 1 rules, in the order of system design section 6.1."""
    s = settings
    body = dict(payload)
    h = {k.lower(): v for k, v in headers.items()}

    if str(body.get("model", "")) not in {s.served_model, *s.model_aliases}:
        return _reject(400, "model_not_allowed", f"model {body.get('model')!r} is not in the allow list")

    tenant, request_class = h.get("x-tenant-id", ""), h.get("x-request-class", "")
    if not _valid_tenant(tenant, s) or request_class not in CLASSES:
        return _reject(400, "bad_headers", "X-Tenant-Id and X-Request-Class must be present and valid")

    cap = s.max_tokens_interactive if request_class == "interactive" else s.max_tokens_batch
    raw = body.get("max_tokens", body.get("max_completion_tokens"))
    body.pop("max_completion_tokens", None)  # vLLM prefers this field, so keep only the clamped max_tokens
    if raw is None:
        body["max_tokens"] = cap
    else:
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            return _reject(400, "bad_max_tokens", "max_tokens must be a positive integer")
        body["max_tokens"] = min(raw, cap)

    texts, images = message_texts(body), image_parts(body)
    if not any(t.strip() for t in texts) and not images:
        return _reject(400, "empty_prompt", "the prompt is empty")

    prompt_tokens = sum(count_text(t) for t in texts) + 4 * len(body.get("messages") or [])
    prompt_tokens += s.image_tokens * len(images)
    if prompt_tokens > s.max_prompt_tokens:
        return _reject(413, "prompt_too_long", f"{prompt_tokens} prompt tokens is above {s.max_prompt_tokens}")

    if len(images) > s.max_images:
        return _reject(413, "image_too_large", f"{len(images)} images is above {s.max_images}")
    for url in images:
        m = _DATA_URL.match(url)
        if not m:
            return _reject(400, "bad_image", "images must be PNG or JPEG data URLs")
        if len(m.group(2)) * 3 // 4 > s.max_image_bytes:
            return _reject(413, "image_too_large", "an image is above the size limit")

    tool_error = _check_tools(body.get("tools"), count_text, s)
    if tool_error:
        return _reject(422, "bad_tools", tool_error)

    return Guard(True, payload=body, tenant=tenant, request_class=request_class,
                 prompt_tokens=prompt_tokens, images=len(images))


@dataclass(frozen=True)
class Stage2Result:
    ok: bool
    status: int = 200
    reason: str = "ok"
    rail: str = ""
    cached: bool = False


def content_key(text: str, images: list[str]) -> str:
    digest = hashlib.sha256(text.encode())
    for url in images:
        digest.update(b"\x00" + hashlib.sha256(url.encode()).digest())
    return "guard:v1:" + digest.hexdigest()


def _reason_for_rail(rail: str) -> str:
    rail = rail.lower()
    if "injection" in rail or "jailbreak" in rail or "prompt_guard" in rail:
        return "prompt_injection"
    return "unsafe_content"


class GuardClient:
    """Stage 2: `guard-injection` and NeMo `/v1/checks`, in parallel, with a Redis verdict cache. Fails closed."""

    def __init__(self, http: httpx.AsyncClient, redis: Any, settings: EdgeSettings) -> None:
        self.http, self.redis, self.s = http, redis, settings

    async def _injection(self, text: str) -> Stage2Result:
        if not text.strip():
            return Stage2Result(True)
        try:
            resp = await self.http.post(f"{self.s.injection_url}/v1/classify", json={"texts": [text]},
                                        timeout=self.s.guard_timeout_s)
            resp.raise_for_status()
            result = resp.json()["results"][0]
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            return Stage2Result(False, 503, "guard_unavailable")
        if result.get("malicious"):
            return Stage2Result(False, 400, "prompt_injection", "prompt_guard")
        return Stage2Result(True)

    async def _content_safety(self, text: str, images: list[str]) -> Stage2Result:
        content: Any = text
        if images:
            content = [{"type": "text", "text": text}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in images]
        # NeMo Guardrails v0.24.1 GuardrailCheckRequest: an OpenAI chat request plus a `guardrails` field.
        request = {"model": self.s.served_model, "messages": [{"role": "user", "content": content}],
                   "guardrails": {"config_ids": [self.s.guard_config_id], "rail_types": ["input"]}}
        try:
            resp = await self.http.post(f"{self.s.guard_url}/v1/checks", json=request,
                                        timeout=self.s.guard_timeout_s)
            resp.raise_for_status()
            return parse_checks_response(resp.json())
        except (httpx.HTTPError, ValueError, KeyError):
            return Stage2Result(False, 503, "guard_unavailable")

    async def check(self, text: str, images: list[str]) -> Stage2Result:
        if not self.s.guard_enabled or (not text.strip() and not images):
            return Stage2Result(True)
        key = content_key(text, images)
        try:
            cached = await self.redis.get(key)
        except Exception:  # noqa: BLE001 - a cache failure must not open the guard
            cached = None
        if cached:
            data = json.loads(cached)
            return Stage2Result(data["ok"], data["status"], data["reason"], data.get("rail", ""), cached=True)

        injection, safety = await asyncio.gather(self._injection(text), self._content_safety(text, images))
        if injection.status == 400:
            result = injection  # an injection verdict wins
        elif not injection.ok or not safety.ok:
            result = injection if not injection.ok else safety  # fail closed: 503, or 400 unsafe_content
        else:
            result = Stage2Result(True)
        if result.status != 503:  # never cache an outage
            with contextlib.suppress(Exception):  # a cache failure must not fail the request
                await self.redis.set(key, json.dumps(result.__dict__ | {"cached": False}),
                                     ex=self.s.guard_cache_ttl_s)
        return result


def parse_checks_response(data: Mapping[str, Any]) -> Stage2Result:
    """Map a `/v1/checks` response (status passed | modified | blocked, and the rail name) to a result."""
    status = str(data.get("status", "")).upper()
    rail = str(data.get("rail") or data.get("blocked_by") or "")
    if status in ("PASSED", "MODIFIED"):
        return Stage2Result(True)
    if status == "BLOCKED":
        return Stage2Result(False, 400, _reason_for_rail(rail), rail)
    raise ValueError(f"unknown /v1/checks status {status!r}")


def decode_image_bytes(url: str) -> bytes:
    m = _DATA_URL.match(url)
    return base64.b64decode(m.group(2)) if m else b""
