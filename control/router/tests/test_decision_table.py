"""Our should_shed and pick (handout Part 3 and Part 4) as a rule table, checked against the real config.

The rules live in production components: the Agent Router (tenant windows), the llm-d router (flow
control, saturation, scheduling profiles), and edge (slice_oom, the reject map, stay or leave).
This test keeps the table of the system design (sections 6.2 and 6.3) and the rendered config the same.
If a value changes in policy.yaml, the render, or edge, and not in the table, the test fails.
"""

from __future__ import annotations

import pytest

from control.edge.admit import ROUTER_REASONS, classify_upstream, slice_oom
from control.router.render import load_policy, render_agent_router, render_epp_config, tenant_windows

P = load_policy()
EPP = render_epp_config(P)
PLUGINS = {pl.get("name", pl["type"]): pl for pl in EPP["plugins"]}
DROPPED = "x-llm-d-request-dropped-reason"

# should_shed(req, snap) -> (shed, code, reason, retry_after_seconds): system design, section 6.2.
SHOULD_SHED = [
    # order, reason, where, code to the app
    (1, "tenant_tokens", "Agent Router", 429),
    (2, "tenant_requests", "Agent Router", 429),
    (3, "slice_oom", "edge", 413),
    (4, "no_signal", "llm-d router (stale metrics count as saturated)", 503),
    (5, "kv_free", "llm-d flow control, saturation detector", 503),
    (6, "timeout_queue", "llm-d flow control, band TTL", 503),
    (7, "p99_spread", "llm-d flow control, priority holdback", 503),
]

# The router drop reasons (llm-d v0.11.0) -> (our reason, may leave to the overflow).
REASON_MAP = {
    "rejected-saturated": ("kv_free", True),
    "rejected-ttl-expired": ("timeout_queue", True),
    "rejected-no-endpoints": ("no_endpoints", True),
    "evicted-priority": ("p99_spread", True),
    "evicted-queue-pressure": ("queue_pressure", True),
    "evicted": ("evicted", True),
    "rejected-shutting-down": ("router_shutdown", True),
    "rejected-context-cancelled": ("client_gone", False),
    "rejected-internal": ("router_internal", False),
}


def test_rules_1_and_2_tenant_windows_are_429_at_the_agent_router():
    _, btp = render_agent_router(P)
    rules = btp["spec"]["rateLimit"]["global"]["rules"]
    for tenant, window in tenant_windows(P).items():
        mine = [r for r in rules if r["clientSelectors"][0]["headers"][0]["value"] == tenant]
        tokens = next(r for r in mine if "cost" in r)
        requests = next(r for r in mine if "cost" not in r)
        assert tokens["limit"] == {"requests": window["tokens_per_minute"], "unit": "Minute"}
        assert requests["limit"] == {"requests": window["requests_per_minute"], "unit": "Minute"}
    # A tenant 429 has no router drop reason, so edge keeps 429 and the request never leaves.
    out = classify_upstream(429, {}, DROPPED)
    assert (out.status, out.may_leave) == (429, False)


def test_rule_3_slice_oom_is_413_and_stays():
    out = slice_oom(prompt_tokens=30000, max_tokens=4000, max_model_len=32768)
    assert out is not None and (out.status, out.reason, out.may_leave) == (413, "slice_oom", False)
    assert slice_oom(prompt_tokens=20000, max_tokens=1000, max_model_len=32768) is None


def test_rules_4_and_5_saturation_and_stale_metrics():
    det = PLUGINS["utilization-detector"]["parameters"]
    sat = P["flow_control"]["saturation"]
    assert det["kvCacheUtilThreshold"] == sat["kv_cache_util_threshold"] == 0.90
    assert det["queueDepthThreshold"] == sat["queue_depth_threshold"] == 5
    assert det["stalenessPolicy"] == "saturated"  # unknown is not idle (class7 rule H6)
    assert det["metricsStalenessThreshold"] == sat["metrics_staleness_threshold"] == "2s"


def test_rule_6_timeout_queue_band_ttls():
    bands = {b["priority"]: b["defaultRequestTTL"] for b in EPP["flowControl"]["priorityBands"]}
    assert bands == {10: "10s", 0: "120s"}  # interactive 10 s (half of its deadline), batch 120 s


