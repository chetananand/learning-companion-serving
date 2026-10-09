"""guard-injection: the prompt-injection classifier service (ADR-011, stage 2).

Model: meta-llama/Llama-Prompt-Guard-2-86M, or the fallback protectai/deberta-v3-base-prompt-injection-v2
until Meta approves access (action A8). It runs on node 2 GPU 1, never on a serving GPU.

POST /v1/classify {"texts": [...]} -> {"results": [{"malicious": bool, "score": float, "windows": [...]}]}
Each text is split into windows of at most 512 tokens. The text score is the highest window score.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

MALICIOUS_LABELS = {"label_1", "malicious", "injection", "jailbreak"}


class Classifier(Protocol):
    def windows(self, text: str, max_tokens: int, stride: int) -> list[tuple[int, int, str]]: ...

    def malicious_scores(self, texts: Sequence[str]) -> list[float]: ...


def split_windows(offsets: Sequence[tuple[int, int]], text: str, max_tokens: int,
                  stride: int) -> list[tuple[int, int, str]]:
    """Split a text into windows of at most `max_tokens` tokens, with `stride` tokens of overlap."""
    if not offsets:
        return [(0, len(text), text)] if text.strip() else []
    out, start, n = [], 0, len(offsets)
    while start < n:
        end = min(start + max_tokens, n)
        a, b = offsets[start][0], offsets[end - 1][1]
        out.append((a, b, text[a:b]))
        if end == n:
            break
        start = end - stride
    return out


class HFClassifier:
    """A Hugging Face sequence classifier. torch and transformers are imported here only."""

    def __init__(self, model_id: str, device: str | None = None, batch_size: int = 16) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_id).to(self.device).eval()
        labels = {int(i): str(name).lower() for i, name in self.model.config.id2label.items()}
        self.malicious_ids = [i for i, name in labels.items() if name in MALICIOUS_LABELS] or [max(labels)]
        self.batch_size = batch_size

    def windows(self, text: str, max_tokens: int, stride: int) -> list[tuple[int, int, str]]:
        enc = self.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
        return split_windows(enc["offset_mapping"], text, max_tokens - 2, stride)  # room for [CLS] and [SEP]

    def malicious_scores(self, texts: Sequence[str]) -> list[float]:
        scores: list[float] = []
        for i in range(0, len(texts), self.batch_size):
            batch = self.tokenizer(list(texts[i:i + self.batch_size]), padding=True, truncation=True,
                                   max_length=512, return_tensors="pt").to(self.device)
            with self.torch.no_grad():
                probs = self.model(**batch).logits.softmax(dim=-1)
            scores += probs[:, self.malicious_ids].sum(dim=-1).tolist()
        return scores


@dataclass(frozen=True)
class ServerSettings:
    threshold: float = 0.5   # E17 tunes it on real pages and injected pages
    max_tokens: int = 512
    stride: int = 64
    max_texts: int = 64


class ClassifyRequest(BaseModel):
    texts: list[str] = Field(default_factory=list)


def create_app(classifier_factory: Callable[[], Classifier], settings: ServerSettings | None = None) -> FastAPI:
    s = settings or ServerSettings()
    registry = CollectorRegistry()
    requests = Counter("guard_injection_texts_total", "Texts classified", ["verdict"], registry=registry)
    seconds = Histogram("guard_injection_seconds", "Classify latency", registry=registry,
                        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5))
    state: dict[str, Any] = {}
    # Session 1 (2026-09-29): FastAPI runs this sync handler in a thread pool, and a Hugging Face fast tokenizer
    # fails when two threads use it ("RuntimeError: Already borrowed": 35 errors in 30 minutes of capture, and
    # the page checks failed closed). One lock around the model work. One GPU gains nothing from threads.
    lock = threading.Lock()

    def clf() -> Classifier:
        if "clf" not in state:
            state["clf"] = classifier_factory()
        return state["clf"]

    app = FastAPI(title="guard-injection")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        clf()  # load the model once; readiness fails until it loads
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/classify")
    def classify(req: ClassifyRequest) -> dict[str, Any]:
        t0 = time.monotonic()
        texts = req.texts[: s.max_texts]
        with lock:
            model = clf()
            spans = [model.windows(t, s.max_tokens, s.stride) for t in texts]
            flat = [w[2] for ws in spans for w in ws]
            scores = iter(model.malicious_scores(flat)) if flat else iter(())
        results = []
        for ws in spans:
            windows = [{"start": a, "end": b, "score": round(next(scores), 4)} for a, b, _ in ws]
            top = max((w["score"] for w in windows), default=0.0)
            malicious = top >= s.threshold
            requests.labels("malicious" if malicious else "benign").inc()
            results.append({"malicious": malicious, "score": top, "windows": windows})
        seconds.observe(time.monotonic() - t0)
        return {"model": os.environ.get("GUARD_INJECTION_MODEL", ""), "threshold": s.threshold, "results": results}

    return app


def app() -> FastAPI:  # uvicorn --factory control.guard.injection_server:app
    model_id = os.environ.get("GUARD_INJECTION_MODEL", "protectai/deberta-v3-base-prompt-injection-v2")
    threshold = float(os.environ.get("GUARD_INJECTION_THRESHOLD", "0.5"))
    return create_app(lambda: HFClassifier(model_id), ServerSettings(threshold=threshold))
