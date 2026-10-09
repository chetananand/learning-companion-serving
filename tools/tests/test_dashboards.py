

def test_the_gateway_shed_panels_use_the_edge_stage_names():
    """Session 2: both panels were always empty (a wrong stage filter, and an Envoy counter that we do not scrape)."""
    import json
    from pathlib import Path
    d = json.loads(Path(__file__).resolve().parents[2].joinpath(
        "cluster/manifests/base/monitoring/dashboards/companion-gateway.json").read_text())
    exprs = {p["title"]: p["targets"][0]["expr"] for p in d["panels"] if p.get("targets")}
    assert "stage='flow_control'" in exprs["Flow-control drops (edge view, by router reason)"]
    assert "stage='rate_limit'" in exprs["Tenant rate-limit rejects (Agent Router, edge view)"]
