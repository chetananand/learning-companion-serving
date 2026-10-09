"""ASD-STE100 style linter for the Markdown files in this repo.

This tool is a helper. It is not the official ASD-STE100 checker. The official
dictionary is not in this repo. The tool checks the writing rules that a script
can check, and a project word list. A human review is still necessary.

Checks (error = E, warning = W):
  E101  Sentence longer than 25 words (descriptive text).
  E102  Instruction longer than 20 words (numbered steps and imperative lines).
  E201  Semicolon in prose.
  E202  Contraction (don't, it's, we'll).
  E301  Word from the project list of non-approved words. The message gives the approved word.
  W401  Modal verb that STE does not use for instructions (should, would, could).
  W402  Possible passive voice.
  W403  Possible -ing form that is not a technical name.
  W404  Latin abbreviation (e.g., i.e., etc.) or "vs".
  W405  Paragraph with more than 6 sentences.

The linter does not check code fences, inline code, URLs, and link targets.
It also skips lines between <!-- ste-ignore-start --> and <!-- ste-ignore-end -->,
and single lines that contain <!-- ste-ignore -->. Use these markers only for
quotes from other documents and for lists of words to avoid.
Usage: python tools/ste_lint.py docs/ [--strict]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_DESCRIPTIVE = 25
MAX_PROCEDURAL = 20

# Project word list. Left: word to avoid. Right: approved word or phrase to use.
AVOID: dict[str, str] = {
    "utilize": "use", "utilise": "use", "utilization": "use (technical name is OK in `gpu-memory-utilization`)",
    "leverage": "use", "ensure": "make sure", "ensures": "makes sure", "perform": "do", "performs": "does",
    "provide": "give / supply", "provides": "gives / supplies", "require": "is necessary / must",
    "requires": "is necessary / must", "required": "necessary", "obtain": "get", "assist": "help",
    "attempt": "try", "commence": "start", "initiate": "start", "demonstrate": "show", "demonstrates": "shows",
    "indicate": "show", "indicates": "shows", "determine": "find / calculate", "determines": "finds",
    "establish": "make / set", "facilitate": "help", "modify": "change", "modifies": "changes",
    "maintain": "keep", "maintains": "keeps", "retain": "keep", "purchase": "buy", "permit": "let",
    "prior": "before", "subsequent": "next / after", "subsequently": "after", "additional": "more / other",
    "additionally": "also", "numerous": "many", "regarding": "about", "concerning": "about",
    "whether": "if", "via": "through", "therefore": "thus / as a result", "hence": "thus",
    "approximately": None, "basically": "(delete)", "simply": "(delete)", "just": "(delete) / only",
    "very": "(delete)", "really": "(delete)", "quite": "(delete)", "actually": "(delete)",
    "enormous": "large", "huge": "large", "tiny": "small", "massive": "large",
    "anticipate": "expect", "commonly": "usually", "consequently": "as a result",
    "sufficiently": "enough", "comprise": "contain / include", "comprises": "contains / includes",
    "endeavor": "try", "execute": "run", "executes": "runs",
}
AVOID = {k: v for k, v in AVOID.items() if v is not None}

PHRASES: dict[str, str] = {
    "in order to": "to",
    "due to": "because of",
    "prior to": "before",
    "a number of": "some / many",
    "as well as": "and",
    "set up": "install / configure",
    "carry out": "do",
    "make use of": "use",
    "in the event that": "if",
    "at this point in time": "now",
}

MODALS = re.compile(r"\b(should|would|could)\b", re.I)
PASSIVE = re.compile(
    r"\b([Ii]s|[Aa]re|[Ww]as|[Ww]ere|[Bb]e|[Bb]een|[Bb]eing)\s+(\w+ly\s+)?([a-z]\w*ed|built|done|made|given|kept|held|known|seen|shown|sent|set|put|run|read|written|taken|chosen|driven|thrown)\b"
)
CONTRACTION = re.compile(r"\b\w+(n't|'re|'ll|'ve|'d|'m)\b|\b(it's|that's|there's|what's|let's)\b", re.I)
LATIN = re.compile(r"\b(e\.g\.|i\.e\.|etc\.|vs\.?)(?=\s|$|\))", re.I)
ING = re.compile(r"\b[a-z]{3,}ing\b")
# -ing words that are technical names, nouns, or adjectives we accept in this project.
ING_OK = {
    "caching", "batching", "routing", "scheduling", "streaming", "string", "thing", "things", "during",
    "morning", "evening", "nothing", "something", "anything", "everything", "ring", "king", "bring",
    "ping", "padding", "embedding", "embeddings", "logging", "tracing", "sharding", "pooling",
    "prefill-ing", "sliding", "pending", "warning", "warnings", "setting", "settings", "meaning",
    "training", "listing", "mapping", "timing", "missing", "remaining", "incoming", "outgoing",
    "existing", "following", "running", "waiting", "chunking", "tokenizing", "reasoning", "thinking",
    "bookmarking", "fact-checking", "binding", "rendering", "hashing", "labeling", "pricing",
    "building", "housekeeping", "spring", "offloading", "handling", "billing", "networking", "casing",
    "loading", "encoding", "sharing", "reranking", "indexing", "processing", "ingestion",
    "serving", "learning", "scaling", "autoscaling", "computing", "monitoring", "alerting",
    "prefilling", "benchmarking", "partitioning", "slicing", "queueing", "queuing", "tooling",
    "overflowing", "pinning", "heading", "headings", "spelling", "wording", "writing",
    "engineering", "gating", "ramping", "draining", "hardening",
    "recording",  # Prometheus "recording rules" (technical name)
}
IMPERATIVE_START = re.compile(
    r"^(use|run|start|stop|set|add|install|configure|send|make|do|keep|write|read|record|measure|compare|"
    r"open|close|create|delete|remove|push|pull|deploy|scale|kill|restart|replace|select|show|test|put|"
    r"give|get|let|move|turn|type|copy|save|load|apply|enable|disable|export|import|build|sync|call|"
    r"point|pin|tag|warm|drain|seed|query|plot|scrape|terminate|launch|attach|check|examine)\b",
    re.I,
)
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-/.:%_]*")


@dataclass
class Finding:
    path: Path
    line: int
    code: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.code} {self.text}"


def strip_markup(line: str) -> str:
    line = re.sub(r"`[^`]*`", " CODE ", line)
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"https?://\S+", " URL ", line)
    line = re.sub(r"<[^>]+>", " ", line)
    line = re.sub(r"[*_#>|]", " ", line)
    return line


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])", text.strip())
    return [p for p in parts if p.strip()]


def word_count(s: str) -> int:
    return len([w for w in WORD.findall(s) if w not in {"CODE", "URL"}])


def text_lines(path: Path) -> list[str]:
    """The text to check. A notebook gives only its markdown cells: code, outputs, and images are not text."""
    raw = path.read_text(encoding="utf-8")
    if path.suffix != ".ipynb":
        return raw.splitlines()
    import json

    lines: list[str] = []
    for cell in json.loads(raw).get("cells", []):
        if cell.get("cell_type") == "markdown":
            src = cell.get("source", "")
            lines.extend(("".join(src) if isinstance(src, list) else src).splitlines() + [""])
    return lines


def lint_file(path: Path) -> list[Finding]:
    out: list[Finding] = []
    in_fence = False
    para_sentences = 0
    para_start = 0
    ignore = False
    for i, raw in enumerate(text_lines(path), start=1):
        if "<!-- ste-ignore-start -->" in raw:
            ignore = True
            continue
        if "<!-- ste-ignore-end -->" in raw:
            ignore = False
            continue
        if ignore or "<!-- ste-ignore -->" in raw:
            continue
        if raw.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = raw.strip()
        if not stripped:
            if para_sentences > 6:
                out.append(Finding(path, para_start, "W405", f"paragraph has {para_sentences} sentences (max 6)"))
            para_sentences = 0
            continue
        is_table = stripped.startswith("|")
        is_heading = stripped.startswith("#")
        is_list = bool(re.match(r"^(\d+\.|[-*+])\s", stripped))
        text = strip_markup(stripped)
        text = re.sub(r"^(\d+\.|[-*+])\s+(\[[ xX]\]\s+)?", "", text.strip())
        if not text.strip():
            continue
        # Word rules apply to every line, tables included.
        low = text.lower()
        for phrase, repl in PHRASES.items():
            if re.search(rf"\b{re.escape(phrase)}\b", low):
                out.append(Finding(path, i, "E301", f"'{phrase}' -> use '{repl}'"))
        for w in re.findall(r"[A-Za-z']+", text):
            lw = w.lower()
            if lw in AVOID:
                out.append(Finding(path, i, "E301", f"'{w}' -> use '{AVOID[lw]}'"))
        if CONTRACTION.search(text):
            out.append(Finding(path, i, "E202", f"contraction: '{CONTRACTION.search(text).group(0)}'"))
        if LATIN.search(text):
            out.append(Finding(path, i, "W404", f"'{LATIN.search(text).group(0)}' -> write it in full words"))
        if is_table or is_heading:
            continue
        if ";" in text:
            out.append(Finding(path, i, "E201", "semicolon in prose: make two sentences"))
        for m in MODALS.finditer(text):
            out.append(Finding(path, i, "W401", f"'{m.group(0)}': use 'must', 'can', or a direct statement"))
        if PASSIVE.search(text):
            out.append(Finding(path, i, "W402", f"possible passive: '{PASSIVE.search(text).group(0)}'"))
        for m in ING.finditer(text):
            if m.group(0).lower() not in ING_OK:
                out.append(Finding(path, i, "W403", f"-ing form '{m.group(0)}': use a verb or a technical name"))
        sents = sentences(text)
        if not is_list:
            if para_sentences == 0:
                para_start = i
            para_sentences += len(sents)
        for s in sents:
            n = word_count(s)
            procedural = is_list and IMPERATIVE_START.match(s.strip()) is not None
            limit = MAX_PROCEDURAL if procedural else MAX_DESCRIPTIVE
            if n > limit:
                code = "E102" if procedural else "E101"
                out.append(Finding(path, i, code, f"{n} words (max {limit}): '{s[:70]}...'"))
    if para_sentences > 6:
        out.append(Finding(path, para_start, "W405", f"paragraph has {para_sentences} sentences (max 6)"))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="ASD-STE100 style linter (helper, not official)")
    p.add_argument("paths", nargs="+")
    p.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    p.add_argument("--quiet-warnings", action="store_true")
    args = p.parse_args()
    files: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        files.extend(sorted([*path.rglob("*.md"), *path.rglob("*.ipynb")]) if path.is_dir() else [path])
    findings = [f for path in files for f in lint_file(path)]
    errors = [f for f in findings if f.code.startswith("E")]
    warnings = [f for f in findings if f.code.startswith("W")]
    for f in errors + ([] if args.quiet_warnings else warnings):
        print(f)
    print(f"\n{len(files)} files, {len(errors)} errors, {len(warnings)} warnings")
    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
