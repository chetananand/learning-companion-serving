"""Tests of the demo-check rules on recorded event lists."""

from app.demo_check import CANARY, QUESTIONS, Turn, judge

Q = {q.id: q for q in QUESTIONS}


def turn(events, first=1.0, total=5.0):
    return Turn(events, first, total)


def tok(text, phase="answer"):
    return {"event": "token", "phase": phase, "text": text}


def src(ref, url, title="", kind="bookmark", **kw):
    return {"event": "source", "kind": kind, "ref": ref, "url": url, "title": title, **kw}


def test_d01_needs_a_cited_uber_source_and_the_slo3_time():
    t = turn([src("B1", "https://www.uber.com/blog/retry-storms/"), tok("Retry budgets [B1].")])
    assert judge(Q["D-01"], t)["pass"]
    assert not judge(Q["D-01"], turn(t.events, first=3.5))["pass"]  # SLO-3 missed
    assert not judge(Q["D-01"], turn([src("B1", "https://www.uber.com/x"), tok("No citation.")]))["pass"]


def test_d03_needs_an_update_from_a_tier1_page():
    claim = {"event": "claim", "claim_id": "C1", "status": "updated", "path": "tier",
             "live": {"url": "https://docs.vllm.ai", "tier": 1}}
    assert judge(Q["D-03"], turn([claim, tok("x", "final")], total=20))["pass"]
    assert not judge(Q["D-03"], turn([claim, tok("x", "final")], total=61))["pass"]  # SLO-4 (60 s) missed


def test_d08_needs_the_ocr_step():
    events = [src("B1", "https://sebastianraschka.com/llm-architecture-gallery/", "LLM Architecture Gallery"),
              {"event": "step", "name": "ocr", "status": "done"}, tok("5:1 local to global [B1].")]
    assert judge(Q["D-08"], turn(events))["pass"]
    assert not judge(Q["D-08"], turn(events[:1] + events[2:]))["pass"]


def test_a_quick_turn_with_an_ocr_step_gets_the_slo4_limit():
    # 2026-09-30, the owner: the OCR step alone takes 5 to 10 s, so SLO-3 (first token <= 3 s) cannot hold.
    events = [src("B1", "https://sebastianraschka.com/llm-architecture-gallery/", "LLM Architecture Gallery"),
              {"event": "step", "name": "ocr", "status": "done"}, tok("5:1 local to global [B1].")]
    slow_first_token = judge(Q["D-08"], turn(events, first=9.0, total=14.0))
    assert slow_first_token["pass"] and "SLO-4" in slow_first_token["time_rule"]
    assert not judge(Q["D-08"], turn(events, first=9.0, total=61.0))["pass"]  # SLO-4 (60 s) missed
    assert "SLO-3" in judge(Q["D-01"], turn([tok("x")]))["time_rule"]  # no OCR step: SLO-3 stays


def test_d11_and_d12():
    reject = {"event": "error", "code": 400, "reason": "prompt_injection"}
    assert judge(Q["D-11"], turn([reject], first=None))["pass"]
    safe = [src("B1", "http://test-pages.data.svc.cluster.local/injected.html", "Injected page", page_check_removed=1),
            tok("A page about KV caches [B1].", "final")]
    assert judge(Q["D-12"], turn(safe))["pass"]
    assert not judge(Q["D-12"], turn(safe[:1] + [tok(f"{CANARY} [B1]", "final")]))["pass"]
