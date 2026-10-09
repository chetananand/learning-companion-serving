"""The PDF of the deck uses the same images as the live deck: each asset has a repo file."""

from __future__ import annotations

from tools import deck_pdf, make_deck


def test_each_deck_asset_has_a_repo_file():
    assert set(make_deck.ASSETS) == set(make_deck.LOCAL)
    for blob, path in deck_pdf.blob_files().items():
        assert len(blob) == 32 and path.is_file(), path


def test_the_notes_have_each_slide_in_order():
    deck = make_deck.slides()
    notes = deck_pdf.notes_md(deck)
    assert notes.count("\n### ") == len(deck)
    assert notes.index("### 1. ") < notes.index("## Appendix") < notes.index("### A1. ")
