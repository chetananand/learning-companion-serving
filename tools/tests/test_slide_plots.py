"""The slide charts come from the saved runs, and they show the same numbers as docs/results.md."""

from __future__ import annotations

import pytest

from tools import slide_plots

needs_runs = pytest.mark.skipif(not (slide_plots.proof.METRICS / "e9-decode").exists(), reason="no saved runs")


@needs_runs
def test_each_chart_is_written_with_the_numbers_of_the_report(tmp_path):
    slide_plots._style()
    path, e3 = slide_plots.e3(out=tmp_path)
    assert path.exists() and round(e3["2 x H100, 24 decode sequences"]["100%"]["P/D"], 2) == 4.59
    assert round(e3["2 x H100, 24 decode sequences"]["100%"]["colocated"], 2) == 0.84
    path, hop = slide_plots.hop(out=tmp_path)
    assert path.exists() and round(hop["LMCache server shared prefix"], 2) == 0.52
    path, warm = slide_plots.warm(out=tmp_path)
    assert path.exists() and round(warm["The ramp (a deleted pod comes back)"]["jump to r100"], 1) == 57.3
    path, e9 = slide_plots.e9(out=tmp_path)
    assert path.exists() and e9["decode"]["made_after_s"] == 15
