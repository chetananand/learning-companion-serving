"""A small client for the native SIE API (ADR-007): embed, rerank, and OCR.

Wire format (from the `sie-sdk` 0.8.3 source): MessagePack bodies with
`Content-Type` and `Accept` set to `application/msgpack`. Arrays come back as
MessagePack NumPy values: {b"nd": True, b"type": "<f4", b"shape": [...], b"data": bytes}.
We do not use `sie-sdk`, because it needs `websockets` below 15 (ADR-007).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx
import msgpack
import numpy as np

MSGPACK = "application/msgpack"
_NUMERIC = {"<f2", "<f4", "<f8", "<i2", "<i4", "<i8", "<u1", "<u2", "<u4", "<u8", "|u1", "|i1", "|b1"}


class SieError(RuntimeError):
    pass


def _key(d: dict[Any, Any], name: str) -> Any:
    return d.get(name.encode(), d.get(name))


def _decode_numpy(obj: dict[Any, Any]) -> Any:
    """Change a MessagePack NumPy value into a list. Accept only flat numeric types."""
    marker = _key(obj, "nd")
    if marker is None:
        return obj
    kind = _key(obj, "kind") or b""
    dtype = _key(obj, "type")
    dtype = dtype.decode() if isinstance(dtype, bytes) else dtype
    if kind not in (b"", "") or dtype not in _NUMERIC:
        raise SieError(f"unsupported array type {dtype!r}")
    data = _key(obj, "data")
    if not isinstance(data, bytes):
        raise SieError("array data is not bytes")
    if marker is False:
        return np.frombuffer(data, dtype=dtype, count=1)[0].item()
    shape = tuple(_key(obj, "shape") or ())
    return np.frombuffer(data, dtype=dtype).reshape(shape).tolist()


def unpack(body: bytes) -> Any:
    return msgpack.unpackb(body, raw=False, object_hook=_decode_numpy, strict_map_key=False)


def pack(obj: Any) -> bytes:
    return msgpack.packb(obj, use_bin_type=True)


class SieClient:
    def __init__(self, base_url: str, *, timeout_s: float = 30.0, retries: int = 3,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport,
                                      headers={"Content-Type": MSGPACK, "Accept": MSGPACK})
        self.retries = retries

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        data = pack(body)
        for attempt in range(self.retries + 1):
            try:
                resp = await self.http.post(path, content=data)
            except httpx.HTTPError as exc:  # G0: a restarting SIE pod must not stop the whole ingest
                raise SieError(f"{path}: {type(exc).__name__}") from exc
            if resp.status_code in (429, 503) and attempt < self.retries:
                # The server loads a model or has no capacity. Wait as it asks, then try again.
                wait = float(resp.headers.get("retry-after", "1") or 1)
                await asyncio.sleep(min(wait, 10.0))
                continue
            if resp.status_code != 200:
                raise SieError(f"{path}: HTTP {resp.status_code}: {resp.text[:200]}")
            if MSGPACK in resp.headers.get("content-type", ""):
                return unpack(resp.content)
            return resp.json()
        raise SieError(f"{path}: no capacity after {self.retries} tries")

    async def embed(self, model: str, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        body = {"items": [{"text": t} for t in texts],
                "params": {"output_types": ["dense"], "options": {"is_query": is_query}}}
        out = await self._post(f"/v1/encode/{model}", body)
        vectors = []
        for item in out["items"]:
            dense = item.get("dense")
            values = dense.get("values") if isinstance(dense, dict) else dense
            if values is None:
                raise SieError("encode result has no dense vector")
            vectors.append([float(v) for v in values])
        return vectors

    async def rerank(self, model: str, query: str, docs: Sequence[str]) -> list[float]:
        """Return one score for each doc, in the order of `docs`."""
        if not docs:
            return []
        body = {"query": {"text": query}, "items": [{"id": str(i), "text": d} for i, d in enumerate(docs)]}
        out = await self._post(f"/v1/score/{model}", body)
        scores = [float("-inf")] * len(docs)
        for entry in out["scores"]:
            item_id = str(entry["item_id"])
            index = int(item_id.removeprefix("item-"))
            scores[index] = float(entry["score"])
        return scores

    async def ocr(self, model: str, image: bytes, image_format: str = "png") -> str:
        body = {"items": [{"images": [{"data": image, "format": image_format}]}]}
        out = await self._post(f"/v1/extract/{model}", body)
        item = out["items"][0]
        if item.get("error"):
            raise SieError(f"OCR failed: {item['error']}")
        return "\n\n".join(e["text"] for e in item.get("entities", []) if e.get("text")).strip()
