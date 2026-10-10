"""The architecture slide: the steps are in order, each arrow touches two boxes, and the old labels are gone."""

from __future__ import annotations

from tools import arch_diagram as ad


def test_the_steps_are_in_order():
    assert [a.num for a in ad.ARROWS if a.num] == ["1", "2", "3", "4", "5", "5a", "5b", "5c", "6", "6"]


def test_only_the_prefill_leg_is_dashed():
    dashed = {a.num or a.text for a in ad.ARROWS if a.dashed}
    assert dashed == {"5a", "5b", "5c", "the prompt"}


def test_each_arrow_is_straight_and_ends_on_a_box_border():
    boxes = [*ad.BOXES, ad.LMC]

    def on_border(x: int, y: int) -> bool:
        return any((b.x <= x <= b.x + b.w and y in (b.y, b.y + b.h)) or
                   (b.y <= y <= b.y + b.h and x in (b.x, b.x + b.w)) for b in boxes)

    for a in ad.ARROWS:
        assert a.a[0] == a.b[0] or a.a[1] == a.b[1], a.text
        assert on_border(*a.a) and on_border(*a.b), a.text


def test_the_page_has_the_new_names_and_no_old_labels():
    page = ad.page()
    assert "Admission control + routing" in page and "LMCache server" in page
    for old in ("Gateway (our policy)", "gateway box", "qwen", "overflow API", "max 1 token", "LMCache tier",
                "the router", "llm-d router"):
        assert old not in page, old


def test_the_llm_d_box_shows_both_jobs():
    lines = " ".join(ad.ROUTER.lines)
    assert "admit (flow control)" in lines and "where (scheduler)" in lines
