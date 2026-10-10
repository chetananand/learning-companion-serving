"""The architecture slide: the path of one LLM call (docs/spec/10-talk.md, slide 3).

A hand-placed flow in the Mermaid style. Each box says who decides or does the work. Each arrow names what moves,
with its step number. Solid arrows occur for each call. Dashed orange arrows occur only when the router also picks
a prefill pod. The page has a fixed size of 1,700 x 776 px, and the slide shows it at that size, so the text stays
at 24 px.

Usage: python3 tools/arch_diagram.py   (writes plots/slides/arch.html)
Then RENDER writes plots/slides/arch.png at 2x with headless Chrome. The page prints OVERFLOW lines in its
report element if a text line does not fit its box (see the --dump-dom command in RENDER).
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "plots" / "slides" / "arch.html"
W, H = 1700, 776
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
RENDER = (f'"{CHROME}" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 '
          f"--window-size={W},{H} --virtual-time-budget=8000 --screenshot=plots/slides/arch.png "
          "file://$PWD/plots/slides/arch.html\n"
          f'"{CHROME}" --headless=new --disable-gpu --virtual-time-budget=8000 --dump-dom '
          "file://$PWD/plots/slides/arch.html | grep -A3 'id=\"report\"'")

PAPER, INK, SOFT, ORANGE, ORANGE_TEXT = "#FBFBF8", "#1F2329", "#4A5260", "#E07B2E", "#B4561A"
AR_BG, AR_LINE, AR_TEXT = "#EAF0FA", "#8AA9DA", "#1F4E9E"
EN_BG, EN_LINE, POD_BG = "#F1F3F6", "#AEB5BF", "#E3E7EC"


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    lines: list[str]  # HTML: <b> for the name

    def side(self, where: str, at: int | None = None) -> tuple[int, int]:
        """A point on the border: top, bottom, left, or right, at x (top, bottom) or y (left, right)."""
        px = at if at is not None else self.x + self.w // 2
        py = at if at is not None else self.y + self.h // 2
        return {"top": (px, self.y), "bottom": (px, self.y + self.h), "left": (self.x, py),
                "right": (self.x + self.w, py)}[where]


# Admission control + routing (the top band). The app sits outside, at the left.
APP = Box(0, 243, 230, 84, ["<b>companion-api</b>", "the app"])
GUARD = Box(462, 54, 420, 114, ["<b>guard models</b>", "Prompt Guard 2, Nemotron Safety", "is the prompt safe?"])
EDGE = Box(462, 228, 320, 114, ["<b>edge</b> (our code)", "admit: block, refuse,", "or pass the call"])
ROUTER = Box(1020, 54, 660, 114, ["<b>llm-d router</b>", "where: which decode pod? And a prefill pod,",
                                  "if 2,048 or more prompt tokens are not in a cache"])
ENVOY = Box(1020, 228, 420, 114, ["<b>Envoy AI Gateway</b>", "admit: is the tenant within", "its token budget?"])
# The engine (the bottom band): the prefill pod at the left, the decode pod at the right, LMCache between them.
BARRIER = Box(42, 460, 500, 114, ["<b>store barrier</b> (our code)", "holds the reply until LMCache",
                                  "has stored the KV copy"])
VPRE = Box(42, 630, 500, 114, ["<b>vLLM</b> · KV cache in GPU memory", "computes the KV of the prompt"])
SIDECAR = Box(1158, 460, 500, 114, ["<b>routing sidecar</b> (llm-d)", "if the router picked a prefill pod,",
                                    "it first sends the prompt there"])
VDEC = Box(1158, 630, 500, 114, ["<b>vLLM</b> · KV cache in GPU memory", "computes the KV that it does not have,",
                                 "then generates the answer token by token"])
LMC = Box(735, 617, 230, 140, ["<b>LMCache server</b>", "a copy of the KV", "in CPU RAM"])
BOXES = [APP, GUARD, EDGE, ROUTER, ENVOY, BARRIER, VPRE, SIDECAR, VDEC]

# (x, y, w, h, title, title position, fill, line, title color, title size)
FRAMES = [
    (430, 0, 1270, 360, "Admission control + routing", (452, 10), AR_BG, AR_LINE, AR_TEXT, 26),
    (0, 396, 1700, 380, "Engine (node 1): Gemma 4 31B FP8 on vLLM", None, EN_BG, EN_LINE, INK, 26),
    (22, 414, 540, 344, "vllm-prefill pod · 1 GPU", (40, 420), POD_BG, EN_LINE, SOFT, 22),
    (1138, 414, 540, 344, "vllm-decode pod · 1 GPU", (-1660, 420), POD_BG, EN_LINE, SOFT, 22),
]


@dataclass
class Arrow:
    a: tuple[int, int]
    b: tuple[int, int]
    num: str  # the step number, or "" for an answer
    text: str
    label: tuple[int, int]  # the label anchor
    place: str  # "center": the label sits on the line; "right": the label starts at the anchor
    dashed: bool = False
    bg: str = PAPER


ARROWS = [
    Arrow(APP.side("right"), EDGE.side("left", 285), "1", "the request", (330, 285), "center"),
    Arrow(EDGE.side("top", 512), GUARD.side("bottom", 512), "2", "the prompt", (524, 198), "right", bg=AR_BG),
    Arrow(GUARD.side("bottom", 732), EDGE.side("top", 732), "", "safe or not safe", (744, 198), "right", bg=AR_BG),
    Arrow(EDGE.side("right"), ENVOY.side("left", 285), "3", "the request", (901, 285), "center", bg=AR_BG),
    Arrow(ENVOY.side("top", 1050), ROUTER.side("bottom", 1050), "4", "the prompt", (1062, 198), "right", bg=AR_BG),
    Arrow(ROUTER.side("bottom", 1260), ENVOY.side("top", 1260), "", "the addresses of the picked pods", (1272, 198),
          "right", bg=AR_BG),
    Arrow(ENVOY.side("bottom", 1300), SIDECAR.side("top", 1300), "5", "the request", (1312, 378), "right"),
    Arrow(SIDECAR.side("left", 496), BARRIER.side("right", 496), "5a", "the prompt, to compute its KV", (850, 496),
          "center", dashed=True, bg=EN_BG),
    Arrow(BARRIER.side("bottom", 272), VPRE.side("top", 272), "", "the prompt", (284, 602), "right", dashed=True,
          bg=POD_BG),
    Arrow(VPRE.side("right", 705), LMC.side("left", 705), "5b", "a copy<br>of the KV", (638, 662), "center",
          dashed=True, bg=EN_BG),
    Arrow(BARRIER.side("right", 540), SIDECAR.side("left", 540), "5c", "the reply, after the KV is stored", (850, 540),
          "center", dashed=True, bg=EN_BG),
    Arrow(SIDECAR.side("bottom", 1408), VDEC.side("top", 1408), "6", "the request", (1420, 602), "right", bg=POD_BG),
    Arrow(LMC.side("right", 705), VDEC.side("left", 705), "6", "the stored<br>KV, if any", (1052, 662), "center",
          bg=EN_BG),
]


def line_svg(arrow: Arrow) -> str:
    (x1, y1), (x2, y2) = arrow.a, arrow.b
    length = max(abs(x2 - x1), abs(y2 - y1))
    dx, dy = (x2 - x1) / length, (y2 - y1) / length
    head, half = 16, 8
    bx, by = x2 - head * dx, y2 - head * dy  # the line ends at the base of the head
    color = ORANGE if arrow.dashed else INK
    dash = ' stroke-dasharray="11 7"' if arrow.dashed else ""
    pts = f"{x2},{y2} {bx - half * dy:.1f},{by + half * dx:.1f} {bx + half * dy:.1f},{by - half * dx:.1f}"
    return (f'<line x1="{x1}" y1="{y1}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{color}" stroke-width="3"{dash}/>'
            f'<polygon points="{pts}" fill="{color}"/>')


def label_html(arrow: Arrow) -> str:
    x, y = arrow.label
    shift = "translate(-50%,-50%)" if arrow.place == "center" else "translateY(-50%)"
    color, pill = (ORANGE_TEXT, ORANGE) if arrow.dashed else (INK, INK)
    num = f'<span class="num" style="background:{pill}">{arrow.num}</span>' if arrow.num else ""
    return (f'<div class="lab" style="left:{x}px;top:{y}px;transform:{shift};background:{arrow.bg};color:{color}">'
            f"{num}{arrow.text}</div>")


def box_html(b: Box, cls: str = "box") -> str:
    rows = "".join(f'<div class="ln">{t}</div>' for t in b.lines)
    return f'<div class="{cls}" style="left:{b.x}px;top:{b.y}px;width:{b.w}px;height:{b.h}px">{rows}</div>'


def cylinder_svg(b: Box, ry: int = 16) -> str:
    x, y, w, h = b.x, b.y, b.w, b.h
    body = f"M{x},{y + ry} L{x},{y + h - ry} A{w / 2},{ry} 0 0 0 {x + w},{y + h - ry} L{x + w},{y + ry}"
    return (f'<path d="{body}" fill="#FFFFFF" stroke="{SOFT}" stroke-width="2"/>'
            f'<ellipse cx="{x + w / 2}" cy="{y + ry}" rx="{w / 2}" ry="{ry}" fill="#FFFFFF" stroke="{SOFT}" '
            'stroke-width="2"/>')


def legend_html() -> str:
    def sample(y: int, dashed: bool) -> str:
        a = Arrow((4, y), (70, y), "", "", (0, 0), "right", dashed=dashed)
        return line_svg(a)
    svg = f'<svg width="80" height="120" style="left:0;top:0">{sample(30, False)}{sample(84, True)}</svg>'
    rows = [(14, "every call", INK), (68, "only when the router also<br>picked a prefill pod", ORANGE_TEXT),
            (146, "The answer tokens go back<br>to the app on the same path.", SOFT)]
    text = "".join(f'<div class="leg" style="left:{88 if i < 2 else 4}px;top:{top}px;color:{c}">{t}</div>'
                   for i, (top, t, c) in enumerate(rows))
    return f'<div style="position:absolute;left:0;top:0;width:420px;height:220px">{svg}{text}</div>'


def page() -> str:
    frames = []
    for x, y, w, h, name, pos, fill, line, color, size in FRAMES:
        if pos is None:  # centered at the top
            tpos = f"left:{x + w // 2}px;top:{y + 8}px;transform:translateX(-50%)"
        elif pos[0] < 0:  # a negative x: the right end of the title
            tpos = f"left:{-pos[0]}px;top:{pos[1]}px;transform:translateX(-100%)"
        else:
            tpos = f"left:{pos[0]}px;top:{pos[1]}px"
        frames.append(f'<div class="frame" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;background:{fill};'
                      f'border:2px solid {line}"></div><div class="ftitle" style="{tpos};color:{color};'
                      f'font-size:{size}px">{html.escape(name)}</div>')
    svg = (f'<svg width="{W}" height="{H}" style="left:0;top:0">{cylinder_svg(LMC)}'
           f'{"".join(line_svg(a) for a in ARROWS)}</svg>')
    lmc_text = Box(LMC.x, LMC.y + 32, LMC.w, LMC.h - 32, LMC.lines)
    check = """
