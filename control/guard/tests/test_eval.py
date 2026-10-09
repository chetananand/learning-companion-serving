"""Tests of the E17 helpers (the cluster parts run at the session)."""

import random

from control.guard.eval import inject, rates, user_turns


def test_user_turns_are_unique_with_some_attacks():
    turns = user_turns(200)
    assert len({t for t, _ in turns}) == 200
    assert 5 <= sum(a for _, a in turns) <= 40


def test_inject_puts_one_instruction_inside_the_page():
    text = "word " * 400
    out = inject(text, random.Random(1))
    assert len(out) > len(text) and out.strip() != text.strip()


def test_rates_per_threshold():
    r = rates([0.1, 0.2, 0.6], [0.9, 0.4, 0.95], [0.5])
    assert r == [{"threshold": 0.5, "false_positive_rate": 0.333, "miss_rate": 0.333}]
