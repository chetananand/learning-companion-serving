"""Write the talk into the repo: docs/talk/deck.pdf (the slides) and docs/talk/notes.md (the speaker notes).

The live deck is a claude.ai Slides artifact that tools/make_deck.py writes. This script uses the same slides and
the same images (make_deck.LOCAL: the repo files of the deck assets). Each slide is one 1920 x 1080 page, and
headless Chrome prints the pages to one PDF. Run it again after each change to the deck.

Usage: python3 tools/deck_pdf.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import make_deck  # noqa: E402

OUT = ROOT / "docs" / "talk"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600"
         "&family=JetBrains+Mono:wght@400&display=block")
CSS = ("@page{size:1920px 1080px;margin:0} html,body{margin:0;padding:0} p,h1,h2{margin:0} "
       "table{border-collapse:collapse} aside{display:none} "
       "section{position:relative;width:1920px;height:1080px;box-sizing:border-box;overflow:hidden;"
       "break-after:page}")
PLAY = ('<svg viewBox="0 0 24 24" style="{style}" fill="currentColor" aria-hidden="true">'
        '<polygon points="6 3 20 12 6 21 6 3"/></svg>')


def blob_files() -> dict[str, Path]:
    """The repo file for each /_blob/ id of the deck."""
    return {url.rsplit("/", 1)[1]: ROOT / make_deck.LOCAL[key] for key, url in make_deck.ASSETS.items()}


def print_page(build: Path) -> str:
    """One HTML page with all slides in deck order, the images from the repo, and no speaker notes."""
    files = blob_files()
    order = json.loads((build / "project" / "deck.json").read_text())["order"]
    sections = []
    for sid in order:
        html = (build / "project" / "slides" / f"{sid}.html").read_text()
        html = re.sub(r"/_blob/([0-9a-f]{32})", lambda m: files[m.group(1)].as_uri(), html)
        html = re.sub(r'<x-icon name="Play" style="([^"]*)"></x-icon>', lambda m: PLAY.format(style=m.group(1)),
                      html)
        sections.append(html)
    return (f'<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{FONTS}">'
            f"<style>{CSS}</style></head><body>{''.join(sections)}</body></html>")


def notes_md(deck: list[tuple[str, str, str]]) -> str:
    """The title and the speaker notes of each slide, in order."""
    total_main = next(i for i, (sid, _, _) in enumerate(deck, 1) if sid == "changed")
    lines = ["# The talk: slides and speaker notes", "",
             "The slides are in `deck.pdf`. `tools/make_deck.py` writes the deck, and `tools/deck_pdf.py` writes "
             "these two files. The talk is on 2026-10-10.", ""]
    for n, (sid, body, notes) in enumerate(deck, 1):
        if n == total_main + 1:
            lines += ["## Appendix (for questions only)", ""]
        m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", body, re.S)
        name = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else sid
        label = f"{n}." if n <= total_main else f"A{n - total_main}."
        lines += [f"### {label} {name}", "", make_deck.code_names(make_deck.paragraphs(notes)), ""]
    return "\n".join(lines)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        build = Path(tmp)
        deck = make_deck.write(build)
        page = build / "print.html"
        page.write_text(print_page(build))
        pdf = OUT / "deck.pdf"
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--virtual-time-budget=20000", f"--print-to-pdf={pdf}", page.as_uri()],
                       check=True, capture_output=True)
    (OUT / "notes.md").write_text(notes_md(deck))
    print(pdf.relative_to(ROOT), f"{pdf.stat().st_size / 1e6:.1f} MB,", len(deck), "slides")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
