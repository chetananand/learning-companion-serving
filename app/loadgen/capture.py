"""Capture the exact request body of each LLM call (for the replayer, 06-experiments.md section 2).

The trace file (FR-14) holds only the call shapes. The replayer needs the real prompts, so a capture
run saves each body that the app sends to `edge`, with the turn metadata. The capture file holds
bookmark text, so it stays on the cluster volume and in the laptop backup (NFR-03, DEBATE-LOG S3).
The replayer reads a frozen copy, so later turns do not change the replay input.
A write error never breaks a user turn: the capture logs one warning and stops.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from app.turn import current_turn

log = logging.getLogger(__name__)

KEEP_HEADERS = ("x-step", "x-prompt-version")


class Capture:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.count = 0
        self.failed = False

    def record(self, request: httpx.Request) -> None:
        if self.failed or not request.url.path.endswith("/chat/completions"):
            return
        try:
            body = json.loads(request.content)
        except (ValueError, httpx.RequestNotRead):
            return
        turn = current_turn()
        rec: dict[str, Any] = {
            "ts": time.time(),
            "turn_id": turn.turn_id if turn else None,
            "session_id": turn.session_id if turn else None,
            "tenant": turn.tenant if turn else None,
            "class": turn.request_class if turn else "interactive",
            "mode": turn.mode if turn else None,
            "step": request.headers.get("x-step", "unknown"),
            "prompt_version": request.headers.get("x-prompt-version"),
            "body": body,
        }
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError as exc:
                self.failed = True
                log.warning("capture stopped: cannot write %s (%s)", self.path, exc)
                return
            self.count += 1

    async def hook(self, request: httpx.Request) -> None:
        self.record(request)
