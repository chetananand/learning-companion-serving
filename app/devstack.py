"""A local dev stack: the Companion API with a fake edge and a fake data plane (no GPU, no cloud).

It serves the real API, agents, and middleware. Only the LLM (a script), the bookmarks, the web
search, and the pages are fakes. Use it to check the UI and to record UI screens for the slides.

Run: uv run python -m app.devstack  (API on 127.0.0.1:8000)
     COMPANION_API_URL=http://127.0.0.1:8000 uv run streamlit run app/ui/streamlit_app.py
"""

from __future__ import annotations

import datetime as dt
import tempfile

import uvicorn

from app.agent.tools import Toolbox
from app.api import create_app
from app.clients.websearch import SearchResult
from app.config import AppSettings
from app.llm import TraceRecorder, make_http_client, make_model
from app.metrics import AppMetrics
from app.pages import ReadPage
from app.prompts import PROMPT_VERSION
from app.service import Companion
from app.tests.fakes import FakeEdge, FakeGuard, FakeReader, FakeRetriever, FakeSearch, hit
from app.tests.test_agent_flow import BOOKMARK_URL, DOCS_URL, script


def build() -> object:
    trace = f"{tempfile.gettempdir()}/companion-dev-trace.jsonl"
    settings = AppSettings(edge_url="http://edge.dev/v1", trace_path=trace)
    edge = FakeEdge(script)
    http = make_http_client(settings, transport=edge.transport())
    models = {r: make_model(r, settings, http, PROMPT_VERSION) for r in ("quick", "agent", "verify")}
    metrics = AppMetrics()
    retriever = FakeRetriever([
        hit("b1-page-000", BOOKMARK_URL, "Install vLLM 0.2.1 and start the server with --tensor-parallel-size 2.",
            title="Deploy LLaMA 2, Mistral, and Mixtral, on AWS EC2 with vLLM", created=dt.date(2024, 3, 13)),
    ])
    search = FakeSearch([SearchResult("vLLM documentation", DOCS_URL, "vLLM 0.30.0 release notes")])
    reader = FakeReader({DOCS_URL: ReadPage(DOCS_URL, DOCS_URL, "vLLM 0.30.0 is the current release.", None, "html")})
    toolbox = Toolbox(settings, retriever, search, reader, FakeGuard(), metrics)
    companion = Companion(settings, toolbox, models, metrics, TraceRecorder(settings.trace_path))
    return create_app(companion, metrics)


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=8000)
