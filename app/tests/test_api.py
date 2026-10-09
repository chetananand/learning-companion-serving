"""Tests of the Companion API: the server-sent event stream and the input rules."""

from __future__ import annotations

import json

import httpx

from app.api import create_app
from app.tests.fakes import FakeEdge
from app.tests.test_agent_flow import DRAFT, make_companion, script


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        data = json.loads(lines["data"])
        assert data["event"] == lines["event"]
        events.append(data)
    return events


async def client_for(tmp_path) -> httpx.AsyncClient:
    companion, metrics = make_companion(tmp_path, FakeEdge(script))
    app = create_app(companion, metrics)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def test_turn_streams_server_sent_events(tmp_path):
    async with await client_for(tmp_path) as client:
        resp = await client.post("/v1/turns", json={"question": "How do I deploy Mixtral?", "session_id": "s9"})
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(resp.text)
    assert events[0]["event"] == "start" and events[0]["session_id"] == "s9"
    assert "".join(e["text"] for e in events if e["event"] == "token").strip() == DRAFT
    assert events[-1]["event"] == "done"
    async with await client_for(tmp_path) as client:
        metrics = (await client.get("/metrics")).text
    assert "companion_turns_total" in metrics


async def test_input_rules(tmp_path):
    async with await client_for(tmp_path) as client:
        assert (await client.post("/v1/turns", json={"question": ""})).status_code == 422
        assert (await client.post("/v1/turns", json={"question": "q", "mode": "deep"})).status_code == 422
        bad = await client.post("/v1/turns", json={"question": "q", "images": ["https://example.com/a.png"]})
        assert bad.status_code == 400
        many = await client.post("/v1/turns", json={"question": "q", "images": ["data:image/png;base64,AA"] * 3})
        assert many.status_code == 400
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        assert (await client.get("/readyz")).status_code == 200
