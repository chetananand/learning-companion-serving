import datetime as dt

import pytest

from app.factcheck.resolve import (
    Finding,
    Source,
    Status,
    Tiers,
    Verdict,
    independent_count,
    normalize_value,
    resolve,
)

TODAY = dt.date(2026, 9, 27)
TIERS = Tiers(
    tier1_hosts=["docs.vllm.ai", "research.character.ai"],
    tier1_github_orgs=["vllm-project", "lmcache"],
    tier2_hosts=["uber.com", "nlpcloud.com", "planetscale.com"],
    deny_hosts=["spam.invalid"],
)


def src(url: str, date: dt.date | None, text: str = "") -> Source:
    return Source(url=url, date=date, text=text)


def run(bookmark: Source, *findings: Finding):
    return resolve(bookmark, list(findings), today=TODAY, tiers=TIERS)


BOOKMARK_2024_T2 = src("https://nlpcloud.com/deploy-mixtral-vllm.html", dt.date(2024, 3, 13))


def test_supported_only_is_verified():
    f = Finding(src("https://docs.vllm.ai/en/latest/", dt.date(2026, 9, 22)), Verdict.SUPPORTED)
    r = run(BOOKMARK_2024_T2, f)
    assert r.status is Status.VERIFIED and r.path == "supported" and r.winner == f


def test_not_found_and_unclear_are_not_verified():
    r = run(BOOKMARK_2024_T2,
            Finding(src("https://docs.vllm.ai/a", TODAY), Verdict.NOT_FOUND),
            Finding(src("https://docs.vllm.ai/b", TODAY), Verdict.UNCLEAR))
    assert r.status is Status.NOT_VERIFIED and r.path == "no_evidence"


def test_newer_better_tier_correction_wins():
    f = Finding(src("https://docs.vllm.ai/en/latest/", dt.date(2026, 9, 22)), Verdict.OUTDATED, "v0.30.0")
    r = run(BOOKMARK_2024_T2, f)
    assert r.status is Status.UPDATED and r.path == "tier" and r.winner == f


def test_older_page_never_wins_even_from_tier1():
    f = Finding(src("https://docs.vllm.ai/old", dt.date(2023, 1, 1)), Verdict.CONTRADICTED, "x")
    r = run(BOOKMARK_2024_T2, f)
    assert r.status is Status.DISPUTED and r.path == "no_rule"


def test_tier1_page_with_no_date_counts_as_current():
    f = Finding(src("https://github.com/vllm-project/vllm", None), Verdict.OUTDATED, "0.30.0")
    r = run(BOOKMARK_2024_T2, f)
    assert r.status is Status.UPDATED and r.path == "tier"


def test_lower_tier_page_with_no_date_never_wins():
    f = Finding(src("https://someblog.example/post", None), Verdict.CONTRADICTED, "x")
    r = run(BOOKMARK_2024_T2, f)
    assert r.status is Status.DISPUTED


def test_lower_tier_single_page_does_not_win():
    bookmark = src("https://research.character.ai/optimizing-inference/", dt.date(2024, 6, 21))  # tier 1
    f = Finding(src("https://forum.example/thread", dt.date(2026, 1, 5)), Verdict.CONTRADICTED, "int4")
    r = run(bookmark, f)
    assert r.status is Status.DISPUTED and r.path == "no_rule"


def test_two_independent_domains_win_even_at_lower_tier():
    bookmark = src("https://research.character.ai/optimizing-inference/", dt.date(2024, 6, 21))
    a = Finding(src("https://forum-a.com/t/1", dt.date(2026, 1, 5), "they moved to int4 weights in 2025"),
                Verdict.CONTRADICTED, "INT4")
    b = Finding(src("https://blog-b.org/p/2", dt.date(2026, 2, 1), "a different write-up about quantization"),
                Verdict.CONTRADICTED, "int4.")
    r = run(bookmark, a, b)
    assert r.status is Status.UPDATED and r.path == "two_domains"


def test_near_copies_count_once():
    text = "Character AI serves its models with int4 weights and a custom attention kernel since 2025"
    a = Finding(src("https://site-a.com/x", dt.date(2026, 1, 5), text), Verdict.CONTRADICTED, "int4")
    b = Finding(src("https://site-b.net/y", dt.date(2026, 1, 6), text), Verdict.CONTRADICTED, "int4")
    assert independent_count([a, b]) == 1
    bookmark = src("https://research.character.ai/optimizing-inference/", dt.date(2024, 6, 21))
    assert run(bookmark, a, b).status is Status.DISPUTED


def test_same_registered_domain_counts_once():
    a = Finding(src("https://blog.example.com/a", TODAY), Verdict.CONTRADICTED, "y")
    b = Finding(src("https://www.example.com/b", TODAY), Verdict.CONTRADICTED, "y")
    assert independent_count([a, b]) == 1


def test_newer_support_of_same_or_better_tier_blocks_a_correction():
    correction = Finding(src("https://uber.com/blog/a", dt.date(2025, 5, 1)), Verdict.CONTRADICTED, "y")
    support = Finding(src("https://docs.vllm.ai/x", dt.date(2026, 9, 1)), Verdict.SUPPORTED)
    r = run(BOOKMARK_2024_T2, correction, support)
    assert r.status is Status.DISPUTED and r.path == "blocked_by_support"


def test_older_support_does_not_block_a_correction():
    correction = Finding(src("https://docs.vllm.ai/new", dt.date(2026, 9, 1)), Verdict.OUTDATED, "0.30.0")
    support = Finding(src("https://docs.vllm.ai/old", dt.date(2024, 5, 1)), Verdict.SUPPORTED)
    r = run(BOOKMARK_2024_T2, correction, support)
    assert r.status is Status.UPDATED and r.winner == correction


def test_two_tier1_corrections_with_different_values_are_a_conflict():
    a = Finding(src("https://docs.vllm.ai/a", dt.date(2026, 9, 1)), Verdict.OUTDATED, "0.30.0")
    b = Finding(src("https://github.com/vllm-project/vllm/releases", dt.date(2026, 9, 2)), Verdict.OUTDATED, "0.29.0")
    r = run(BOOKMARK_2024_T2, a, b)
    assert r.status is Status.DISPUTED and r.path == "live_conflict"


def test_denied_domain_does_not_count():
    f = Finding(src("https://spam.invalid/x", TODAY), Verdict.CONTRADICTED, "y")
    r = run(BOOKMARK_2024_T2, f)
    assert r.status is Status.NOT_VERIFIED


@pytest.mark.parametrize(("url", "tier"), [
    ("https://docs.vllm.ai/en/latest", 1),
    ("https://github.com/vllm-project/vllm", 1),
    ("https://github.com/someone/fork", 3),
    ("https://www.uber.com/us/en/blog/protecting-against-retry-storms/", 2),
    ("https://unknown.example/page", 3),
    ("https://spam.invalid/x", None),
])
def test_tiers(url, tier):
    assert TIERS.tier(url) == tier


def test_normalize_value():
    assert normalize_value("v0.30.0") == normalize_value("0.30.0 ")
    assert normalize_value("INT4.") == "int4"


def test_the_new_timescaledb_docs_host_is_tier1():
    """Demo D-07: docs.timescale.com now redirects to tigerdata.com."""
    from app.factcheck.resolve import default_tiers
    t = default_tiers()
    assert t.tier("https://www.tigerdata.com/docs/timescaledb/latest/") == 1
    assert t.tier("https://docs.timescale.com/") == 1
