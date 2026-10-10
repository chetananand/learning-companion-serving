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
    "e3": "/_blob/c52a5f28b9379587ae1de638b1032b28", "e9": "/_blob/33d6ecab663862b797ffccf742537e60",
    "hop": "/_blob/bef7f263ef04880cd591661ca8dedf8c", "warm": "/_blob/f6633cb512f077a6b00cb0fbdf1a49ed",
    "arch": "/_blob/ea2cc8cd93f9a3412a5f996150e1a655",
    "cluster": "/_blob/c93f5728251ce5c26c873f9fc0da2440", "guard": "/_blob/51874d3a3f4d12ee3c96b0a08ca10aeb",
    "tenant": "/_blob/4466d622d255b88d350409463917aaca", "lmcache": "/_blob/bd6b9524acd3f31f149f04f6244c30d7",
    "queues": "/_blob/8bf5df6c9b3ea7d826771b4f749137af", "pd": "/_blob/64db200fb2311fad8d21915521ec3dc8",
    "overflow": "/_blob/88f1f4655c6ae3a220f8d19ddec374ae", "tokens": "/_blob/7dec7da661854b29c57e90efb61cfe72",
    "replicas": "/_blob/555b51655b33e9258f9abf12f0f93b21",
    "itl_pd": "/_blob/b83b952f9637f6cb551a1f4313858ac6", "itl_co": "/_blob/561ac7a8d501112c738c5a67773cb4e2",
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
    "itl_pd": "plots/slides/panels/vllm-itl-pd-100.png", "itl_co": "plots/slides/panels/vllm-itl-colocated-100.png",
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


# The deck order. slides() builds the slides in any order, and then sorts them by these lists.
MAIN = ["cover", "intro", "arch", "models", "search", "capacity", "deploy", "design", "app", "guard", "admit",
        "place", "hop", "warm", "scale", "hypothesis", "topology", "latency", "ttft-points", "questions-1",
        "questions-2", "changed"]
