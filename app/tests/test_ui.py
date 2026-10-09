"""Tests of the UI helpers (the Streamlit page itself is checked by hand at Gate G0)."""

from app.ui.streamlit_app import claim_row, claims_markdown, iter_sse, sources_markdown


def test_iter_sse_parses_the_api_stream():
    lines = ['event: start', 'data: {"event": "start", "turn_id": "t"}', '',
             'event: token', 'data: {"event": "token", "phase": "answer", "text": "Hi"}', '']
    assert [e["event"] for e in iter_sse(iter(lines))] == ["start", "token"]


def test_claim_row():
    row = claim_row({"claim_id": "C1", "claim": "vLLM 0.2.1", "status": "updated", "path": "tier",
                     "live": {"url": "https://docs.vllm.ai/", "value": "0.30.0", "date": None},
                     "bookmark": {"ref": "B1", "date": "2024-03-13"}})
    assert row["verdict"] == "🔄 updated" and row["live value"] == "0.30.0" and row["bookmark date"] == "2024-03-13"


def test_markdown_tables():
    row = claim_row({"claim_id": "C1", "claim": "a | b", "status": "verified", "path": "supported",
                     "live": {"url": "https://docs.vllm.ai/", "value": None, "date": "2026-09-01"}, "bookmark": {}})
    md = claims_markdown([row])
    assert "a \\| b" in md and "[https://docs.vllm.ai/](https://docs.vllm.ai/)" in md
    src = sources_markdown([
        {"kind": "web", "ref": "W1", "url": "https://docs.vllm.ai/", "domain": "vllm.ai", "tier": 1},
        {"kind": "bookmark", "ref": "B2", "url": "https://x.com", "title": "X", "saved": "2024-01-01"},
        {"kind": "bookmark", "ref": "B10", "url": "https://y.com", "title": "Y"},
    ])
    assert src.index("[B2]") < src.index("[B10]") < src.index("[W1]")


def test_the_ui_has_no_overflow_switch_and_never_leaves():
    """The owner, 2026-09-29: with no provider, the switch "makes no sense". A UI turn stays in the cluster."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("ui", "streamlit_app.py").read_text()
    assert "Allow overflow" not in src and '"allow_overflow": False' in src
