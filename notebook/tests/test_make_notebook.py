"""The Part 5 notebook has one section for each question H-65 to H-73."""

import importlib.util
from pathlib import Path


def test_notebook_sections():
    path = Path(__file__).resolve().parents[2] / "tools" / "make_notebook.py"
    spec = importlib.util.spec_from_file_location("make_notebook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    nb = mod.build()
    headings = [c.source.split(":")[0] for c in nb.cells if c.cell_type == "markdown" and c.source.startswith("## H-")]
    assert headings == [f"## H-{n}" for n in range(65, 74)]
    for cell in nb.cells:
        if cell.cell_type == "code":
            compile(cell.source, "<cell>", "exec")  # each cell is valid Python