APPENDIX = ["a-place", "a-part5", "a-warm", "a-hop", "a-production", "a-scale", "a-bad", "a-scrape", "a-faults",
            "a-demo", "a-cost"]


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
          "llm-d decides where it goes. vLLM runs the model.", 32, DARK_SOFT),
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
         f'(edge, guard models, Envoy AI Gateway, llm-d), and the engine (prefill pod, decode pod, LMCache '
         f'server), steps 1 to 6" style="width:1700px;height:{round(1700 * SIZES["arch"][1] / SIZES["arch"][0])}px;'
         f'object-fit:contain;align-self:center">'),
    ]), "This is the path of one LLM call. Admission control decides if the call may enter, and routing decides "
        "where it goes. Each box says what it decides or does, and each arrow names what moves. 1: the app sends "
        "the request to edge, our code. 2: edge asks the guard models if the prompt is safe. 3: edge sends the "
        "request to the Envoy AI Gateway, which checks the token budget of the tenant. 4: Envoy sends the prompt "
        "to llm-d. The llm-d box shows two jobs. Its flow control is the last admit check: it holds the call in a"
        " queue while the pods are full. Then its scheduler picks the pods, and llm-d sends back only the pod "
        "addresses. 5: Envoy sends the request to the decode pod. Each request enters this pod through the llm-d "
        "routing sidecar, a small proxy next to vLLM. The sidecar runs the steps, because llm-d only decides. For"
        " most calls, llm-d picks no prefill pod, and the sidecar passes the request straight to vLLM. The orange"
        " steps occur only when llm-d also picked a prefill pod. 5a: the sidecar sends the prompt to the prefill "
        "pod and asks for only one token, so that pod only computes the KV. 5b: vLLM there sends a copy of the KV"
        " to the LMCache server. 5c: the store barrier replies only after the store. 6: the sidecar sends the "
        "request to its own vLLM, which loads the stored KV and generates the answer. The sidecar never moves the"
        " KV itself. In our mode, the KV goes through the LMCache server."))

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
        callout("Why Gemma 4 31B", "A tested FP8 checkpoint that fits one H100. It passed all 7 of our gate "
                "tests. They test tool calls (39 of 40, so 97.5%), TTFT, decode speed, the prefix cache, a P/D "
                "split, and the CPU tier. We dropped Qwen3.8-27B for its open vLLM bugs in the prefix cache."),
    ]), "These are the models. One LLM, Gemma 4 31B in FP8, runs all agent steps on vLLM. It fits one H100, and "
        "it passed all 7 of our gate tests on 2026-09-29. Tool calls from our agent: 39 of 40 correct, so 97.5%. "
        "The TTFT of one 8K prompt: 0.90 seconds. The time between tokens with 8 calls at the same time: 20.5 "
        "milliseconds at p95. The prefix cache with 2,000 calls: no bad answer, and a hit ratio of 84.5%. One P/D"
        " split call: the decode pod loaded 8,448 of 8,500 prompt tokens. The CPU tier: a prefix came back from "
        "LMCache, also after a pod restart. The rule of the gate: the challenger, Muse Glimmer 30B, replaces "
        "Gemma 4 only if Gemma 4 fails a test that the challenger passes. Gemma 4 failed none. We dropped "
        "Qwen3.8-27B for its open vLLM bugs in the prefix cache. Small models do the rest: two guard models, and "
        "the search and OCR models in SIE."))

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

    s.append(("capacity", "".join([
        title("KV on paper: bytes for each token, and how many sequences fit"),
        two_columns([
            p("Gemma 4 31B, BF16 KV. Full-attention layers: 40,960 bytes for each token. Sliding-window layers: "
              "819,200 bytes for each token, but only for the last 1,024 tokens, so 800 MiB for each sequence.", 28,
              INK),
            (f'<div style="background:{CODE_BG};border-radius:12px;padding:12px 20px">'
             f'<p style="font-family:{MONO};font-size:24px;line-height:1.35;color:{INK}">max seqs = (HBM - weights - '
             f'activations) / KV of one sequence</p></div>'),
            table(["Length", "KV of one sequence", "Max seqs, BF16 KV", "Max seqs, FP8 KV"],
                  [["5,121 tokens: what the app sends (median)", "0.98 GiB", "36", "72"],
                   ["8K tokens: the length that we planned", "1.09 GiB", "32", "64"],
                   ["32K tokens: max_len", "2.03 GiB", "17", "34"]], [40, 22, 19, 19]),
            p("One H100, the KV budget after the weights: 35.3 GiB.", 24),
        ], [
            measured("The KV cache that vLLM gave the decode pod (one H100)."),
            proof("173,657 tokens", "with BF16 KV, and 345,235 tokens with FP8 KV"),
            callout("Model switch", "We kept Gemma 4 31B, because it passed all 7 gate tests. With Muse Glimmer 30B, "
                    "the KV of an 8K sequence is 83% smaller, and 189 sequences fit at 8K, not 32. We changed the KV "
                    "to FP8 in a test instead: it halves the bytes for each token. The base runs used BF16 KV."),
            p("Later in the talk: which limiter came first, and if our guess was right.", 24, MUTED),
        ], left_w=860),
    ]), "Before the cluster, we did the KV math. Gemma 4 31B has two kinds of KV. The full-attention layers need "
        "40,960 bytes for each token. The sliding-window layers add 800 MiB for each sequence, after 1,024 "
        "tokens. At the length that our app sends, about 5,000 tokens, one H100 fits 36 sequences, and 17 at the "
        "32K max_len. FP8 KV doubles both. We tested FP8 KV in one arm, but the base runs used BF16 KV. We kept "
        "the model, because it passed all gate tests. With Muse Glimmer, the KV of an 8K sequence is 83% smaller."))

    s.append(("deploy", "".join([
        title("The deployment: two nodes, and an A100 fallback"),
        two_columns([
            callout("Node 1 · 2 × H100 SXM 80 GB · the engine",
                    "vllm-prefill on GPU 0 and vllm-decode on GPU 1, each on a full GPU. The LMCache server: up to "
                    "250 GiB of the 450 GiB of CPU RAM."),
            callout("Node 2 · 1 × H100 80 GB · control and data",
                    "HAMi slices of one GPU for the SIE and guard models. edge, guard, Envoy, llm-d, the app, "
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
        "We sent 200 chat turns, 22 of them attacks. The guard blocked all 22 attacks and no normal turn. Edge "
        "keeps each verdict in Redis for 1 hour. The key is a hash of the last user message. So the next LLM "
        "calls of the same turn do not call the guard models again. Edge never stores an outage. If Redis fails, "
        "edge calls the guard models. Stop two is stay or leave. should_leave() keeps 429, 500, and slice_oom on "
        "our cluster. Only an interactive capacity refusal may leave. At 150% load the gate let only the 124 "
        "interactive 503 calls go, and no 429. Redis also holds the limits of the overflow API. The overflow was "
        "off in all runs, so these calls got a 503."))

    s.append(("admit", "".join([
        strip(["admit"]),
        title("Admit: we refuse work at the door, not in the engine"),
        two_columns([
            code([("control/router/policy.yaml", "tenants, flow_control"), ("control/edge/admit.py:23", "slice_oom")]),
            p("Each tenant has a token budget for each minute. While the pods are full (5 queued requests or 90% KV "
              "use), the llm-d flow control holds the calls.", 28),
            measured("In all load tests, from 50% to 150% load: the preemptions in the vLLM engine."),
            proof("0 preemptions", "Before the KV was full, flow control refused the extra work."),
            measured("Tenant test: one noisy tenant over its token budget, at 100% load."),
            p("55 calls of the noisy tenant got 429 tenant_tokens. No other tenant got a 429.", 28, INK,
              600),
        ], [
            image("tenant", "Grafana panel: tenant rate-limit rejects, tenant_tokens",
                  "Dashboard 3 · Gateway + admission: the 429 tenant_tokens rejects each second in the tenant "
                  "test", 896),
            callout("Redis and the queue", "Redis holds the tenant counts. The queue is in the flow control of llm-d. "
                    "Flow control is the admit part of llm-d."),
        ]),
    ]), "Stop three is admit. The policy file holds our numbers. The Envoy AI Gateway counts the tokens and the "
        "requests of each tenant in each minute. Its rate-limit service keeps these counts in Redis. When a "
        "tenant is over its budget, it gets a 429 at once, with no wait. Redis is not a queue. It has four jobs: "
        "the tenant counts, the guard verdicts, the overflow limits, and the web search results of the app. The "
        "queue is in the flow control of llm-d, in its own memory. Flow control is the admit part of llm-d: it "
        "decides if and when a call goes to a pod. Then the scheduler of llm-d decides which pod, and the place "
        "slide shows it. The queue has two priority bands with a time limit: 10 seconds for an interactive call, "
        "and 120 seconds for a batch call. A pod is full at 5 queued requests or 90% KV use. The flow control "
        "holds the calls when the average fullness of the pods reaches full, so one pod can have more queued "
        "requests. This is how the decode queue reached 50 at 150% load. Then the flow control holds the calls in"
        " the queue. In all load tests the engine preempted nothing, because the flow control refused the extra "
        "work first. In the tenant test the noisy tenant got 55 429 replies, and no other tenant got one. But the"
        " TTFT p95 of the others stayed near 10 seconds, because at 100% load the P/D layout is above the limit "
        "of the engine."))

    s.append(("place", "".join([
        strip(["place"]),
        title("Place: prefix match first, then load"),
        two_columns([
            code([("control/router/policy.yaml", "weights"), ("control/router/render.py:50", "render_epp_config()")]),
            measured("Stale-metrics test: one of two pods sends a frozen copy of its metrics from an empty moment, "
                     "at 100% load. We measured its share of the work."),
            proof("80% of the work", "went to that pod. With real metrics, it got 51%."),
            callout("What the data changed",
                    "We cleared the prefix cache of one pod in a run. The llm-d scheduler did not act on the "
                    "clear, and all 15 warm sessions went to that pod again and missed the cache once."),
        ], [
            image("pd", "Grafana panel: P/D decisions, decode-only and prefill-decode",
                  "Dashboard 4 · Router: the decision for each call at 150% load: decode only, or split", 680),
            image("queues", "Grafana panel: queue depth for each pod",
                  "Dashboard 5 · Queue depth by pod: decode up to 47, prefill up to 2", 680),
        ]),
    ]), "Stop four is place. The llm-d scheduler scores each pod: the prefix match counts most, then the session,"
        " the queue depth, the KV use, and the ramp. A pod with metrics older than 2 seconds counts as full. In "
        "the stale-metrics test, a frozen copy of the metrics of an empty pod pulled 80% of the work to that pod."
        " And after a cache clear, llm-d still sent the warm sessions to the cleared pod."))

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
            p("Why not Mooncake: no Gemma 4 test, and no RDMA on our Lambda nodes.", 24, INK),
        ]),
    ]), "Stop five is the hop. The llm-d scheduler splits a request only when 2,048 or more of its tokens are not"
        " in a cache. The sidecar then sends the prompt to the prefill pod and asks for only one output token. "
        "There is no vLLM request for a prefill only, and one token is the smallest request. The pass that "
        "computes the KV of the prompt also gives this token, so it costs almost nothing. The decode pod does not"
        " use this token: it writes the whole answer itself. Who does what in the hop: the llm-d scheduler "
        "decides if a call hops, with the P/D decider, and it picks the prefill pod. The llm-d routing sidecar in"
        " the decode pod runs the steps: first the prompt to the prefill pod, then the request to its own vLLM. "
        "The LMCache connector inside each vLLM moves the KV bytes: the prefill vLLM stores a copy of the KV, and"
        " the decode vLLM loads it. The LMCache server holds the copy in CPU RAM on the node. Our store barrier "
        "in the prefill pod waits until LMCache has stored the copy. Our hop script records each hop from the "
        "Envoy log. The connector moves the bytes through CUDA IPC, so this hop works only inside one node. Why "
        "LMCache and not Mooncake? We decided on 2026-09-27, for four reasons. First, Gemma 4 has full-attention "
        "layers and sliding-window layers, so vLLM keeps two kinds of KV. The LMCache connector supports this, "
        "and LMCache tested Gemma 4 31B with it. Mooncake had no Gemma 4 test. Second, Mooncake uses RDMA by "
        "default, and our Lambda nodes have no RDMA. Over TCP, a KV load can be slower than a new prefill: NIXL "
        "over TCP took about 4 seconds. Third, Mooncake Store needs a master service and its own config. LMCache "
        "is one server on each GPU node, and all pods on the node share it. Fourth, we had no time for a Mooncake"
        " test. LMCache had a cost: it keeps the sliding-window layers in full. That is about 865 KB for each "
        "token, against about 237 KB in the GPU cache, so we raised its cap to 250 GiB. In production, with RDMA "
        "between nodes, Mooncake or NIXL is the right medium for a hop across nodes. LMCache can also use "
        "Mooncake Store as a backend. The barrier finds the prefill request by its shape: one token and no "
        "stream. In the hop test, the first token came in 0.52 to 0.78 seconds, against about 4 seconds for NIXL "
        "over TCP. If someone asks if this is production quality: the one-token request is, and the barrier is "
        "not. Under load on the A100 node, the barrier hit its half-second cap on 72% to 99% of split calls. A "
        "production hop needs a store signal for each request, RDMA between nodes, and two or more pods in each "
        "pool."))

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
    ]), "Stop six is declare warm. A pod with its weights on the GPU is not warm yet. The warm controller sends "
        "our system prompts and the shapes of our app. A new decode pod also gets one split call through the "
        "prefill pod, so the hop path is warm too. Then a 4,000-token probe runs. The pod gets the warm label "
        "only if the probe is fast enough. Then it gets 10% of the traffic weight, and more while the TTFT holds."
        " In the restart test, the warmup cut the first-minute p95 from 10.9 to 7.3 seconds. In minutes 2 to 4, "
        "both arms had a p95 near 12.7 seconds, so the warmup helps only the first minute. The ramp cut it from "
        "57.3 to 14.6 seconds."))

    s.append(("hypothesis", "".join([
        title("The decode pod was the limit, not prefill compute"),
        two_columns([
            table(["What we expected first", "Result"], [
                ["RAG: prefill compute", "Partly. Only for long prompts."],
                ["Agents: KV blocks", "Partly. The llm-d flow control held KV at 90%."],
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
        "fewer than 2,048 new tokens. The llm-d scheduler does not split them, so the decode pod runs their "
        "prefill too. At 100% load it processed 16,200 prompt tokens each second, and the prefill pod 4,550."))

    s.append(("topology", "".join([
        title("For our traffic, two colocated replicas beat a P/D split"),
        two_columns([
            measured("The same recorded app traffic at three loads, on two layouts: one prefill pod and one decode "
                     "pod (P/D), or two colocated replicas. The TTFT p50 of the interactive calls."),
            proof("0.84 s against 4.59 s", "at 100% load on the H100: two colocated replicas against P/D. The A100 "
                  "tests agree."),
            p("Why: only 10% to 13% of the calls split, so the decode pod runs the prefill of most prompts.", 28),
            callout("What the data changed",
                    "We keep the split for long uncached prompts. A P/D layout needs two or more pods in each "
                    "pool, or one restart stops all split calls."),
        ], [
            image("e3", "Chart: TTFT p50 of P/D against two colocated replicas, on the H100 and on the A100",
                  "The TTFT p50 of the interactive calls, by layout and load", 960),
        ], left_w=640),
    ]), "This test sends the same recorded app traffic to two layouts, at three loads. A colocated replica is one"
        " vLLM pod that does the prefill and the decode of its calls. Two colocated replicas with prefix routing "
        "beat one prefill pod and one decode pod at each load, on the H100 and on the A100. At 100% load the TTFT"
        " p50 was 0.84 seconds, against 4.59. The split still helps a long uncached prompt. But a P/D layout "
        "needs two pods in each pool: when the one prefill engine restarted, 60 split calls got no endpoint."))

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

    s.append(("latency", "".join([
        title("TTFT and ITL: P/D missed both SLOs at each load"),
        two_columns([
            measured("The same recorded app traffic on the two layouts: P/D, and two colocated replicas. TTFT: what "
                     "the app saw, for the streaming calls (the answers). ITL: the p95 of each minute on the decode "
                     "pod, the median over the run."),
            table(["Load", "TTFT p95, P/D", "TTFT p95, colocated", "ITL p95, P/D", "ITL p95, colocated"], [
                ["50%", "2.04 s", "1.05 s", "84 ms", "36 ms"],
                ["100%", "15.95 s", "1.57 s", "192 ms", "50 ms"],
                ["150%", "21.45 s", "21.10 s", "206 ms", "129 ms"],
                ["SLO", "1.5 s", "1.5 s", "50 ms", "50 ms"],
            ], [16, 21, 21, 21, 21], size=22),
            p("One call at a time, in the gate test: TTFT 0.90 s, and ITL p95 20.5 ms.", 22),
            p("TPOT at the client, the p50 of the calls: P/D 32 to 51 ms, colocated 25 to 41 ms. The "
              "engine has the same means.", 22),
            callout("Why the ITL of P/D is high", "Most calls do not split, so the decode pod also computes their "
                    "prompts. These prefill chunks share each step with the decode streams."),
        ], [
            image("itl_pd", "Grafana panel: ITL p95 for each pod, P/D at 100% load",
                  "Dashboard 6 · vLLM: ITL p95, P/D at 100% load. About 200 ms most of the time.", 760),
            image("itl_co", "Grafana panel: ITL p95 for each pod, two colocated replicas at 100% load",
                  "Dashboard 6 · vLLM: ITL p95, two colocated replicas at 100% load. Near 50 ms most of the "
                  "time, with bursts.", 760),
        ], left_w=820),
    ]), "These are the latency numbers that a user feels. TTFT is the time to the first token. ITL is the time "
        "between two tokens of an answer. Our goal for the TTFT is a p95 of at most 1.5 seconds, for prompts up "
        "to 8K tokens. Our goal for the ITL is a p95 of at most 50 milliseconds. One call at a time, the gate "
        "test gave a TTFT of 0.90 seconds and an ITL p95 of 20.5 milliseconds. So the engine can meet both. Under"
        " load, P/D missed both SLOs at each load. At 50% load, its TTFT p95 was 2.04 seconds, and its ITL p95 "
        "was 84 milliseconds. The two colocated replicas met both SLOs at 50% load: 1.05 seconds and 36 "
        "milliseconds. At 100% load, they were a little above the TTFT limit, at 1.57 seconds, and at the ITL "
        "limit, 50 milliseconds. At 150% load, both layouts missed both SLOs. Why is the ITL of P/D high? Most "
        "calls do not split, so the decode pod also computes their prompts. Each step of the decode pod then "
        "carries prefill chunks next to the decode streams, and each stream waits longer for its next token. The "
        "split test showed the other side: a split cut the ITL of the other streams from 0.24 to 0.07 seconds for"
        " an 8K prompt. The TTFT that the app saw also includes the wait in our queue. We measure the ITL at the "
        "engine. A check at the client agrees. The TPOT is the time per output token of one streaming call. It is"
        " the time from the first token to the last token, divided by the output tokens minus 1. ITL is each gap "
        "between two tokens. TPOT is the mean gap of one call. The engine mean is the running sequences of the "
        "decode pod, divided by its generated tokens each second. The TPOT p50 of the calls, against the engine "
        "mean. P/D at 50% load: 32 milliseconds at the client, and 31 in the engine. Colocated at 50%: 25 and 28."
        " P/D at 100%: 48 and 52. Colocated at 100%: 38 and 38. P/D at 150%: 51 and 54. Colocated at 150%: 41 and"
        " 51. At this load, the engine value also counts the calls that are still in prefill. So edge and Envoy "
        "add no time between tokens. A mean is always lower than the p95, because the p95 catches the slow steps."
        " So P/D has a mean near 50 milliseconds, but a p95 near 200. Over the calls, the TPOT p95 was 33 to 63 "
        "milliseconds. Dashboard 6 has these panels: TTFT p95 and ITL p95 for each pod."))

    s.append(("ttft-points", "".join([
        title("TTFT at three points: under load, calls wait before the engine"),
        two_columns([
            table(["Layout and load", "Client", "Gateway", "Engine", "llm-d queue"], [
                ["P/D, 50%", "2.04 s", "1.87 s", "0.80 s", "0.00 s"],
                ["Colocated, 50%", "1.05 s", "0.94 s", "0.72 s", "0.00 s"],
                ["P/D, 100%", "15.95 s", "15.67 s", "4.38 s", "2.25 s"],
                ["Colocated, 100%", "1.57 s", "1.35 s", "0.90 s", "0.00 s"],
                ["P/D, 150%", "21.45 s", "21.34 s", "16.90 s", "9.53 s"],
                ["Colocated, 150%", "21.10 s", "20.79 s", "9.60 s", "8.28 s"],
            ], [28, 18, 18, 18, 18], size=22),
            p("All values are p95. Client and gateway: the same streaming calls. Engine and queue: the p95 of each "
              "minute, the median over the run.", 22),
        ], [
            measured("Client: the load generator, to the first answer token. Gateway: edge, to the first byte. "
                     "Engine: vLLM on the decode pod. Queue: the interactive band in llm-d."),
            callout("What the gaps mean", "Client to gateway: 0.1 to 0.3 s, the relay. Gateway to engine: the "
                    "guard, the llm-d queue, and the hop. With P/D at 100% load, this gap was 11 s."),
            callout("On the dashboards", "Dashboard 6: the engine TTFT for each pod. Dashboard 3: the edge TTFT, "
                    "but of all calls, so a call that does not stream counts its full answer. The client TTFT is in "
                    "the load test logs."),
        ], left_w=860),
    ]), "This slide shows where the TTFT comes from. We measured it at three points. The client is the load "
        "generator, in the place of our app. It times each streaming call to its first answer token. The gateway "
        "is edge, and it times the same calls to their first byte. The engine is vLLM on the decode pod. The "
        "client and the gateway differ by only 0.1 to 0.3 seconds at p95: the relay, and the first answer token "
        "after the first byte. The big gap is between the gateway and the engine. It holds the guard, the wait in"
        " the llm-d queue, and the hop. With P/D at 100% load, the gateway saw 15.67 seconds at p95, and the "
        "engine only 4.38. The llm-d queue alone held interactive calls up to 2.25 seconds at p95. With two "
        "colocated replicas at the same load, the queue held nothing, and the gateway saw 1.35 seconds. So when "
        "the pods are full, calls wait before the engine, in our admit queue, and not in vLLM. On the dashboards,"
        " dashboard 6 shows the engine TTFT for each pod. Dashboard 3 shows the edge TTFT, but for all calls. A "
        "call that does not stream counts its full answer there, so that panel reads higher. The client TTFT is "
        "in the load test logs and in the results file."))

    cards = [("Colocated replicas", "for our traffic. Split only long uncached prompts."),
             ("A store barrier", "for the hop: completion is not visibility."),
             ("A cap for the ramp", "not only a score."),
             ("GPU values", "from the GPU: the planner capacity and the warm baseline."),
             ("Apply a cache clear", "in llm-d, or each warm session misses the cache once.")]
    card_html = "".join(
        f'<div style="flex:1;background:#2A3039;border-radius:14px;padding:18px 20px;display:flex;'
        f'flex-direction:column;gap:6px">{p(a, 28, DARK_BLUE, 600)}{p(b, 24, DARK_SOFT)}</div>' for a, b in cards)
    s.append(("changed", "".join([
        title("What the data changed in our design", DARK_TEXT),
        f'<div style="display:flex;flex-direction:row;gap:16px">{card_html}</div>',
        p("10 times the traffic: more decode capacity first, 32 sequences and FP8 KV, and more RAM for LMCache. The "
          "wrong knobs: more prefill pods, longer queues, and a lower split threshold.", 28, DARK_TEXT),
        p("GPU cost: 237 USD of the 400 USD credit. The repo has the report, the notebooks, and each run.", 28,
          DARK_SOFT),
        '<div style="flex:1"></div>',
        (f'<div style="display:flex;flex-direction:row;justify-content:space-between;align-items:baseline">'
         f'{p("Questions?", 56, DARK_ORANGE, 600)}{p(REPO, 32, DARK_BLUE)}</div>'),
    ]), "Five things changed in the design because of the data. Two colocated replicas for our traffic. A store "
        "barrier for the hop. A cap for the ramp. Values like the planner capacity must come from the GPU. And "
        "llm-d must apply a cache clear. At 10 times the traffic I add decode capacity first, with 32 sequences, "
        "FP8 KV, and more CPU RAM for the LMCache server. The wrong knobs are more prefill pods, longer queues, "
        "and a lower split threshold. The GPU time cost 237 dollars. Thank you."))

    # ----------------------------------------------------------------------------------------------------------
    # The appendix (for questions only).
    num = {sid: n for n, sid in enumerate(MAIN, 1)}  # the main slide numbers, for the references below
    design = [
        ["GPU", "H100 SXM 80 GB. Each vLLM pod gets a full GPU.",
         "The smallest GPU with FP8 compute that meets the TTFT goal. An A6000 leaves 6.8 GiB for KV.",
         "TTFT on paper at 8K: 0.62 s, against 3.95 s (A100) and 7.96 s (A6000)."],
        ["Model", "Gemma 4 31B FP8. Weights: 30.4 GiB. KV at 5,121 tokens: 0.98 GiB.",
         "It passed all 7 gate tests, with 97.5% correct tool calls.", f"The KV math (slide {num['capacity']})"],
        ["Topology", "1 prefill pod and 1 decode pod. A call splits only at 2,048 or more uncached tokens.",
         "Short or cached calls stay on the decode pod, with no hop. Each pool has its own scale signal.",
         f"For our traffic, two colocated replicas were faster (slide {num['topology']})."],
        ["Slices", "None for vLLM. HAMi slices one GPU for SIE and the guard models.",
         "The KV needs all the free HBM. The small models need 52 GB in total.",
         f"The deployment (slide {num['deploy']})"],
        ["Concurrency", "Decode: 24 sequences. Prefill: 8. Max length: 32,768 tokens.",
         "At 8K, 32 sequences fit, and at 24K, about 20. At 24, the time between tokens stays near 20 ms.",
         "With 32 sequences, the TTFT p50 fell from 4.59 s to 2.10 s."],
        ["Hop backend", "The LMCache server (our name: lmcache), with our store barrier",
         "Gemma 4 runs on it. Mooncake: no Gemma 4 test, no RDMA on our nodes. NIXL over TCP: 4.1 to 4.3 s.",
         f"Hop TTFT: 0.52 to 0.78 s (slide {num['hop']})"],
        ["Overflow", "qwen3.8-27b on the Superlinked API, only for an interactive 503 or 529",
         "Quality near ours, the same API, and a Redis limiter on the cost. It was off in all runs.",
         "The gate marked the 124 interactive timeout calls."],
        ["Two boxes, two workers", "Gateway: edge, the Envoy AI Gateway, and llm\u2011d. Engine: two vLLM pods.",
         "The handout puts admit, place, and the queue in the gateway, and vLLM in the engine.",
         f"The architecture (slide {num['arch']})"],
        ["Scale", "KEDA, for each pool. Prefill: uncached prefill tokens. Decode: running sequences.",
         "Each pool grows on its own signal, so only the hot pool grows.",
         f"A new decode pod 15 s after the request (slide {num['scale']})"],
    ]
    s.append(("design", "".join([
        title("The cluster design: each choice, its reason, and the proof"),
        table(["Item", "Our choice", "Why", "Proof"], design, [11, 29, 35, 25], size=22),
    ]), "Each row gives a choice of the cluster design, the reason, and the proof. GPU: why not a cheaper GPU? An"
        " A6000 holds the weights, but it leaves only 6.8 GiB for KV, and it has no FP8 compute. An A100 has no "
        "FP8 compute either, so the prefill of 8K tokens takes 3.95 seconds on paper. The H100 SXM is the "
        "smallest GPU that meets the TTFT goal, and it gives two GPUs with NVLink on one node. On 2026-10-01, no "
        "H100 had stock, so one A100 node ran the tests. Model: we kept Gemma 4 31B, because it passed all 7 gate"
        " tests. They test tool calls, TTFT, decode speed, the prefix cache, a P/D split, and the CPU tier, also "
        "after a restart. The challenger replaces it only if Gemma 4 fails a test that the challenger passes. "
        "Topology: why a split, when two colocated replicas were faster? On paper, the split keeps long prompts "
        "away from the decode steps, and each pool gets its own scale signal. The data showed that most agent "
        "calls are short, so for our traffic two colocated replicas win. Slices: vLLM gets full GPUs, because the"
        " KV needs all the free HBM. The course notes also say: do not split prefill and decode on one sliced "
        "GPU. Concurrency: why 24? At 8K tokens, 32 sequences fit, and at 24K tokens, about 20 fit. We chose 24, "
        "between them. The data showed that 32 is better, and no pod preempted. Hop: why not Mooncake? The "
        "handout names it, but it had no Gemma 4 test, and our Lambda nodes had no RDMA. NIXL over TCP took more "
        "than 4 seconds. The LMCache server gave a hop TTFT of 0.52 to 0.78 seconds. Overflow: only an "
        "interactive call that llm-d refused for capacity may leave. A 429 never leaves. The overflow was off in "
        "all runs, so these calls got a 503. Two boxes: the handout puts admit, place, and the queue in the "
        "gateway. So llm-d, with its flow control and its scheduler, is in the gateway box, and vLLM is the "
        "engine box. Scale: we scale the pool that is the limit. Uncached prefill tokens grow the prefill pool, "
        "and running sequences grow the decode pool."))

    s.append(("a-place", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("How llm-d places a call: the policy and the scorers"),
        two_columns([
            code([("control/router/policy.yaml:15", "policy: prefix_then_load"),
                  ("control/router/policy.yaml:36", "weights: decode, prefill"),
                  ("control/router/render.py:50", "render_epp_config()")]),
            table(["Scorer", "Decode", "Prefill", "What it scores"], [
                ["prefix match", "3", "3", "the part of the prompt in the KV cache of the pod"],
                ["session", "2", "-", "the pod that served the session before"],
                ["queue depth", "2", "2", "the requests that wait on the pod"],
                ["KV use", "2", "-", "the KV cache use of the pod"],
                ["token load", "-", "2", "the load of the pod in tokens"],
                ["ramp", "2", "2", "a new pod gets 10%, 25%, 50%, then 100%"],
            ], [22, 11, 11, 56], size=22),
            p("Filters first: only warm pods, and only pods with the role of the profile. Then the picker takes "
              "the pod with the highest total score.", 22),
        ], [
            callout("The policy", "All runs used prefix_then_load. The other choices are least_loaded and random. "
                    "The llm-d scheduler has no p2c picker. With two pods in a pool, p2c is the same as least "
                    "loaded."),
            callout("Queue depth: a scorer and an admit input", "The queue scorer is in both profiles. The flow "
                    "control also reads the queue depth: at 5 queued requests, a pod is full."),
            measured("Stale-metrics test: one pod sends a frozen copy of its metrics from an empty moment."),
            proof("80% of the work", "went to that pod. With real metrics, it got 51%."),
        ], left_w=900),
    ]), "For questions only. This slide shows how llm-d places a call. Two different things in llm-d answer two "
        "different questions. The queue order of the flow control answers: which waiting call goes next, and "
        "when? Interactive calls go before batch calls, tenants take turns, and the first in goes out first. It "
        "works only while the pods are full and calls wait. The placement policy of the scheduler answers: to "
        "which pod does the call go? It is prefix_then_load, and it works for each call when the call leaves the "
        "queue. We set both in the policy file. They run one after the other: the queue order picks the next "
        "call, and then the scheduler picks its pod. When the pods are not full, no call waits, and only the "
        "scheduler works. The policy file holds our numbers, and render.py turns them into the llm-d "
        "configuration. There are two scheduling profiles: one for the decode pool and one for the prefill pool. "
        "The decode profile runs first. The prefill profile runs only when llm-d splits the call. Each profile "
        "first filters the pods: only warm pods, and only pods with its role. Then each scorer gives each pod a "
        "score, and the picker takes the pod with the highest weighted total. Load means how busy each pod is "
        "now, from the metrics of each vLLM pod. Queue depth gives 1 to the pod with the shortest queue and 0 to "
        "the pod with the longest queue. KV use gives 1 minus the KV use of the pod. Token load gives 1 minus the"
        " tokens in flight, divided by a limit. Prefix match has weight 3, and each load scorer has weight 2. For"
        " example, pod A has the prompt in its KV cache, but it has the longest queue and 85% KV use: 3.3 points."
        " Pod B has no prefix match, the shortest queue, and 30% KV use: 3.4 points. So pod B gets the call: the "
        "load beat the prefix. Thus the name: prefix, then load. A return call of a session also gets the session"
        " score on its old pod. Prefix and session give up to 5 points, and the load at most 4. So a session "
        "almost always goes back to its pod, and only the flow control stops it when the pods are full. The two "
        "profiles use different scorers. The decode pod keeps the KV of the running sequences, and the next call "
        "of a session can use it again. So the decode profile scores the session and the KV use. The prefill "
        "profile scores the token load, because a prefill costs compute for each new token. But the token-load "
        "scorer used the default limit of 4,194,304 tokens. Even our prefill test reached only about 14% of it. "
        "So this score stayed high, and queue depth was the real load signal for prefill. Queue depth is a scorer"
        " in both profiles, and it is also an admit input. The flow control counts a pod with 5 queued requests "
        "as full. Why not p2c? The llm-d scheduler has no p2c picker. With two pods in a pool, p2c compares both "
        "pods, so it is the same as least loaded. The stale-metrics test shows that the load scores matter. A "
        "frozen copy of the metrics of an empty pod pulled 80% of the work to that pod."))

    q1 = [["What is the app? Which tokens are common, and which are unique?",
           "RAG and agent steps over the bookmarks. Shared: system prompt, tools, session history. Unique: question, "
           "chunks, pages.",
           "metrics/cap-b1/envoy-access.log: an agent step finds 64% of its prompt in the cache."],
          ["What dies at the guard, admit, place, and queue?",
           "Guard: unsafe prompts (400). Admit: a tenant over its budget (429). Queue: a wait past the time limit "
           "(503). Place: no ready pod (503).",
           "docs/results.md: 22 of 22 attacks, 55 tenant 429 replies, 273 timeouts at 150% load."],
          ["Where do I prevent work that will time out?",
           "In the llm-d queue: 10 s for interactive and 120 s for batch. Edge sets each call to half of its time "
           "left.",
           "control/router/policy.yaml:46 to :48, and the timeout_queue panel of dashboard 3"],
          ["Where do I protect KV?",
           "The llm-d flow control holds calls while the pods are full, so the KV stays near 90% or below. vLLM "
           "preemption is the last line.",
           "control/router/policy.yaml:55, and 0 preemptions in the raw scrape of a live engine"],
          ["Where do I prioritize interactive traffic?",
           "Interactive goes before batch in the llm-d queue. Batch waits from 70% fullness, interactive only at "
           "100%.",
           "control/router/policy.yaml:46 to :54. At 100% load: 89 batch and 11 interactive sheds."],
          ["Where do I limit one tenant, so that it cannot take the GPU?",
           "The Envoy AI Gateway counts the tokens and requests of each tenant in Redis. Over the budget: a 429 that "
           "never leaves.",
           "control/router/policy.yaml:61. 55 429 replies for the noisy tenant, none for the others."],
          ["Where do I hop, and what is not copied?",
           "llm-d splits a call at 2,048 or more uncached tokens. The decode pod computes again only the tokens "
           "after the last full chunk of 256.",
           "hops.jsonl of each P/D run: 2,304 of 2,371 tokens came from the cache."]]
    q2 = [["Where do I evict, and what becomes a ghost if I skip it?",
           "vLLM and LMCache evict their oldest blocks. If llm-d misses a clear, the next call of each warm session "
           "misses: a ghost.",
           "metrics/e7b-*/kv-events.json and tools/ghost_probe.py: 15 of 15 sessions missed once."],
          ["Where does the engine scheduler sit, against our admit, place, and queue?",
           "Inside each vLLM pod, after our admit, place, and queue. We set its flags, and we do not change its "
           "code.",
           "cluster/manifests/base/engine/vllm-decode.yaml:34 to :38"],
          ["What limited concurrency on this GPU for this app?",
           "The decode pod. Most calls do not split, so it does their prefill and their decode, at 24 sequences.",
           "Dashboard 6: 16,200 prompt tokens each second on the decode pod, 4,550 on the prefill pod."],
          ["Four production alerts?",
           "Interactive TTFT budget burn, a shed rate above 5%, KV pressure (above 92%, or preemptions), and hop "
           "failures.",
           "cluster/manifests/base/monitoring/rules.yaml:25 to :40"],
          ["If I scale, which pool: prefill tokens or decode slots?",
           "Decode first, on running sequences against 60% of 24 slots. Prefill on uncached prefill tokens against "
           "70% of its capacity.",
           "rules.yaml:18 and :22. KEDA made a decode pod 15 s after the request."],
          ["What changes at 10 times the traffic?",
           "More decode capacity first: more pods, 32 sequences, and FP8 KV. More CPU RAM for LMCache.",
           "32 sequences: TTFT p50 from 4.59 s to 2.10 s. FP8 KV: twice the tokens."],
          ["Which three knobs are the wrong next move?",
           "More prefill pods, a longer queue, and a lower split threshold.",
           "The prefill pod was not the limit. A longer wait still misses the goal. Each split costs more."]]
    notes_q = {"questions-1": (
        "These are the questions of the handout. Each answer points at a file or a scrape in the repo. The "
        "app is a learning companion over the bookmarks of its owner, with RAG and agent steps on Gemma 4 "
        "31B. The shared tokens are the system prompt, the tool schemas, and the history of a session. The "
        "unique tokens are the question, the retrieved chunks, the fetched pages, and the OCR text. The Envoy"
        " logs of the capture runs give the numbers. An agent step finds 64% of its prompt in the cache, and "
        "31% of all prompt tokens were in the cache. What dies where: the guard stops an unsafe prompt with a"
        " 400, before any GPU work. Admit stops a tenant over its budget with a 429. The queue stops a call "
        "that waits past its time limit with a 503. Place gives a 503 when no pod is ready. Time limits: the "
        "llm-d queue has a time limit for each band. Edge sets the limit of each call to half of its time "
        "left. So a call that cannot finish in time leaves before it uses the GPU. KV: the llm-d flow control"
        " holds calls while the pods are full, so the KV of a pod stays near 90% or below. vLLM preemption is"
        " only the last line, and no run preempted. Priority: interactive calls go before batch calls in the "
        "llm-d queue. Batch calls already wait at 70% fullness, and interactive calls only at 100%. vLLM also"
        " schedules by priority. At 100% load, llm-d shed 89 batch calls and 11 interactive calls. One "
        "tenant: the Envoy AI Gateway counts the tokens and the requests of each tenant in Redis. A tenant "
        "over its budget gets a 429, and a 429 never leaves the cluster. In the tenant test, the noisy tenant"
        " got 55 429 replies, and no other tenant got one. But the TTFT p95 of the others stayed near 10 "
        "seconds, because 100% load in this layout is above the limit of the engine. The hop: llm-d splits a "
        "call at 2,048 or more uncached tokens. The decode pod loads the KV from the LMCache server. It "
        "computes again only the tokens after the last full chunk of 256 tokens."),
               "questions-2": (
        "Evict and ghosts: vLLM evicts blocks of its GPU prefix cache when it needs space. The LMCache server"
        " evicts its oldest chunks at 90% of its 250 GiB cap. llm-d learns of each eviction from the KV "
        "events of vLLM. A ghost is a prefix that llm-d still places on a pod after the pod cleared it. We "
        "cleared the prefix cache of one pod during a run. The pod sent one AllBlocksCleared event, and llm-d"
        " did not act on it. The next call of each warm session went to the cleared pod and missed the cache "
        "once, in 15 of 15 sessions. A ghost costs one prefill of the session history. The engine scheduler "
        "sits inside each vLLM pod, after our admit, place, and queue. The engine does continuous batching, "
        "chunked prefill, its waiting queue, preemption, and the KV blocks. We only set its flags. The limit "
        "on concurrency was the decode pod. Most agent calls have fewer than 2,048 new tokens, so llm-d does "
        "not split them, and the decode pod also does their prefill. At 100% load it processed 16,200 prompt "
        "tokens each second, and the prefill pod 4,550. The alerts: an interactive TTFT budget burn, a shed "
        "rate above 5%, KV pressure, and hop failures. KV pressure means a KV use above 92% for 5 minutes, or"
        " preemptions. We also alert when the guard is down or an engine stalls. Scale: decode first, because"
        " the decode pod is the limit. The planner asks for decode pods from the running sequences, and for "
        "prefill pods from the uncached prefill tokens. In the scale test, KEDA made a new decode pod 15 "
        "seconds after the request. At 10 times the traffic: more decode capacity first, 32 sequences on each"
        " decode pod, and FP8 KV. With 32 sequences, the TTFT p50 fell from 4.59 to 2.10 seconds, and FP8 KV "
        "doubled the tokens of each pod. Also more CPU RAM for LMCache, because it keeps the prefixes of the "
        "sessions. The wrong knobs: more prefill pods, because the prefill pod had little work. A longer "
        "queue, because calls then wait longer and still miss the TTFT goal. A lower split threshold, because"
        " each split pays the prefill, the hold, and the load from LMCache.")}
    for sid, rows, part in (("questions-1", q1, "1 of 2"), ("questions-2", q2, "2 of 2")):
        s.append((sid, "".join([
            title(f"The handout questions: our answers and the evidence ({part})"),
            table(["Question", "Our answer", "File or scrape"], rows, [24, 46, 30], size=21),
        ]), notes_q[sid]))

    bad = [["A benchmark at batch 8 is the production SLO.",
            "Our SLO comes from the app: interactive TTFT p95 at most 1.5 s up to 8K tokens, on a warm pod, "
            "with recorded app traffic."],
           ["The cache is full, so add a replica of the same size.",
            "First make the KV smaller (FP8 KV: twice the tokens) and keep the prefixes in LMCache. Then scale the "
            "pool that the planner names."],
           ["NCCL or NIXL in this repo moves the KV.",
            "The LMCache connector in vLLM moves the KV. Our code records the hop and holds the prefill answer "
            "until the store ends."],
           ["A replica is ready when the weights are on the GPU.",
            "A pod is warm only after the warmup and a probe. The warmup cut the first-minute TTFT p95 from 10.9 s "
            "to 7.3 s."],
           ["The overflow is another API, with no model and no limiter.",
            "qwen3.8-27b on the Superlinked API, only for an interactive 503 or 529. A Redis limiter caps the "
            "requests, tokens, and cost."],
           ["I wrote my own vLLM scheduler in the gateway.",
            "No. vLLM schedules inside each pod, and we only set its flags. The gateway decides what enters, the "
            "wait order, and the pod."],
           ["A 429 that left the cluster.",
            "The leave gate keeps each 429. At 150% load, it let only 124 interactive 503 calls go."],
           ["The gateway fixed OOM.",
            "No. vLLM manages the GPU memory. The gateway keeps the load below preemption, and a prompt that is too "
            "long gets a 413."],
           ["RAG is a third phase.",
            "No. The search runs outside the LLM, with SIE and Qdrant. Its chunks are prompt tokens: the engine "
            "sees only prefill and decode."],
           ["Wall seconds across models, with no token counts.",
            "We compare models for each token: KV bytes and the time between tokens. Each load test replays the "
            "same traffic in each arm."],
           ["The TTFT of a cold pod as the SLO.",
            "Our SLO runs use warm pods. The first minute of a new pod is a separate measure: 10.9 s cold, 7.3 s "
            "warm."]]
    s.append(("a-bad", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("Traps that the handout names, and what our design does"),
        table(["The trap", "What our design does, and the proof"], bad, [34, 66], size=20),
    ]), "For questions only. The handout names traps: bad answers that it marks down. This slide shows what our "
        "design does in place of each trap, and the proof. The benchmark: a fixed batch in a benchmark is not our"
        " SLO. Our SLO comes from the app. The interactive TTFT p95 must be at most 1.5 seconds for prompts up to"
        " 8K tokens, on a warm pod. The load tests replay the recorded calls of our app. A full cache: a new "
        "replica of the same size starts with an empty cache, and it splits the prefixes between more pods. We "
        "first make the KV smaller: FP8 KV gave each pod twice the tokens. The LMCache server keeps the prefixes "
        "in CPU RAM. Then we scale the pool that the planner names. The KV move: our code does not move KV bytes."
        " The LMCache connector in vLLM moves them. Our code records the hop and holds the prefill answer until "
        "the store ends. Ready: a pod with its weights on the GPU is not warm. The warmup cut the first-minute "
        "TTFT p95 from 10.9 to 7.3 seconds. The overflow: we name the model and the limiter. Only an interactive "
        "503 or 529 may leave, and a Redis limiter caps the requests, the tokens, and the cost. The overflow was "
        "off in all runs. The scheduler: vLLM schedules inside each pod, and we only set its flags. Our gateway "
        "decides what enters, the order of the waiting calls, and the pod. A 429: the leave gate keeps each 429 "
        "on our cluster. In the 150% test, it let only 124 interactive 503 calls go. OOM: the gateway does not "
        "fix the memory of the engine. vLLM manages the GPU memory. Our gateway keeps the load below the point of"
        " preemption, and no run preempted. A prompt that is too long for the model gets a 413 at edge. RAG: the "
        "search is not a phase of the engine. It runs outside the LLM, with SIE and Qdrant. Its chunks become "
        "prompt tokens, so the engine sees only prefill and decode. Wall seconds: we compare models for each "
        "token, with the KV bytes and the time between tokens. Each load test replays the same recorded traffic "
        "in each arm. A cold pod: our SLO runs use warm pods. The first minute of a new pod is a separate "
        "measure."))

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
        title("The queue questions: our answers and the proof"),
        table(["Question", "Our answer", "What we measured"], [
            ["Who waits in our queue, and who in the vLLM queue?",
             "Ours is in the llm-d flow control: a call waits there before the pick, while the pods are full. "
             "In vLLM, a call waits inside the pod for a batch slot.",
             "At 150% load: up to 48 interactive and 24 batch calls in ours, and up to 50 in the decode pod."],
            ["Waiting, running, preempted?",
             "vLLM has no swap: it preempts by computing the KV again. The decode pod runs at most 24 calls.",
             "Decode: up to 24 running and 50 waiting. 0 preemptions in all runs."],
            ["Queue depth of each pod, by mix?",
             "We read the queue of each pod from llm-d and from vLLM, for each traffic mix (dashboard 5).",
             "6 or fewer for most mixes. Up to 36 on the decode pod with the mixed app traffic."],
            ["A 32K retrieve and a short agent step are both ready. Who goes first?",
             "The short step. The split sends the long prompt to the prefill pod. Without the split, vLLM computes "
             "it in chunks.",
             "The short step was first in all rounds: 0.25 s with the split, 1.0 to 1.26 s without."],
            ["PagedAttention or the prefix cache: which saved memory?",
             "Neither changed the KV use at our load. The prefix cache saved compute. FP8 KV saved memory.",
             "KV use 13% with and without the prefix cache, 5.5% with FP8. 44% of prompt tokens were hits."],
            ["Engine flags for chunked prefill and batching?",
             "Decode: 24 sequences, chunks of 2,560 tokens, so a short prefill fits one chunk. Prefill: 8 "
             "sequences, chunks of 16,384.",
             "With 32 decode sequences, the TTFT p50 fell from 4.59 s to 2.10 s."],
            ["KV full after admit: the door or preemption?",
             "The door. The llm-d flow control holds calls while the pods are full, so vLLM did not preempt.",
             "Soak test: the KV use stayed at 90% or less, with 0 preemptions."],
            ["The client is gone: who frees the KV?",
             "vLLM. Envoy closes the stream, and vLLM stops the call and frees its KV blocks.",
             "Envoy closed all 46 aborted streams at the client time."],
            ["A pod returns: 100% at once, or a ramp?",
             "A ramp: 10%, 25%, 50%, then 100% of the score, while the TTFT p99 of the pod holds.",
             "First-minute TTFT p95: 14.6 s with the ramp, 57.3 s with a jump."],
        ], [24, 43, 33], size=21),
    ]), "For questions only. Our queue is the queue in the flow control of llm-d, the admit part of llm-d. We did"
        " not write a second queue, and the handout does not ask for one. It puts admit, place, and the queue in "
        "the gateway. We set the rules of the queue in the policy file. A call waits in our queue before llm-d "
        "picks a pod, and only while the pods are full. For this, llm-d gives each pod a fullness: its queued "
        "requests divided by 5, or its KV use divided by 90%, whichever is larger. The pods are full when the "
        "average fullness reaches 1. Batch calls already wait at 0.7. In our queue, interactive calls go before "
        "batch calls, and tenants take turns. A call that waits longer than its time limit gets a 503: 10 seconds"
        " for interactive, 120 seconds for batch. The handout draws the queue after the pick. But llm-d puts it "
        "before the pick, so the pick uses the state of the pods at the moment that a pod has room. The vLLM "
        "waiting queue is inside each pod, after the pick. The engine moves a call from it into the running batch"
        " when a batch slot and KV blocks are free. At 150% load, our queue held up to 48 interactive and 24 "
        "batch calls. The vLLM queue of the decode pod held up to 50. Two different things in llm-d answer two "
        "different questions. The queue order of the flow control answers: which waiting call goes next, and "
        "when? Interactive calls go before batch calls, tenants take turns, and the first in goes out first. It "
        "works only while the pods are full and calls wait. The placement policy of the scheduler answers: to "
        "which pod does the call go? It is prefix_then_load, and it works for each call when the call leaves the "
        "queue. We set both in the policy file. They run one after the other: the queue order picks the next "
        "call, and then the scheduler picks its pod. When the pods are not full, no call waits, and only the "
        "scheduler works. Batch calls in our app come from the freshness sweep: a job that checks the volatile "
        "claims of the bookmarks on the live web. All its calls use the batch class. Waiting, running, preempted:"
        " vLLM V1 has no swap. When the KV is short, it preempts a running call and computes its KV again later. "
        "The decode pod ran at most 24 calls, its limit, and up to 50 waited. No pod preempted a call in any run."
        " Queue depth for each pod: dashboard 5 shows the queue of each pod, as llm-d sees it and as vLLM reports"
        " it. At 100% load, the unique, shared-prefix, and stale-metrics mixes kept 6 or fewer. The mixed app "
        "traffic put up to 36 on the decode pod. The long retrieve and the short steps: we sent one retrieve of "
        "about 27,400 tokens and five short agent steps at the same time. Each arm had five rounds. In all "
        "rounds, a short step got the first token first. With the split, the long prompt went to the prefill pod,"
        " and the short steps started in 0.25 seconds. Without the split, the long prompt shared the decode pod. "
        "The engine computes a long prompt in chunks, so the short steps still went first. But they needed 1.0 to"
        " 1.26 seconds. The class of the retrieve, interactive or batch, did not change the order. At this low "
        "load, the pods were not full, so our queue did not hold the calls. PagedAttention and the prefix cache: "
        "PagedAttention packs the KV in blocks in all arms, and we cannot turn it off. On the shared-prefix mix "
        "at 100% load, the KV use was 13% with and without the prefix cache. So the prefix cache did not save "
        "memory at this load. It saved compute: 44% of the prompt tokens were cache hits, and the TTFT p50 fell "
        "from 0.60 to 0.43 seconds. FP8 KV saved memory: the KV use fell to 5.5%, and each pod held twice the "
        "tokens. Engine flags: vLLM adds calls to the running batch at each step, up to max-num-seqs. This is "
        "continuous batching. Chunked prefill cuts a long prompt into chunks of at most max-num-batched-tokens "
        "for each step. The decode pod has 24 sequences, because its KV holds about 20 sequences at 24K tokens "
        "and 32 at 8K. Its chunk of 2,560 tokens lets a prefill below the split threshold of 2,048 tokens run in "
        "one chunk. The engine does not accept less than 2,496 tokens, the largest image item of Gemma 4. The "
        "prefill pod has 8 sequences, because its calls leave after the hop. Its chunks of 16,384 tokens give "
        "throughput. With 32 decode sequences, the TTFT p50 fell from 4.59 to 2.10 seconds. A prefill chunk of "
        "8,192 tokens made it worse. KV full after admit: we refuse at the door. The flow control holds calls "
        "before the KV of the pods is full. In the soak test, the load grew each minute. The first refusal came "
        "in minute 18, at 100% load. 3,645 calls were ok, and the flow control refused 43. The KV use stayed at "
        "90% or less, and no pod preempted. Client gone: no component sends a message. The close moves along the "
        "same connections, one hop at a time. The client closes its stream to edge, and edge closes its stream to"
        " Envoy. Envoy closes its stream to the routing sidecar, and the sidecar stops its request to vLLM. But "
        "llm-d is not on this path: it only picks the pod before the call goes. The vLLM API server sees the "
        "closed connection and cancels the call. Its engine client then sends an abort for the call to the engine"
        " core. The scheduler marks the call as aborted and frees its KV blocks. We closed 20% of the streams in "
        "the middle: 46 calls. Envoy closed all 46 streams at the moment of the client close. But vLLM v0.30 does"
        " not count these aborts in its success counter, so we have no vLLM metric for the last step. A pod "
        "returns: we ramp. The warm controller gives the pod a ramp label of 10%, then 25%, 50%, and 100%, while "
        "the TTFT p99 of the pod holds. The ramp scorer of llm-d reads the label. In the restart test, the "
        "first-minute TTFT p95 of the returned pod was 14.6 seconds with the ramp, and 57.3 seconds with a jump. "
        "But the ramp is a score, not a cap. In the first 10 seconds, the empty pod got 75% of the calls, because"
        " the queue scorer likes an empty queue. So the ramp needs a cap."))

    record = ['{"src": "10.42.1.21", "dst": "10.42.1.20",', ' "tokens": 2371, "prefix_tokens": 2304,',
              ' "backend": "lmcache", "first_byte_ms": 496}']
    s.append(("a-warm", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("The hop record, and a cold pod against a warm pod"),
        two_columns([
            p("Same pod: llm-d picks only the decode pod. The KV is already there, so there is no hop and no "
              "record.", 24, INK),
            p("Two pods: one hop record for each split call, from the Envoy log:", 24, INK),
            (f'<div style="background:{CODE_BG};border-radius:12px;padding:12px 20px">'
             f'<p style="font-family:{MONO};font-size:22px;line-height:1.4;color:{INK}">'
             f'{"<br>".join(e(x) for x in record)}</p></div>'),
            p("The field src is the prefill pod, and dst is the decode pod. The field prefix_tokens is the part of "
              "the prompt that the decode pod did not compute. The field first_byte_ms is the TTFT.", 22),
            callout("The warmup routine", "Our system prompts, the shapes of our app, one split call through the "
                    "prefill pod, and a 4,000-token probe. The pod is warm only if the probe TTFT is at most 1.5 "
                    "times the warm baseline."),
        ], [
            measured("Restart test: we deleted the one decode pod at 70% load. The TTFT of the calls on the new "
                     "pod, in its first minute."),
            table(["", "No warmup", "Warmup"], [
                ["TTFT p95", "10.9 s", "7.3 s"],
                ["TTFT p50", "1.29 s", "1.03 s"],
                ["Outage", "275 s", "290 s"],
            ], [40, 30, 30]),
            p("Minutes 2 to 4: a TTFT p95 near 12.7 s in both arms. The warmup helps only the first minute.", 24,
              INK),
        ], left_w=860),
    ]), "For questions only. This slide answers the hop and warmth questions. Same pod: when llm-d picks only the"
        " decode pod, the KV is already there. The decode pod computes the prompt or finds it in its cache, so "
        "there is no hop and no record. Two pods: when llm-d splits a call, the prefill pod computes the KV and "
        "stores a copy in the LMCache server. The decode pod loads it. The script hop_records.py writes one "
        "record for each split call, from the Envoy log. The record has the source pod, the destination pod, the "
        "tokens, the cached tokens, and the backend. In this record, the decode pod got 2,304 of 2,371 tokens "
        "from the cache. It computed the rest again: the tokens after the last full LMCache chunk of 256 tokens. "
        "Is the new pod warm? A pod with its weights on the GPU is not warm yet. Its first calls can be slow, "
        "because its prefix cache is empty and some one-time work runs on the first calls. Warm the box and then "
        "re-quote the TTFT means this: send warmup calls first, and then measure the TTFT again. We did this as "
        "two arms of the restart test. We deleted the one decode pod at 70% load. With no warmup, the "
        "first-minute TTFT p95 on the new pod was 10.9 seconds. With the warmup, it was 7.3 seconds. The warmup "
        "made the outage 15 seconds longer. In minutes 2 to 4, both arms had a p95 near 12.7 seconds, so the "
        "warmup helps only the first minute. Our warmup routine sends our system prompts and the shapes of our "
        "app. A new decode pod also gets one split call through the prefill pod, so the hop path is warm too. "
        "Then a 4,000-token probe runs. The pod gets the warm label only if the probe TTFT is at most 1.5 times "
        "the warm baseline. Then the ramp starts. In the scale test, each new pod got the warm label 225 to 235 "
        "seconds after the request."))

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
    needs = ["A store signal for each request, in place of the global store counters.",
             "RDMA between nodes, with NIXL or a distributed KV store. Our LMCache path works only inside one node, "
             "and NIXL over TCP took about 4 s.",
             "Two or more pods in each pool, and a fallback: if the prefill request fails, the decode pod does the "
             "prefill itself.",
             "mTLS between pods. Our sidecar ran with plain HTTP.",
             "P/D only where long uncached prompts are common. For our traffic, two colocated replicas were "
             "faster."]
    needs_html = (f'<div style="background:{BOX_BG};border:2px solid {LINE};border-radius:12px;padding:16px 20px;'
                  f'display:flex;flex-direction:column;gap:10px">{p("A production hop needs", 28, INK, 600)}'
                  + "".join(p(f"{i}. {t}", 24, INK) for i, t in enumerate(needs, 1)) + "</div>")
    s.append(("a-production", "".join([
        p("Appendix", 24, ORANGE_TEXT, 600),
        title("The hop at production scale: what we keep, what we change"),
        two_columns([
            callout("Keep", "The llm-d scheduler decides, and the sidecar runs the two steps. The one-token prefill "
                    "request: the smallest vLLM request that runs a full prefill."),
            measured("The store barrier on the A100 node, under load. It waits for every store that LMCache had at "
                     "that time, with a cap of 0.5 s."),
            table(["When", "Holds", "Hit the 0.5 s cap"],
                  [["P/D load tests", "237", "170 (72%)"], ["End of session, prefill pod 1", "171", "163 (95%)"],
                   ["End of session, prefill pod 2", "68", "67 (99%)"],
                   ["Start of session, one request at a time", "3", "0 (0.19 s on average)"]], [48, 18, 34]),
        ], [needs_html], left_w=820),
    ]), "For questions only. The one-token request is production quality, and the barrier is not. Under load, it "
        "hit its half-second cap on most split calls, so a production hop needs a store signal for each request."))
    by_id = {sid: (sid, body, notes) for sid, body, notes in s}
    assert sorted(by_id) == sorted(MAIN + APPENDIX), set(by_id) ^ set(MAIN + APPENDIX)
    return [by_id[sid] for sid in MAIN + APPENDIX]


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
             "sections": {"s1": {"description": "The app and the system", "start": "cover"},
                          "s2": {"description": "Capacity and the cluster design", "start": "capacity"},
                          "s3": {"description": "One request, end to end: the code, the proof, and the "
                                                "dashboards", "start": "app"},
                          "s4": {"description": "The results and the handout questions",
                                 "start": "hypothesis"},
                          "s5": {"description": "Appendix, for questions only", "start": "a-place"}},
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
                                                # the queue slide names the notebook
                                                ROOT / "notebook" / "part5_queue.ipynb",
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