document.fonts.ready.then(() => {
  const out = [];
  document.querySelectorAll('.box .ln').forEach(el => {
    const box = el.parentElement;
    if (el.scrollWidth > box.clientWidth - 8) out.push('OVERFLOW width ' + el.textContent);
  });
  document.querySelectorAll('.box').forEach(b => {
    if (b.scrollHeight > b.clientHeight + 1) out.push('OVERFLOW height ' + b.textContent);
  });
  document.getElementById('report').textContent = out.length ? out.join('\\n') : 'FIT OK';
});"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>The path of one LLM call</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700&display=block">
<style>
html,body{{margin:0;padding:0;background:{PAPER}}}
#c{{position:relative;width:{W}px;height:{H}px;overflow:hidden;font-family:'IBM Plex Sans',Arial,sans-serif;
  color:{INK}}}
#c svg{{position:absolute}}
.frame{{position:absolute;box-sizing:border-box;border-radius:16px}}
.ftitle{{position:absolute;font-weight:600;line-height:32px;white-space:nowrap}}
.box,.cyl{{position:absolute;box-sizing:border-box;display:flex;flex-direction:column;justify-content:center;
  align-items:center;text-align:center;font-size:24px;line-height:30px}}
.box{{background:#FFFFFF;border:2px solid {SOFT};border-radius:10px;padding:0 14px}}
.ln{{white-space:nowrap}}
b{{font-weight:600}}
.lab{{position:absolute;font-size:24px;line-height:29px;white-space:nowrap;padding:1px 7px;border-radius:6px;
  text-align:center}}
.num{{display:inline-block;color:#FFFFFF;font-weight:700;border-radius:14px;padding:0 9px;margin-right:8px;
  line-height:27px;font-size:22px}}
.leg{{position:absolute;font-size:24px;line-height:29px;white-space:nowrap}}
#report{{position:absolute;left:0;top:{H}px}}
</style></head><body><div id="c">
{"".join(frames)}
{svg}
{"".join(box_html(b) for b in BOXES)}{box_html(lmc_text, "cyl")}
{"".join(label_html(a) for a in ARROWS)}
{legend_html()}
</div><pre id="report">waiting</pre>
<script>{check}</script>
</body></html>
"""


def main() -> int:
    OUT.write_text(page())
    print(OUT.relative_to(ROOT), json.dumps({"size": [W, H], "boxes": len(BOXES) + 1, "arrows": len(ARROWS)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
