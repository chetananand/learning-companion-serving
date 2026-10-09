"""The Streamlit UI (ADR-006, FR-10 to FR-12). It calls the Companion API and shows its events.

Run: streamlit run app/ui/streamlit_app.py --server.address 0.0.0.0 --server.port 8501
Env: COMPANION_API_URL (default http://companion-api.companion.svc.cluster.local:8000)
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import httpx

API_URL = os.environ.get("COMPANION_API_URL", "http://companion-api.companion.svc.cluster.local:8000")
STATUS_ICON = {"verified": "✅ verified", "updated": "🔄 updated", "disputed": "⚖️ disputed",
               "not_verified": "❔ not verified"}


def iter_sse(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Parse a server-sent event stream into the event dictionaries of the API."""
    data: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "" and data:
            yield json.loads("\n".join(data))
            data = []
    if data:
        yield json.loads("\n".join(data))


def stream_turn(body: dict[str, Any], api_url: str = API_URL) -> Iterator[dict[str, Any]]:
    with httpx.Client(timeout=httpx.Timeout(90.0, connect=5.0)) as client, \
            client.stream("POST", f"{api_url}/v1/turns", json=body) as resp:
        if resp.status_code != 200:
            yield {"event": "error", "code": resp.status_code, "reason": "api_error",
                   "message": resp.read().decode()[:300]}
            return
        yield from iter_sse(resp.iter_lines())


def claim_row(e: dict[str, Any]) -> dict[str, Any]:
    live = e.get("live") or {}
    return {"claim": e["claim_id"], "text": e.get("claim", ""), "verdict": STATUS_ICON.get(e["status"], e["status"]),
            "rule path": e.get("path"), "live value": live.get("value"), "live source": live.get("url"),
            "live date": live.get("date"), "bookmark date": (e.get("bookmark") or {}).get("date")}


def _cell(value: Any) -> str:
    return str(value if value not in (None, "") else "-").replace("|", "\\|").replace("\n", " ")


def claims_markdown(rows: list[dict[str, Any]]) -> str:
    """The verdict table as Markdown: it renders fast and keeps the links clickable."""
    head = "| Claim | Verdict | Rule path | Live value | Live source | Bookmark date |\n|---|---|---|---|---|---|"
    lines = []
    for r in rows:
        src = f"[{_cell(r['live source'])[:40]}]({r['live source']})" if r.get("live source") else "-"
        lines.append(f"| {r['claim']}: {_cell(r['text'])[:120]} | {r['verdict']} | {_cell(r['rule path'])} | "
                     f"{_cell(r['live value'])} | {src} | {_cell(r['bookmark date'])} |")
    return "\n".join([head, *lines])


def sources_markdown(sources: list[dict[str, Any]]) -> str:
    parts = []
    for s in sorted(sources, key=lambda s: (s["ref"][0], int(s["ref"][1:]))):
        if s.get("kind") == "bookmark":
            label = s.get("title") or s["url"]
            extra = f"saved {s.get('saved') or '?'}" + (" · dead link" if s.get("dead") else "")
        else:
            label = s.get("domain") or s["url"]
            extra = f"date {s.get('date') or 'unknown'} · tier {s.get('tier') or '-'} · {s.get('via', 'html')}"
        parts.append(f"[{s['ref']}] [{_cell(label)[:70]}]({s['url']}) ({extra})")
    return "  \n".join(parts)


def main() -> None:  # pragma: no cover - UI code; the parser above has tests
    import streamlit as st

    st.set_page_config(page_title="Learning Companion", page_icon="📚", layout="wide")
    ss = st.session_state
    ss.setdefault("session_id", uuid.uuid4().hex)
    ss.setdefault("history", [])

    with st.sidebar:
        st.header("Learning Companion")
        mode = st.radio("Mode", ["quick", "verified"], help="verified: check the volatile claims on the live web")
        upload = st.file_uploader("Image (PNG or JPEG)", type=["png", "jpg", "jpeg"])
        if st.button("New chat"):
            ss.session_id, ss.history = uuid.uuid4().hex, []
        st.caption(f"session {ss.session_id[:8]}")

    for item in ss.history:
        with st.chat_message(item["role"]):
            st.markdown(item["text"])
            if item.get("claims"):
                st.markdown(claims_markdown(item["claims"]))
            if item.get("sources"):
                st.caption(sources_markdown(item["sources"]))

    question = st.chat_input("Ask about your bookmarks")
    if not question:
        return
    ss.history.append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)

    images = []
    if upload is not None:
        kind = "png" if upload.type == "image/png" else "jpeg"
        images.append(f"data:image/{kind};base64,{base64.b64encode(upload.getvalue()).decode()}")

    # No overflow provider is set (the owner, 2026-09-29: the switch "makes no sense"). A UI turn never leaves the
    # cluster. The edge gate still decides stay or leave for E13, and it records would_leave with no provider.
    body = {"question": question, "mode": mode, "session_id": ss.session_id, "allow_overflow": False,
            "images": images}
    with st.chat_message("assistant"):
        status = st.status("Working", expanded=False)
        draft_box, claims_box, final_box, sources_box, foot = st.empty(), st.empty(), st.empty(), st.empty(), st.empty()
        draft, final, claims, sources = "", "", [], []
        for e in stream_turn(body):
            kind = e["event"]
            if kind == "step" and e.get("status") != "start":
                status.write(f"`{e['name']}` {e.get('detail', '')} ({e.get('ms', 0)} ms)")
            elif kind == "source":
                sources.append(e)
                status.write(f"[{e['ref']}] {e.get('title') or e.get('domain', '')} {e['url']}")
            elif kind == "guard":
                status.warning(f"The page check removed {e['removed_windows']} window(s) from {e['url']}")
            elif kind == "claim_start":
                status.update(label=f"Checking claim {e['claim_id']}")
            elif kind == "claim":
                claims.append(claim_row(e))
                claims_box.markdown(claims_markdown(claims))
            elif kind == "token" and e["phase"] in ("answer", "draft"):
                draft += e["text"]
                draft_box.markdown(draft if mode == "quick" else f"**Draft** (from the bookmarks)\n\n{draft}")
            elif kind == "token" and e["phase"] == "final":
                final += e["text"]
                final_box.markdown(final)
            elif kind == "error":
                retry = f" Try again in {e['retry_after']} s." if e.get("retry_after") else ""
                st.error(f"{e.get('code')} {e.get('reason')}: {e.get('message', '')}.{retry}")
            elif kind == "done":
                status.update(label=f"Done in {e['elapsed_ms']} ms", state="complete")
                if sources:
                    sources_box.caption(sources_markdown(sources))
                foot.caption(f"via {', '.join(e.get('via') or ['-'])} · {len(sources)} sources · "
                             f"{e.get('claims', 0)} claims checked · turn {e['turn_id'][:8]}")
        answer = final or draft
        if final:
            draft_box.empty()
            with st.expander("Draft from the bookmarks"):
                st.markdown(draft)
        ss.history.append({"role": "assistant", "text": answer, "claims": claims, "sources": sources})


if __name__ == "__main__":
    main()
