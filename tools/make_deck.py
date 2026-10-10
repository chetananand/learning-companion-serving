"""Write the slide deck of the talk (docs/spec/10-talk.md) as the files of a claude.ai slide deck.

Usage: python3 tools/make_deck.py <root>
  writes <root>/project/deck.json and <root>/project/slides/<id>.html, and <root>/deck-text.md (all slide text and
  notes, for tools/ste_lint.py). It prints the time of each slide (130 words each minute of notes) and each number
  on a slide that is not in the report (DESIGN.md, docs/results.md, docs/spec/, docs/budget-ledger.md).
The images are assets of the deck (ASSETS): plots/slides/ and the Grafana panel crops in plots/slides/panels/.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ASSETS = {
    "e3": "/_blob/54f351d14862f0849b7a3206cd05191c", "e9": "/_blob/33d6ecab663862b797ffccf742537e60",
    "hop": "/_blob/bef7f263ef04880cd591661ca8dedf8c", "warm": "/_blob/f6633cb512f077a6b00cb0fbdf1a49ed",
    "arch": "/_blob/40be9ee479f4bb16ea86c303db00815c",
    "cluster": "/_blob/c93f5728251ce5c26c873f9fc0da2440", "guard": "/_blob/51874d3a3f4d12ee3c96b0a08ca10aeb",
    "tenant": "/_blob/4466d622d255b88d350409463917aaca", "lmcache": "/_blob/bd6b9524acd3f31f149f04f6244c30d7",
    "queues": "/_blob/8bf5df6c9b3ea7d826771b4f749137af", "pd": "/_blob/64db200fb2311fad8d21915521ec3dc8",
    "overflow": "/_blob/88f1f4655c6ae3a220f8d19ddec374ae", "tokens": "/_blob/7dec7da661854b29c57e90efb61cfe72",
    "replicas": "/_blob/555b51655b33e9258f9abf12f0f93b21",
}
# The repo file of each asset (the same bytes, checked by sha256 on 2026-10-09). tools/deck_pdf.py uses them.
LOCAL = {
    "e3": "plots/slides/e3.png", "e9": "plots/slides/e9.png", "hop": "plots/slides/hop.png",
    "warm": "plots/slides/warm.png", "arch": "plots/slides/arch.png",
    "cluster": "plots/slides/panels/cluster-gpu-use.png", "guard": "plots/slides/panels/gateway-guard-rejects.png",
    "tenant": "plots/slides/panels/gateway-tenant-rejects.png",
    "lmcache": "plots/slides/panels/hop-lmcache-lookups.png",
    "queues": "plots/slides/panels/queues-depth-by-pod.png", "pd": "plots/slides/panels/router-pd-decisions.png",
    "overflow": "plots/slides/panels/success-overflow-gate.png",
    "tokens": "plots/slides/panels/vllm-tokens-per-second.png",
    "replicas": "plots/slides/panels/scaling-desired-replicas.png",
}
# Pixel sizes of the images (to keep the aspect ratio).
SIZES = {"e3": (1760, 980), "e9": (1739, 1035), "hop": (1421, 826), "warm": (1816, 862), "arch": (3400, 1552),
         "panel": (780, 296),
         "panel_short": (780, 268)}

PAPER, PAPER2, INK, SOFT, MUTED = "#FBFBF8", "#F1F3F6", "#1F2329", "#4A5260", "#6A7179"
BLUE, ORANGE, ORANGE_TEXT, LINE = "#2563C9", "#E07B2E", "#B4561A", "#C9CED6"
CODE_BG, CALLOUT_BG, BOX_BG = "#EAEEF3", "#FCEFE3", "#FFFEFA"
DARK, DARK_TEXT, DARK_SOFT, DARK_BLUE, DARK_ORANGE = "#1F2329", "#F3F4EF", "#C3C9D2", "#8DB4F7", "#F0A167"
SANS = "'IBM Plex Sans', Arial, sans-serif"
MONO = "'JetBrains Mono', 'Courier New', monospace"
STOPS = ["guard", "stay or leave", "admit", "place", "hop", "warm", "scale"]
FOOTER = "Chetan Anand · A learning companion on a scarce GPU"
REPO = "github.com/chetananand/learning-companion-serving"


def e(text: str) -> str:
    return html.escape(text, quote=False)


def p(text: str, size: int = 28, color: str = SOFT, weight: int = 400, extra: str = "", raw: bool = False) -> str:
    body = text if raw else e(text)
    more = f";{extra}" if extra else ""
    return (f'<p style="font-size:{size}px;line-height:1.35;color:{color};font-weight:{weight}{more}">'
            f"{body}</p>")


def title(text: str, color: str = INK) -> str:
    return (f'<h2 style="font-size:56px;font-weight:600;line-height:1.1;color:{color};letter-spacing:-0.5px">'
            f"{e(text)}</h2>")


def strip(active: list[str]) -> str:
    pills = []
    for s in STOPS:
        on = s in active
        style = (f"background:{BLUE};color:{PAPER};border:2px solid {BLUE}" if on
                 else f"color:{MUTED};border:2px solid {LINE}")
        pills.append(f'<p style="font-size:24px;line-height:1.2;padding:4px 16px;border-radius:999px;{style}">'
                     f"{e(s)}</p>")
    return f'<div style="display:flex;flex-direction:row;gap:12px">{"".join(pills)}</div>'


def code(lines: list[tuple[str, str]]) -> str:
    rows = "".join(f'<p style="font-family:{MONO};font-size:24px;line-height:1.35;color:{INK}">{e(a)}'
                   f'{" " if b else ""}<span style="color:{BLUE}">{e(b)}</span></p>' for a, b in lines)
    return (f'<div style="background:{CODE_BG};border-radius:12px;padding:14px 20px;display:flex;'
            f'flex-direction:column;gap:2px">{rows}</div>')


def proof(big: str, caption: str) -> str:
    return (f'<div style="display:flex;flex-direction:column;gap:2px">'
            f'<p style="font-size:56px;font-weight:600;line-height:1.1;color:{BLUE}">{e(big)}</p>'
            f'{p(caption, 24, SOFT)}</div>')


def callout(label: str, text: str) -> str:
    return (f'<div style="background:{CALLOUT_BG};border-left:8px solid {ORANGE};border-radius:0 12px 12px 0;'
            f'padding:14px 20px;display:flex;flex-direction:column;gap:4px">'
            f'<p style="font-size:24px;line-height:1.2;font-weight:600;color:{ORANGE_TEXT}">{e(label)}</p>'
            f'{p(text, 24, INK)}</div>')


def image(key: str, alt: str, caption: str, width: int, size: str = "panel") -> str:
    w, h = SIZES[size if key not in SIZES else key]
    height = round(width * h / w)
    return (f'<div style="display:flex;flex-direction:column;gap:6px">{p(caption, 24, MUTED)}'
            f'<img src="{ASSETS[key]}" alt="{e(alt)}" style="width:{width}px;height:{height}px;object-fit:contain;'
            f'border-radius:8px"></div>')


def footer(n: int, total: int, dark: bool = False) -> str:
    c = DARK_SOFT if dark else MUTED
    return (f'<div style="position:absolute;left:128px;right:128px;bottom:64px;display:flex;flex-direction:row;'
            f'justify-content:space-between">{p(FOOTER, 24, c)}{p(f"{n} / {total}", 24, c)}</div>')


def paragraphs(notes: str, most: int = 5) -> str:
    """Speaker notes as paragraphs of `most` sentences or fewer (the STE rule is 6 at most)."""
    sentences = re.split(r"(?<=[.!?])\s+", notes.strip())
    return "\n\n".join(" ".join(sentences[i:i + most]) for i in range(0, len(sentences), most))


def section(sid: str, body: str, notes: str, n: int, total: int, bg: str = PAPER, dark: bool = False,
            pad: str = "104px 128px 160px", gap: int = 28) -> str:
    color = DARK_TEXT if dark else INK
    return (f'<section id="{sid}" data-transition="fade" style="background:{bg};color:{color};font-family:{SANS};'
            f'padding:{pad};display:flex;flex-direction:column;gap:{gap}px">\n{body}\n{footer(n, total, dark)}\n'
            f"<aside>{e(paragraphs(notes))}</aside>\n</section>\n")


def two_columns(left: list[str], right: list[str], left_w: int = 720, gap_left: int = 18) -> str:
    return (f'<div style="display:flex;flex-direction:row;gap:48px">'
            f'<div style="width:{left_w}px;display:flex;flex-direction:column;gap:{gap_left}px">{"".join(left)}</div>'
            f'<div style="flex:1;display:flex;flex-direction:column;gap:16px">{"".join(right)}</div></div>')


def table(head: list[str], rows: list[list[str]], widths: list[int], size: int = 24) -> str:
    cell = "padding:6px 12px;text-align:left"
    th = "".join(f'<th style="width:{w}%;{cell}">{e(h)}</th>' for h, w in zip(head, widths, strict=True))
    trs = [f'<tr style="background:{CODE_BG}">{th}</tr>']
    for r in rows:
        trs.append("<tr>" + "".join(f'<td style="{cell}">{e(c)}</td>' for c in r) + "</tr>")
    return f'<table style="font-size:{size}px;color:{INK};border:1px solid {LINE}">{"".join(trs)}</table>'


# ----------------------------------------------------------------------------------------------------------------
# The architecture slide shows plots/slides/arch.png: the flow that tools/arch_diagram.py places by hand, in the
# Mermaid style, rendered at 2x with headless Chrome. The slide shows it at 1,700 px, so its text stays at 24 px.

# ----------------------------------------------------------------------------------------------------------------

def flow(steps: list[tuple[str, str, bool]]) -> str:
    """One user turn as boxes from top to bottom. A step with True occurs only in verified mode (orange)."""
    parts = []
    for i, (head, text, verified) in enumerate(steps):
        if i:
            label = " verified mode only" if verified and not steps[i - 1][2] else ""
            parts.append(p(f"↓{label}", 28, ORANGE_TEXT if label else MUTED, 600, "padding-left:24px"))
        line = ORANGE if verified else LINE
        parts.append(f'<div style="background:{BOX_BG};border:2px solid {line};border-radius:12px;padding:12px 20px;'
                     f'display:flex;flex-direction:column;gap:2px">{p(head, 28, INK, 600)}{p(text, 24, SOFT)}</div>')
    return f'<div style="display:flex;flex-direction:column;gap:6px">{"".join(parts)}</div>'


def steps(heading: str, items: list[str]) -> str:
    """A short flow: a heading, then one box for each step, from top to bottom."""
    parts = [p(heading, 28, INK, 600)]
    for i, text in enumerate(items):
        if i:
            parts.append(p("↓", 24, MUTED, 600, "padding-left:20px;line-height:1"))
        parts.append(f'<div style="background:{BOX_BG};border:2px solid {LINE};border-radius:10px;padding:8px 16px">'
                     f"{p(text, 24, INK)}</div>")
    return f'<div style="display:flex;flex-direction:column;gap:6px">{"".join(parts)}</div>'


def measured(text: str, label: str = "What we measured") -> str:
    return (f'<div style="display:flex;flex-direction:column;gap:2px">'
            f'<p style="font-size:24px;line-height:1.2;font-weight:600;color:{BLUE};text-transform:uppercase;'
            f'letter-spacing:1px">{e(label)}</p>{p(text, 28, INK)}</div>')


def play(text: str) -> str:
    return (f'<div style="display:flex;flex-direction:row;gap:14px;align-items:center;background:{CALLOUT_BG};'
            f'border-radius:12px;padding:12px 18px"><x-icon name="Play" style="color:{ORANGE_TEXT};width:36px;'
            f'height:36px"></x-icon>{p(text, 28, INK, 600)}</div>')


def slides() -> list[tuple[str, str, str]]:
    """(id, body html, notes) for each slide, in order. The section wrapper and the footer come later.

    Each result slide says what we measured (the traffic, the load, the metric) and the result. No internal
    labels (experiment ids, run names, rule names): JARGON in check() refuses them.
    """
    s: list[tuple[str, str, str]] = []

    s.append(("cover", "".join([
        p("AI Inference Engineering and Systems Design · Final project", 28, DARK_ORANGE, 600),
        f'<h1 style="font-size:88px;font-weight:600;line-height:1.05;color:{DARK_TEXT};letter-spacing:-1px">'
        "A learning companion on a scarce GPU</h1>",
        p("An agentic RAG app on our own vLLM cluster on Lambda GPUs. Admission control decides what enters, and "
          "the router decides where it goes. vLLM runs the model.", 32, DARK_SOFT),
        '<div style="flex:1"></div>',
        p("Chetan Anand", 32, DARK_TEXT, 600),
        p("2026-10-10", 24, DARK_SOFT),
        p(REPO, 28, DARK_BLUE),
    ]), "I built a learning companion over my bookmarks, on my own vLLM cluster. I walk one request through the "
        "code, and at each step I show what we measured."))

    s.append(("intro", "".join([
        title("The app: a learning companion over my bookmarks"),
        two_columns([
            p("My Notion database has 998 bookmarks of pages that I want to learn from. The companion answers my "
              "questions from these bookmarks, and it cites a source for each fact.", 28, INK),
            p("Quick mode: one agent searches the bookmarks, reads a page, and reads a figure with OCR.", 28),
            p("Verified mode: bookmarks get old, so a second agent checks up to 3 claims on live web pages. Our "
              "code decides when a live fact wins over a bookmark.", 28),
            callout("In course terms", "Track B with a Track A tool: an agent that retrieves."),
            play("Clip 1, 27 s: a verified answer, both claims verified"),
        ], [
            flow([("I ask a question", "in the chat of the app", False),
                  ("One agent finds the facts", "search the bookmarks, read a page, read a figure", False),
                  ("The answer", "with a source for each fact", False),
                  ("A second agent checks the claims", "up to 3 claims, on live web pages", True),
                  ("The final answer", "with the status of each claim", True)]),
        ]),
    ]), "First, the app. My Notion database has 998 bookmarks of pages that I want to learn from. The companion "
        "answers my questions from them and cites each source. In quick mode, one agent searches the bookmarks, "
        "reads pages, and reads figures with OCR. In verified mode, a second agent checks up to 3 claims on live "
        "web pages, and our code decides when a live fact wins. Each LLM call of these agents goes to our own "
        "vLLM cluster. Now clip 1."))

    s.append(("arch", "".join([
        title("The path of one LLM call"),
        (f'<img src="{ASSETS["arch"]}" alt="Flow diagram of one LLM call: the app, admission control and routing '
         f'(edge, guard models, Envoy AI Gateway, llm-d router), and the engine (prefill pod, decode pod, LMCache '
         f'server), steps 1 to 6" style="width:1700px;height:{round(1700 * SIZES["arch"][1] / SIZES["arch"][0])}px;'
         f'object-fit:contain;align-self:center">'),
    ]), "This is the path of one LLM call. Admission control decides if the call may enter, and the router "
        "decides where it goes. Each box decides or does one thing, and each arrow names what moves. 1: the app "
        "sends the request to edge, our code. 2: edge asks the guard models if the prompt is safe. 3: edge sends "
        "the request to the Envoy AI Gateway, which checks the token budget of the tenant. 4: Envoy sends the "
        "prompt to the llm-d router, and the router sends back only the pod addresses. 5: Envoy sends the request"
        " to the decode pod. The orange steps occur only when the router also picked a prefill pod. 5a: the "
        "sidecar sends the prompt to the prefill pod, only to compute its KV. 5b: vLLM there sends a copy of the "
        "KV to the LMCache server. 5c: the store barrier replies only after the store. 6: vLLM in the decode pod "
        "loads the stored KV and generates the answer."))

    s.append(("models", "".join([
        title("The models, and the job of each"),
        table(["Job", "Model", "Where it runs"],
              [["The answers and all agent steps", "Gemma 4 31B, FP8 (RedHatAI), on vLLM v0.30.0",
                "node 1: the prefill pod and the decode pod, one H100 each"],
               ["Prompt injection check", "Llama Prompt Guard 2, 86M", "node 2: a GPU slice of 4,000 MiB"],
               ["Content safety check", "Nemotron 3.5 Content Safety, in NeMo Guardrails",
                "node 2: a GPU slice of 16,000 MiB"],
               ["Embeddings for the bookmark search", "BAAI bge-m3", "node 2: SIE, a GPU slice of 12,000 MiB"],
               ["Rerank of the search results", "Qwen3 Reranker 4B", "node 2: SIE, the same slice"],
               ["Text in figures (OCR)", "LightOnOCR-2, 1B", "node 2: SIE, a GPU slice of 20,000 MiB"]],
              [30, 38, 32]),
        p("HAMi cuts one H100 of node 2 into these slices. On the A100 node, the small models shared one GPU.", 24),
        callout("Why Gemma 4 31B", "A tested FP8 checkpoint that fits one H100. It passed all 7 of our gate tests, "
                "with 97.5% correct tool calls. We dropped Qwen3.8-27B for its open vLLM bugs in the prefix "
                "cache."),
    ]), "These are the models. One LLM, Gemma 4 31B in FP8, runs all agent steps on vLLM. It fits one H100, and "
        "it passed all 7 of our gate tests, with 97.5% correct tool calls. Small models do the rest: two guard "
        "models, and the search and OCR models in SIE."))

    s.append(("search", "".join([
        title("The bookmark search: ingest once, then search in each turn"),
        two_columns([
            steps("Ingest: a job in the cluster, before all tests", [
                "Read the Notion rows, and fetch each page",
                "OCR on SIE when a page has little text",
                "Cut the text into chunks of 500 to 800 tokens",
                "Page check (Prompt Guard 2): hide text that gives orders to the LLM",
                "SIE embeds each chunk (bge-m3), into Qdrant",
            ]),
            p("It ran on 2026-09-28: 910 of 1,000 rows gave 4,112 chunks. Each later session restored a snapshot of "
              "Qdrant from the laptop.", 24),
        ], [
            steps("Search: in each turn, a tool of the agent", [
                "SIE embeds the question (bge-m3)",
                "Qdrant finds 30 chunks: vector search and BM25, fused (RRF)",
                "SIE reranks the 30 chunks (Qwen3 Reranker 4B)",
                "The agent gets the top 8, with their bookmark links",
            ]),
            callout("SIE and Qdrant", "SIE is the model server for the small models: embed, rerank, and OCR. Qdrant "
                    "stores the vectors and searches them. The search makes no LLM call."),
        ], left_w=760),
    ]), "This is the bookmark search. The ingest ran once, as a job in the cluster, before all tests. It fetched "
        "each bookmarked page and cut the text into chunks. The page check hid any part of a page that looked "
        "like an order to the LLM. The check has a limit. In our test with 50 injected pages, it caught only half"
        " of them, even at a more sensitive setting. It flagged no real page. In the fact check, a second layer "
        "helps: code, not the LLM, decides when a live page wins over a bookmark. So a page cannot argue its way "
        "to a win. Then SIE turned each chunk into a vector in Qdrant. In each turn, SIE turns the question into "
        "a vector. Qdrant finds 30 chunks by vector and by keyword, and SIE reranks them. The agent gets the top "
        "8. SIE serves only the small models, so the search makes no LLM call."))

    s.append(("deploy", "".join([
        title("The deployment: two nodes, and an A100 fallback"),
        two_columns([
            callout("Node 1 · 2 × H100 SXM 80 GB · the engine",
                    "vllm-prefill on GPU 0 and vllm-decode on GPU 1, each on a full GPU. The LMCache server: up to "
                    "250 GiB of the 450 GiB of CPU RAM."),
            callout("Node 2 · 1 × H100 80 GB · control and data",
                    "HAMi slices of one GPU for the SIE and guard models. edge, guard, Envoy, the router, the app, "
                    "Qdrant, Redis, Prometheus, Grafana, and KEDA."),
            callout("2026-10-01 · one node · 8 × A100 80 GB",
                    "No H100 had stock. HAMi on all GPUs: each engine pod gets a full GPU, and the node 2 pods share "
                    "one GPU."),
            p("The laptop reaches the cluster only through an SSH tunnel to ports on 127.0.0.1.", 24, SOFT),
        ], [
            image("cluster", "Grafana panel GPU use: gpu0 and gpu1 at 100%",
                  "Dashboard 1 · Cluster: the use of each engine GPU at 150% load. Both GPUs are at 100%.",
                  896),
        ]),
    ]), "The deployment has two nodes on Lambda. Node 1 has two H100 GPUs for the engine: one prefill pod and one"
        " decode pod, each on a full GPU. The LMCache server may use up to 250 GiB of the 450 GiB of CPU RAM on "
        "node 1. In the A100 tests, it held up to 214 GiB. Node 2 runs everything else, and HAMi slices one GPU "
        "for the small models. On 2026-10-01 no H100 had stock, so one node with eight A100 GPUs ran all pods."))

    s.append(("app", "".join([
        title("What the app sends: short agent steps with a cached prefix"),
        two_columns([
            proof("64%", "of the prompt of an agent step was in the prefix cache"),
            proof("60%", "of the calls had fewer than 2,048 new tokens"),
            measured("We replay recorded app turns at random times. A turn is one question with all its LLM "
                     "calls. 100% load is 54 turns each minute, on average. At this rate, a soak test that adds "
                     "load each minute refused its first call.", "How we load the cluster"),
        ], [
            measured("The tokens of 1,606 real app calls, from the Envoy logs of four capture runs."),
            table(["Step", "Calls", "Prompt tokens (median)", "Share in the prefix cache", "New tokens (median)"],
                  [["quick", "978", "5,121", "0.19", "4,515"], ["verify", "321", "1,427", "0.65", "133"],
                   ["agent", "303", "2,121", "0.64", "459"]], [22, 16, 22, 22, 18]),
            p("The quick steps come from the quick agent. The agent steps come from the second agent, which picks "
              "the claims and writes the final answer. The verify steps come from its helper, which checks one "
              "claim. New tokens are the prompt tokens that were not in the prefix cache, so the engine computed "
              "them.", 24),
            callout("What this means", "The quick steps carry the bookmark chunks that the search found, so most "
                    "of their tokens are new: the Track A shape. The agent steps share a long prefix: the Track B "
                    "shape."),
        ]),
    ]), "This is what the app sends to the cluster. The second agent has two parts. The agent steps pick the "
        "claims and write the final answer. The verify steps check one claim each, on the web. An agent step "
        "found 64% of its prompt in the cache, and 60% of the calls had fewer than 2,048 new tokens. In the load "
        "tests, we replay recorded app turns. A turn is one question with all its LLM calls. 100% load is 54 "
        "turns each minute, on average: the rate where the soak test refused its first call."))

    s.append(("guard", "".join([
        strip(["guard", "stay or leave"]),
        title("Guard and stay or leave happen before any GPU work"),
        two_columns([
            code([("control/edge/guard.py:105", "inspect()"), ("control/edge/overflow.py:19", "should_leave()")]),
            measured("200 chat turns through the full path: 22 prompt-injection attacks and 178 normal turns."),
            proof("22 of 22 attacks blocked", "and 0 of 178 normal turns blocked. One check took 0.19 s (p50)."),
            measured("At 150% load, which calls the leave gate let go. A 429, a 500, and a batch call must stay.",
                     "What we measured: stay or leave"),
            p("The gate let only the 124 interactive 503 timeout_queue calls go. No 429 left.", 28, INK, 600),
        ], [
            image("guard", "Grafana panel: guard rejects by stage and reason",
                  "Dashboard 3 · Gateway + admission: guard rejects by stage and reason", 680),
            image("overflow", "Grafana panel: the leave gate, refused no_provider",
                  "Dashboard 2 · Success and failures: the calls that may leave. The overflow was off, so they "
                  "got a 503.", 680),
        ]),
    ]), "Stop one is the guard. inspect() runs fixed rules on the CPU, then Prompt Guard 2 and NeMo Guardrails. "
        "We sent 200 chat turns, 22 of them attacks. The guard blocked all 22 attacks and no normal turn. Stop "
        "two is stay or leave. should_leave() keeps 429, 500, and slice_oom on our cluster. Only an interactive "
        "capacity refusal may leave. At 150% load the gate let only the 124 interactive 503 calls go, and no 429."))

    s.append(("admit", "".join([
        strip(["admit"]),
        title("Admit: we refuse work at the door, not in the engine"),
        two_columns([
            code([("control/router/policy.yaml", "tenants, flow_control"), ("control/edge/admit.py:23", "slice_oom")]),
            p("Each tenant has a token budget for each minute. The router stops the dispatch to a pod with 5 queued "
              "requests or 90% KV use.", 28),
            measured("In all load tests, from 50% to 150% load: the preemptions in the vLLM engine."),
            proof("0 preemptions", "The router refused the extra work before the KV was full."),
            measured("Tenant test: one noisy tenant over its token budget, at 100% load."),
            p("55 calls of the noisy tenant got 429 tenant_tokens. The other tenants kept their service.", 28, INK,
              600),
        ], [
            image("tenant", "Grafana panel: tenant rate-limit rejects, tenant_tokens",
                  "Dashboard 3 · Gateway + admission: the 429 tenant_tokens rejects each second in the tenant "
                  "test", 896),
        ]),
    ]), "Stop three is admit. The policy file holds our numbers. The Envoy AI Gateway counts the tokens of each "
        "tenant. The router keeps two priority bands with a time limit, and it stops the dispatch at 90% KV use. In "
        "all load tests the engine preempted nothing, because the router refused the extra work first. In the "
        "tenant test the noisy tenant got 55 429 replies, and the others kept their service."))

    s.append(("place", "".join([
        strip(["place"]),
        title("Place: prefix match first, then load"),
        two_columns([
            code([("control/router/policy.yaml", "weights"), ("control/router/render.py:50", "render_epp_config()")]),
            measured("Stale-metrics test: one of two pods sends a frozen copy of its metrics from an empty moment, "
                     "at 100% load. We measured its share of the work."),
            proof("80% of the work", "went to that pod. With real metrics, it got 51%."),
            callout("What the data changed",
                    "We cleared the prefix cache of one pod in a run. The router did not act on the clear, and "
                    "all 15 warm sessions went to that pod again and missed the cache once."),
        ], [
            image("pd", "Grafana panel: P/D decisions, decode-only and prefill-decode",
                  "Dashboard 4 · Router: the decision for each call at 150% load: decode only, or split", 680),
            image("queues", "Grafana panel: queue depth for each pod",
                  "Dashboard 5 · Queue depth by pod: decode up to 47, prefill up to 2", 680),
        ]),
    ]), "Stop four is place. The router scores each pod: the prefix match counts most, then the session, the "
        "queue depth, the KV use, and the ramp. A pod with metrics older than 2 seconds counts as full. In the "
        "stale-metrics test, a frozen copy of the metrics of an empty pod pulled 80% of the work to that pod. And"
        " after a cache clear, the router still sent the warm sessions to the cleared pod."))

    s.append(("hop", "".join([
        strip(["hop"]),
        title("The hop: the KV moves through the LMCache server"),
        two_columns([
            code([("control/router/policy.yaml", "pd_non_cached_tokens: 2048"),
                  ("control/barrier/proxy.py:96", "Barrier"), ("tools/hop_records.py", "one record each hop")]),
            measured("Hop test: split requests of about 9,000 tokens. The time to the first token, by the path "
                     "of the KV."),
            proof("0.52 to 0.78 s", "through the LMCache server, against 4.1 to 4.3 s through NIXL over TCP"),
            callout("What the data changed",
                    "With no store barrier, the decode pod looked for the KV before the store ended: 0 hits in 10 "
                    "tries. The barrier now holds the prefill answer until the store ends."),
        ], [
            image("lmcache", "Grafana panel: LMCache lookups, hit and requested tokens",
                  "Dashboard 7 · Hop store: the tokens that the pods ask the LMCache server for, and the hit "
                  "tokens", 800,
                  "panel_short"),
            p("A hop record, from the Envoy log:", 24, MUTED),
            (f'<div style="background:{CODE_BG};border-radius:12px;padding:14px 20px">'
             f'<p style="font-family:{MONO};font-size:24px;line-height:1.35;color:{INK}">'
             '{"src": "10.42.1.21", "dst": "10.42.1.20",<br>"tokens": 6624, "prefix_tokens": 6400,<br>'
             '"backend": "lmcache"}</p></div>'),
            p("6,400 tokens are 25 full chunks of 256. The decode pod computes the KV of only the last 224 tokens.", 24,
              SOFT),
        ]),
    ]), "Stop five is the hop. The router splits a request only when 2,048 or more of its tokens are not in a "
        "cache. The prefill pod computes the KV of the prompt. The LMCache connector in vLLM copies the KV chunks"
        " to the LMCache server in CPU RAM, and the decode pod loads them. Our code only records the hop, and the"
        " store barrier holds the prefill answer until the store ends. In the hop test, the first token came in "
        "0.52 to 0.78 seconds, against about 4 seconds for NIXL over TCP."))

    s.append(("warm", "".join([
        strip(["warm"]),
        title("A pod with its weights on the GPU is not warm yet"),
        two_columns([
            code([("control/warm/logic.py:32", "is_warm()"), ("control/warm/logic.py:36", "next_ramp()")]),
            measured("Restart test at 70% load: we deleted one decode pod. The TTFT p95 of the calls on the new "
                     "pod, in its first minute."),
            proof("7.3 s against 10.9 s", "with the warmup routine, and with none. The warmup made the outage 15 s "
                  "longer."),
            callout("What the data changed",
                    "The ramp is a score, not a cap. In the first 10 s, the new pod got 75% of the calls."),
        ], [
            image("warm", "Chart: first-minute TTFT p95 with and without the warmup, and ramp against jump",
                  "The TTFT p95 of the calls on the new pod, in its first minute (H100)", 896),
        ], left_w=700),
    ]), "Stop six is declare warm. A pod with its weights on the GPU is not warm yet. The warm controller sends our "
        "system prompts, the shapes of our app, and a 4,000-token probe. The pod gets the warm label only if the "
        "probe is fast enough. Then it gets 10% of the traffic weight, and more while the TTFT holds. In the "
        "restart test, the warmup cut the first-minute p95 from 10.9 to 7.3 seconds. The ramp cut it from 57.3 to "
        "14.6 seconds."))

    s.append(("hypothesis", "".join([
        title("The decode pod was the limit, not prefill compute"),
        two_columns([
            table(["What we expected first", "Result"], [
                ["RAG: prefill compute", "Partly. Only for long prompts."],
                ["Agents: KV blocks", "Partly. The router held KV at 90%."],
                ["Not the weights", "Right. 30.4 of 71.7 GiB."],
                ["Not the interconnect", "Right for LMCache. Wrong for NIXL over TCP."],
                ["Not the scheduler", "Wrong. The decode queue held 50 requests."],
            ], [42, 58]),
            measured("At 100% load: the prompt tokens that each pod processed each second."),
            proof("16,200 against 4,550", "on the decode pod and on the prefill pod"),
        ], [
            image("tokens", "Grafana panel: vLLM tokens per second for each pod",
                  "Dashboard 6 · vLLM: the prompt tokens each second for each pod, at 150% load", 860, "panel_short"),
        ], left_w=760),
    ]), "The handout asked which limit we expected first. We expected prefill compute for the RAG traffic and KV "
        "blocks for the agents. The answer: partly right. The decode pod was the limit. Most agent calls have "
        "fewer than 2,048 new tokens. The router does not split them, so the decode pod runs their prefill too. "
        "At 100% load it processed 16,200 prompt tokens each second, and the prefill pod 4,550."))

    s.append(("topology", "".join([
        title("For our traffic, two whole pods beat a P/D split"),
        two_columns([
            measured("The same recorded app traffic at three loads, on two layouts: one prefill pod and one decode "
                     "pod (P/D), or two whole pods. The TTFT p50 of the interactive calls."),
            proof("0.84 s against 4.59 s", "at 100% load on the H100: two whole pods against P/D. The A100 tests "
                  "agree."),
            p("Why: only 10% to 13% of the calls split, so the decode pod runs the prefill of most prompts.", 28),
            callout("What the data changed",
                    "We keep the split for long uncached prompts. A P/D layout needs two or more pods in each "
                    "pool, or one restart stops all split calls."),
        ], [
            image("e3", "Chart: TTFT p50 of P/D against two whole pods, on the H100 and on the A100",
                  "The TTFT p50 of the interactive calls, by layout and load", 960),
        ], left_w=640),
    ]), "This test sends the same recorded app traffic to two layouts, at three loads. Two whole pods with "
        "prefix routing beat one prefill pod and one decode pod at each load, on the H100 and on the A100. At "
        "100% load the TTFT p50 was 0.84 seconds, against 4.59. The split still helps a long uncached prompt. "
        "But a P/D layout needs two pods in each pool: when the one prefill engine restarted, 60 split calls got "
        "no endpoint."))

    s.append(("scale", "".join([
        strip(["scale"]),
        title("Scale: the planner names the pool"),
        two_columns([
            code([("monitoring/rules.yaml:18", "prefill: uncached tokens"),
                  ("monitoring/rules.yaml:22", "decode: running"), ("KEDA", "one ScaledObject each pool")]),
            measured("Scale test on 8 × A100, one pool at a time: the time from the planner request to a new pod."),
            proof("15 s to the new pod", "in both pools. The new pod was warm about 4 minutes later."),
            play("Clip 2, 34 s, at 8 times speed: the replica panel"),
            callout("What the data changed",
                    "The prefill capacity value must match the GPU. With the H100 value, the planner asked for no "
                    "second prefill pod on the A100."),
        ], [
            image("replicas", "Grafana panel: desired against actual replicas in the scale test",
                  "Dashboard 8 · Pods / replicas / KEDA, in the scale test. Green: the decode pods that the "
                  "planner asks for. Blue: the ready decode pods, at most 2 in this test.", 896),
        ], left_w=720),
    ]), "Stop seven is scale. The planner rules name the pool: uncached prefill tokens for the prefill pool, and "
        "running requests for the decode pool. KEDA reads them. Dashboard 8 shows the decode pods that the "
        "planner asks for, and the ready pods. The test allowed at most 2 pods in each pool. In the scale test, "
        "KEDA made the new pod 15 seconds after the request, in both pools. The pod was warm about 4 minutes "
        "later, so on a spike that is the real reaction time. The capacity value must match the GPU. Now clip 2, "
        "at 8 times speed."))

    cards = [("Whole pods", "for our traffic. Split only long uncached prompts."),
             ("A store barrier", "for the hop: completion is not visibility."),
             ("A cap for the ramp", "not only a score."),
             ("GPU values", "from the GPU: the planner capacity and the warm baseline."),
             ("Apply a cache clear", "in the router, or each warm session misses the cache once.")]
    card_html = "".join(
        f'<div style="flex:1;background:#2A3039;border-radius:14px;padding:18px 20px;display:flex;'
        f'flex-direction:column;gap:6px">{p(a, 28, DARK_BLUE, 600)}{p(b, 24, DARK_SOFT)}</div>' for a, b in cards)
    s.append(("changed", "".join([
        title("What the data changed in our design", DARK_TEXT),
        f'<div style="display:flex;flex-direction:row;gap:16px">{card_html}</div>',
        p("10 times the traffic: more decode capacity first, 32 sequences and fp8 KV, and more RAM for LMCache. The "
          "wrong knobs: more prefill pods, longer queues, and a lower split threshold.", 28, DARK_TEXT),
        p("GPU cost: 237 USD of the 400 USD credit. The repo has the report, the notebooks, and each run.", 28,
          DARK_SOFT),
        '<div style="flex:1"></div>',
        (f'<div style="display:flex;flex-direction:row;justify-content:space-between;align-items:baseline">'
         f'{p("Questions?", 56, DARK_ORANGE, 600)}{p(REPO, 32, DARK_BLUE)}</div>'),
    ]), "Five things changed in the design because of the data. Two whole pods for our traffic. A store barrier "
        "for the hop. A cap for the ramp. Values like the planner capacity must come from the GPU. And the router"
        " must apply a cache clear. At 10 times the traffic I add decode capacity first, with 32 sequences, fp8 "
        "KV, and more CPU RAM for the LMCache server. The wrong knobs are more prefill pods, longer queues, and a"
        " lower split threshold. The GPU time cost 237 dollars. Thank you."))

    # ----------------------------------------------------------------------------------------------------------
    # The appendix (for questions only).
    q1 = [["What is the app? Shared and unique tokens?", "64% of an agent prompt is in the cache", "Envoy logs"],
          ["What dies at guard, admit, place, queue?", "400 prompt_injection, 429 tenant_tokens, 503",
           "docs/results.md"],
          ["Where do we stop work that will time out?", "band time limits: 10 s and 120 s", "policy.yaml:45"],
          ["Where do we protect KV?", "the router stops the dispatch at 90%", "policy.yaml:57"],
          ["Where do we give priority to interactive?", "priority bands, holdback policy", "policy.yaml:47"],
          ["Where do we stop one tenant?", "Agent Router token windows", "policy.yaml:61"],
          ["Where do we hop? What is not copied?", "2,048+ uncached tokens, and the last part chunk", "hops.jsonl"]]
    q2 = [["Where do we evict? What becomes a ghost?", "vLLM and LMCache evict, and a cleared prefix",
           "ghost_probe.py"],
          ["Where does the engine scheduler sit?", "inside each vLLM pod, after our queue", "manifests/engine"],
          ["What limited concurrency?", "the decode pod", "vLLM dashboard"],
          ["Four production alerts?", "TTFT burn, shed rate, KV pressure, hop failures", "rules.yaml"],
          ["If we scale, which pool?", "the pool that the planner names", "rules.yaml"],
          ["What changes at 10 times the traffic?", "decode capacity, 32 sequences, fp8 KV", "DESIGN.md"],
          ["Three wrong knobs?", "prefill pods, longer queues, lower split", "DESIGN.md"]]
    for sid, rows, part in (("a-questions-1", q1, "1 of 2"), ("a-questions-2", q2, "2 of 2")):
        s.append((sid, "".join([
            p("Appendix", 24, ORANGE_TEXT, 600),
            title(f"The handout questions and their evidence ({part})"),
            table(["Question", "Answer", "Evidence"], rows, [38, 40, 22]),
        ]), "For questions only. Each answer points at a file in the repo."))

    scrape = [
        "# the vllm-decode pod",
        'vllm:num_requests_running{engine="0",model_name="companion"} 1.0',
        'vllm:kv_cache_usage_perc{engine="0",model_name="companion"} 0.01538461538461533',
        'vllm:num_preemptions_total{engine="0",model_name="companion"} 0.0',
        'vllm:prompt_tokens_total{engine="0",model_name="companion"} 5.426828e+06',
        "# edge",
        'orch_shed_total{code="400",reason="prompt_injection",stage="guard"} 7.0',
        'orch_shed_total{code="503",reason="timeout_queue",stage="flow_control"} 138.0',
        'orch_overflow_refused_total{why="no_provider"} 89.0',
        "# the store barrier",
        "barrier_hold_seconds_sum 85.915873",
        "barrier_hold_seconds_count 171",
    ]
    lines = "<br>".join(e(x) for x in scrape)
    s.append(("a-scrape", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("A raw /metrics scrape of a live engine"),
        (f'<div style="background:{CODE_BG};border-radius:12px;padding:18px 24px">'
         f'<p style="font-family:{MONO};font-size:24px;line-height:1.4;color:{INK}">{lines}</p></div>'),
        p("Saved from the live A100 node before we stopped it. The report has more scrapes.", 24, MUTED),
    ]), "For questions only. These lines come from the live engine, the edge, and the store barrier."))

    s.append(("a-faults", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("Faults that we found and fixed"),
        table(["Fault", "What happened", "Fix"], [
            ["hop race", "The decode lookup ran before LMCache ended the store.", "the store barrier"],
            ["Envoy buffer", "Envoy gave 413 for prompts of about 12K tokens.", "a 4 MiB buffer"],
            ["gateway timeout", "The AI Gateway stopped each request at 60 s.", "a timeout of 300 s"],
            ["lost pod label", "The warm-controller series lost the pod label.", "honorLabels: true"],
            ["prefill restart", "The one prefill engine restarted under overload.", "not fixed: two pods"],
            ["cache reset", "The cache reset said 200 with success false.", "a forced reset"],
            ["hop records", "The hop records named NIXL for each run.", "the real connector"],
        ], [20, 52, 28]),
    ]), "For questions only. The session logs list each fault with its fix and its test."))

    s.append(("a-demo", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("The demo questions: 8 of 12 on the H100, 6 of 12 on the A100"),
        table(["Run", "Passed", "What failed"], [
            ["H100, 2026-09-30", "8 of 12", "4 questions in the fact check"],
            ["A100, 2026-10-01", "6 of 12", "5 questions in the fact check, and 1 slow first token"],
            ["After the fixes", "the OCR question", "Claim checks still time out or find no evidence."],
        ], [26, 22, 52]),
        p("The failures are in the fact check, not in the serve path.", 28),
    ]), "For questions only. Twelve fixed demo questions test the whole app."))

    s.append(("a-part5", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("The queue questions, with the notebook answers"),
        table(["Question", "Answer"], [
            ["Our queue or the vLLM queue?", "ours before the pick (up to 48 and 24), vLLM after (up to 50)"],
            ["Waiting, running, preempted?", "decode: 24 running and 50 waiting, and 0 preemptions"],
            ["Queue depth for each pod?", "6 or fewer for most traffic, and up to 36 for the mixed traffic"],
            ["A 32K retrieve and an agent step?", "the agent step goes first, in all rounds"],
            ["PagedAttention or prefix cache?", "the prefix cache saved prefill work, and fp8 KV saved memory"],
            ["Engine flags?", "decode: 24 sequences, chunks of 2,560. Prefill: 8, chunks of 16,384."],
            ["KV full after admit?", "we refuse at the door, and no pod preempted"],
            ["Client gone?", "vLLM abort_requests frees the KV"],
            ["A worker returns?", "a ramp while the p99 holds"],
        ], [38, 62]),
    ]), "For questions only. The answers, with plots, are in notebook/part5_queue.ipynb."))

    s.append(("a-cost", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("The cost of each GPU block"),
        table(["Date (PDT)", "Shape", "Hours", "USD"], [
            ["2026-09-27", "2 × H100 SXM (first engine test)", "0.37", "3.07"],
            ["2026-09-28", "2 × A6000 (first platform test)", "1.92", "4.17"],
            ["2026-09-29", "1 × H100 SXM (node 2)", "11.40", "48.91"],
            ["2026-09-29", "2 × H100 SXM (node 1)", "9.85", "82.56"],
            ["2026-09-30", "4 × H100 SXM (no node 2 came)", "0.75", "12.20"],
            ["2026-10-01", "8 × A100 80 GB (one node)", "3.88", "86.51"],
            ["Total", "of the 400 USD credit", "", "237.43"],
        ], [22, 46, 14, 18]),
    ]), "For questions only. Each block is in docs/budget-ledger.md. A spend guard stopped each GPU at the limits."))

    s.append(("a-hop", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("The hop: the LMCache server against NIXL"),
        image("hop", "Chart: TTFT median of a split request, LMCache server against NIXL over TCP",
              "The time to the first token of a split request of about 9,000 tokens", 1100),
    ]), "For questions only. NIXL between two pods used TCP, because the nodes have no RDMA."))

    s.append(("a-scale", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("The scale test: the planner against KEDA, in each pool"),
        image("e9", "Chart: the pods that the planner asks for against the pods that KEDA runs, in each pool",
              "The pods that the planner asks for, and the pods that KEDA runs. One pool at a time.", 1000),
    ]), "For questions only. The decode pool scaled 15 seconds after the planner asked. The prefill pool "
        "scaled only after we set its capacity value for the A100."))
    return s


JARGON = re.compile(r"\b(E\d{1,2}|G\d|H-\d+|D-\d+|M\d|F2|SLO-\d|U-\d+|ADR-\d+|RATE100)\b|rule F2|\bfault \d+"
                    r"|\b[a-z]\d+(?:-[a-z0-9]+)+\b|\bPart \d\b")


def write(root: Path) -> list[tuple[str, str, str]]:
    deck = slides()
    total_main = next(i for i, (sid, _, _) in enumerate(deck, 1) if sid == "changed")
    (root / "project" / "slides").mkdir(parents=True, exist_ok=True)
    for n, (sid, body, notes) in enumerate(deck, 1):
        dark = sid in ("cover", "changed")
        appendix = sid.startswith("a-")
        bg = DARK if dark else PAPER2 if appendix else PAPER
        pad = {"cover": "128px 128px 160px", "arch": "64px 110px 120px"}.get(sid, "104px 128px 160px")
        number = n if n <= total_main else n - total_main
        label_total = total_main if n <= total_main else len(deck) - total_main
        html_text = section(sid, body, notes, number, label_total, bg=bg, dark=dark, pad=pad,
                            gap=20 if sid == "arch" else 28)
        if appendix:
            html_text = html_text.replace(f">{number} / {label_total}<", f">A{number} / A{label_total}<")
        (root / "project" / "slides" / f"{sid}.html").write_text(html_text)
    index = {"v": 4, "createdOnFiles": {"v": 1, "at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
             "lists": "css", "title": "A learning companion on a scarce GPU", "cover": "cover",
             "order": [sid for sid, _, _ in deck],
             "sections": {"s1": {"description": "The app, the architecture, and the deployment",
                                 "start": "cover"},
                          "s2": {"description": "One request, end to end: the code, the proof, and the dashboards",
                                 "start": "guard"},
                          "s3": {"description": "The results and what the data changed", "start": "hypothesis"},
                          "s4": {"description": "Appendix, for questions only", "start": "a-questions-1"}},
             "faces": {"ibm-plex-sans": {"family": "IBM Plex Sans",
                                         "href": "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600"
                                                 "&display=swap"},
                       "jetbrains-mono": {"family": "JetBrains Mono",
                                          "href": "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400"
                                                  "&display=swap"}},
             "designSystems": []}
    (root / "project" / "deck.json").write_text(json.dumps(index, indent=1))
    return deck


def text_of(body: str) -> list[str]:
    """The visible text of a slide, one entry for each block (for the STE lint and the number check). Text in the
    mono face is code, and the lint text puts it in backticks."""
    body = re.sub(r"<br\s*/?>", " ", body)
    out = []
    for _, attrs, inner in re.findall(r"<(h1|h2|h3|p|td|th)\b([^>]*)>(.*?)</\1>", body, flags=re.S):
        text = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        if text:
            out.append(f"`{text}`" if "JetBrains Mono" in attrs else text)
    return out


def code_names(text: str) -> str:
    return re.sub(r"\b[\w.]+\(\)", lambda m: f"`{m.group(0)}`", text)


def check(deck: list[tuple[str, str, str]], root: Path) -> int:
    sources = "\n".join(f.read_text() for f in [ROOT / "DESIGN.md", ROOT / "docs" / "results.md",
                                                ROOT / "docs" / "budget-ledger.md", ROOT / "README.md",
                                                *sorted((ROOT / "docs" / "spec").glob("*.md")),
                                                # the raw scrapes and the hop record that the slides quote
                                                *sorted((ROOT / "metrics" / "one-20261002T050552Z").glob("scrape-*")),
                                                ROOT / "metrics" / "e3-c-150" / "hops.jsonl"])
    lint, missing, total_words = [], [], 0
    print(f"{'slide':16s} {'words':>5s} {'seconds':>7s}")
    for sid, body, notes in deck:
        blocks = text_of(body)
        lint.append(f"## {sid}\n\n" + "\n\n".join(b if b.startswith("`") else code_names(b) for b in blocks)
                    + "\n\nNotes:\n\n" + code_names(paragraphs(notes)) + "\n")
        words = len(notes.split())
        if not sid.startswith("a-"):
            total_words += words
            print(f"{sid:16s} {words:5d} {words / 130 * 60:7.0f}")
        for b in blocks + [notes]:
            for num in re.findall(r"\d[\d,]*(?:\.\d+)?", b):
                bare = num.rstrip(",")
                if len(bare.replace(",", "").replace(".", "")) < 2:
                    continue
                if bare not in sources and bare.replace(",", "") not in sources:
                    missing.append((sid, bare))
    print(f"{'main slides':16s} {total_words:5d} {total_words / 130 * 60:7.0f}  (+ 61 s of clips)")
    jargon = sorted({(sid, m.group(0)) for sid, body, notes in deck for b in text_of(body) + [notes]
                     for m in JARGON.finditer(b)})
    if jargon:
        print("INTERNAL LABELS on the slides (not for the audience):", jargon)

    (root / "deck-text.md").write_text("# Deck text\n\n" + "\n".join(lint))
    if missing:
        print("numbers not found in the report:", sorted(set(missing)))
    return 1 if jargon or missing else 0


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "deck"
    deck = write(root)
    return check(deck, root)


if __name__ == "__main__":
    raise SystemExit(main())
