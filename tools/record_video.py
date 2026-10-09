"""Record a browser video on the cluster node: the Streamlit demo, or a Grafana dashboard during a load.

The owner was away on 2026-10-01 and asked for a screen recording. This tool runs in the image
companion/browser:dev (Playwright and Chromium) with the host network of the node, so it reaches the NodePorts
on 127.0.0.1. It needs only the Python standard library and Playwright (the image has no repo code).

  demo     For each question: a new chat, the mode, the question, then wait for "Done in" (or an error). It
           expands the trace, holds the answer on the screen, and saves a screenshot of each answer.
  grafana  Open a dashboard in kiosk mode with a 5 s refresh for --seconds, and save a screenshot each minute.

Output: <out>/video.webm, <out>/*.png, and <out>/record.json (the times and the result of each question).
Usage on the node (cluster/sessions/record.sh runs it):
  sudo docker run --rm --network host --shm-size 1g --user root -v ~/companion/tools:/rec:ro \\
    -v /var/lib/companion/app-data/videos:/out companion/browser:dev \\
    python /rec/record_video.py demo --questions /out/questions.json --ids D-03,D-08 --out /out/demo-x
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

UI = "http://127.0.0.1:30851"
GRAFANA = "http://127.0.0.1:30300"
SIZE = {"width": 1440, "height": 900}
DONE = re.compile(r"Done in \d+ ms")


def pick(questions: list[dict[str, Any]], ids: str) -> list[dict[str, Any]]:
    wanted = [i.strip() for i in ids.split(",") if i.strip()]
    by_id = {q["id"]: q for q in questions}
    missing = [i for i in wanted if i not in by_id]
    if missing:
        raise SystemExit(f"unknown question ids: {missing}")
    return [by_id[i] for i in wanted] if wanted else questions


def finish(context: Any, page: Any, out: Path) -> str | None:
    video = page.video
    context.close()
    if video is None:
        return None
    target = out / "video.webm"
    Path(video.path()).rename(target)
    return str(target)


def demo(pw: Any, questions: list[dict[str, Any]], out: Path, timeout_s: float, hold_s: float) -> dict[str, Any]:
    browser = pw.chromium.launch()
    context = browser.new_context(viewport=SIZE, record_video_dir=str(out), record_video_size=SIZE)
    page = context.new_page()
    page.goto(UI)
    page.locator('[data-testid="stChatInputTextArea"]').wait_for(timeout=60_000)
    results = []
    for q in questions:
        t0 = time.monotonic()
        page.get_by_role("button", name="New chat").click()
        page.wait_for_timeout(1500)
        page.locator('[data-testid="stRadio"]').get_by_text(q["mode"], exact=True).click()
        page.wait_for_timeout(1000)
        done_before = page.get_by_text(DONE).count()
        alerts_before = page.locator('[data-testid="stAlert"]').count()
        box = page.locator('[data-testid="stChatInputTextArea"]')
        box.fill(q["text"])
        box.press("Enter")
        outcome = "timeout"
        while time.monotonic() - t0 < timeout_s:
            if page.get_by_text(DONE).count() > done_before:
                outcome = "done"
                break
            if page.locator('[data-testid="stAlert"]').count() > alerts_before:
                outcome = "error"
                break
            page.wait_for_timeout(1000)
        if outcome == "done":
            page.get_by_text(DONE).last.click()  # expand the trace: the steps, the sources, and the claims
        page.wait_for_timeout(1000)
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(hold_s * 1000)
        page.screenshot(path=str(out / f"{q['id']}.png"), full_page=True)
        label = page.get_by_text(DONE).last.inner_text() if outcome == "done" else ""
        results.append({"id": q["id"], "mode": q["mode"], "outcome": outcome, "label": label,
                        "seconds": round(time.monotonic() - t0, 1)})
        print(json.dumps(results[-1]), flush=True)
    video = finish(context, page, out)
    browser.close()
    return {"kind": "demo", "url": UI, "video": video, "questions": results}


def grafana(pw: Any, uid: str, seconds: float, out: Path) -> dict[str, Any]:
    browser = pw.chromium.launch()
    context = browser.new_context(viewport=SIZE, record_video_dir=str(out), record_video_size=SIZE,
                                  http_credentials={"username": "admin",
                                                    "password": os.environ["GRAFANA_PASSWORD"]})
    page = context.new_page()
    page.goto(f"{GRAFANA}/d/{uid}?orgId=1&kiosk&refresh=5s&from=now-15m&to=now")
    t0, shots = time.monotonic(), 0
    while time.monotonic() - t0 < seconds:
        page.wait_for_timeout(min(60_000, max(1000, (seconds - (time.monotonic() - t0)) * 1000)))
        shots += 1
        page.screenshot(path=str(out / f"frame-{shots:03d}.png"))
    video = finish(context, page, out)
    browser.close()
    return {"kind": "grafana", "uid": uid, "seconds": seconds, "video": video, "screenshots": shots}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--questions", required=True, help="JSON list of {id, text, mode} (from app/demo_check.py)")
    d.add_argument("--ids", default="", help="comma-separated question ids (default: all)")
    d.add_argument("--timeout", type=float, default=180.0, help="seconds to wait for one answer")
    d.add_argument("--hold", type=float, default=6.0, help="seconds to hold each answer on the screen")
    d.add_argument("--out", required=True)
    g = sub.add_parser("grafana")
    g.add_argument("--uid", default="companion-scaling")
    g.add_argument("--seconds", type=float, required=True)
    g.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        if a.cmd == "demo":
            record = demo(pw, pick(json.loads(Path(a.questions).read_text()), a.ids), out, a.timeout, a.hold)
        else:
            record = grafana(pw, a.uid, a.seconds, out)
    (out / "record.json").write_text(json.dumps(record, indent=1))
    print(json.dumps({k: v for k, v in record.items() if k != "questions"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
