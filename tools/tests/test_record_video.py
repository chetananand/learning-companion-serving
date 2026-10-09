"""Tests of the parts of tools/record_video.py that need no browser."""

import pytest

from tools.record_video import DONE, pick

QS = [{"id": "D-01", "text": "a", "mode": "quick"}, {"id": "D-03", "text": "b", "mode": "verified"},
      {"id": "D-08", "text": "c", "mode": "quick"}]


def test_pick_keeps_the_given_order_and_rejects_unknown_ids():
    assert [q["id"] for q in pick(QS, "D-08,D-01")] == ["D-08", "D-01"]
    assert pick(QS, "") == QS
    with pytest.raises(SystemExit):
        pick(QS, "D-99")


def test_the_done_label_of_the_ui():
    assert DONE.search("Done in 41234 ms") and not DONE.search("Working")