def test_rule_7_interactive_first_under_saturation():
    assert EPP["flowControl"]["usageLimitPolicyPluginRef"] == "priority-holdback-policy"
    holdback = next(pl for pl in EPP["plugins"] if pl["type"] == "priority-holdback-policy")
    lo, hi = holdback["parameters"]["minCeiling"], holdback["parameters"].get("maxCeiling", 1.0)
    assert 0.0 <= lo < hi <= 1.0  # llm-d v0.11: minCeiling is required (the EPP refused to start without it)


def test_the_epp_may_load_the_alpha_label_producer():
    from control.router.render import render_router_values

    values = render_router_values(P, "config")
    assert any(pl["type"] == "label-producer" for pl in EPP["plugins"])  # Alpha in llm-d v0.11 (G0)
    assert values["router"]["epp"]["flags"]["allow-experimental-plugins"] is True


def test_the_tokenizer_sidecar_serves_the_name_that_the_epp_sends():
    from control.router.render import render_router_values

    extra = render_router_values(P, "config")["router"]["tokenizer"]["extraArgs"]
    names = extra[extra.index("--served-model-name") + 1:]
    assert P["served_model_name"] in names and P["model_name"] in names  # G0: a 404 stopped every P/D split
    prio = {b["objective"]: b["priority"] for b in P["flow_control"]["bands"]}
    assert prio["interactive"] > prio["batch"]


@pytest.mark.parametrize("raw,expected", sorted(REASON_MAP.items()))
def test_router_reasons_map_to_our_codes(raw, expected):
    assert ROUTER_REASONS[raw] == expected
    out = classify_upstream(429, {DROPPED: raw}, DROPPED)
    reason, may_leave = expected
    assert out.reason == reason and out.may_leave == may_leave
    assert out.status == (500 if raw == "rejected-internal" else 503 if may_leave else out.status)


def test_should_shed_table_covers_every_rule_once():
    assert [r[0] for r in SHOULD_SHED] == list(range(1, 8))
    assert {r[1] for r in SHOULD_SHED} >= {"tenant_tokens", "slice_oom", "kv_free", "timeout_queue", "p99_spread"}


# pick(req, workers, *, policy) -> Worker | Shed: system design, section 6.3.
PICK = {
    "decode": {"filters": ["decode-filter", "warm-filter"],
               "scorers": {"prefix-cache-scorer": 3, "session-affinity-scorer": 2, "queue-scorer": 2,
                           "kv-cache-utilization-scorer": 2, "ramp-scorer": 2}},
    "prefill": {"filters": ["prefill-filter", "warm-filter"],
                "scorers": {"prefix-cache-scorer": 3, "token-load-scorer": 2, "queue-scorer": 2, "ramp-scorer": 2}},
}


def _profile(cfg, name):
    prof = next(p for p in cfg["schedulingProfiles"] if p["name"] == name)
    refs = [(r["pluginRef"], r.get("weight")) for r in prof["plugins"]]
    return refs


@pytest.mark.parametrize("role", ["decode", "prefill"])
def test_pick_prefix_then_load(role):
    refs = _profile(EPP, role)
    assert [r for r, _ in refs[:2]] == PICK[role]["filters"]  # filters first: role, then the warm gate
    scorers = {r: w for r, w in refs[2:-1]}
    types = {r: PLUGINS[r]["type"] for r in scorers}
    named = {(types[r] if r != "ramp-scorer" else "ramp-scorer"): w for r, w in scorers.items()}
    assert named == PICK[role]["scorers"]
    assert "queue-scorer" in named  # queue depth is a scorer, not only an admit input (L649)
    assert refs[-1][0] == "picker" and PLUGINS["picker"]["type"] == "max-score-picker"


def test_pick_other_policies():
    least = render_epp_config({**P, "policy": "least_loaded"})
    for role in ("decode", "prefill"):
        scorers = {r for r, _ in _profile(least, role)[2:-1]}
        types = {next(pl for pl in least["plugins"] if pl.get("name", pl["type"]) == s)["type"] for s in scorers}
        assert types <= {"queue-scorer", "kv-cache-utilization-scorer", "endpoint-attribute-weight-scorer"}
    rnd = render_epp_config({**P, "policy": "random"})
    assert next(pl for pl in rnd["plugins"] if pl.get("name") == "picker")["type"] == "random-picker"


def test_no_bounce_the_route_has_no_retry():
    route, _ = render_agent_router(P)
    assert "retry" not in str(route).lower()  # the router picks once, the route never retries (L651)
