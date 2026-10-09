"""Tests of the Grafana image tool: URLs, run windows, images, and the panel data check (fake HTTP)."""

from __future__ import annotations

import json
import urllib.parse

from tools import grafana_shots as gs

PNG = b"\x89PNG\r\n\x1a\nfake"
DASH = {"uid": "companion-gateway", "title": "Gateway and admission", "panels": [
    {"id": 1, "title": "Sheds by reason", "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8},
     "targets": [{"expr": "sum by (reason) (rate(orch_sheds_total[1m]))"}]},
    {"id": 2, "title": "Queue wait p95", "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8},
     "targets": [{"expr": "empty_metric"}]},
    {"id": 3, "title": "Tenant windows", "gridPos": {"x": 0, "y": 8, "w": 12, "h": 8},
     "targets": [{"expr": "rate_limited_total"}, {"expr": "empty_metric"}]},
]}


def run_dir(tmp_path, start=1_800_000_000.0, end=1_800_000_600.0):
    d = tmp_path / "e2-soak"
    d.mkdir()
    (d / "window.json").write_text(json.dumps({"start": start, "end": end}))
    return d


def test_dashboard_and_panel_urls():
    url = gs.render_url("http://g:3000", DASH, 1_800_000_000, 1_800_000_600)
    parsed = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert parsed.path == "/render/d/companion-gateway/gateway-and-admission"
    assert q["from"] == "1800000000000" and q["to"] == "1800000600000" and q["kiosk"] == "true"
    assert q["height"] == str(16 * gs.CELL_PX + 60) and q["tz"] == "America/Los_Angeles"
    solo = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(
        gs.render_url("http://g:3000", DASH, 1, 2, panel_id=2)).query))
    assert "/render/d-solo/companion-gateway/" in gs.render_url("http://g:3000", DASH, 1, 2, panel_id=2)
    assert solo["panelId"] == "2" and "kiosk" not in solo


def test_run_window_from_window_json_or_client_calls(tmp_path):
    assert gs.run_window(run_dir(tmp_path)) == (1_800_000_000.0, 1_800_000_600.0)
    d = tmp_path / "old"
    d.mkdir()
    rows = [{"t_start": 1000.0, "latency_s": 5.0}, {"t_start": 1100.0, "latency_s": 20.0}]
    (d / "client.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert gs.run_window(d) == (940.0, 1180.0)


def test_render_writes_images_and_records_errors(tmp_path):
    d = run_dir(tmp_path)
    seen = []

    def get(url, headers, timeout):
        seen.append((url, headers))
        if "panelId=2" in url:
            return 500, "application/json", b'{"message":"Rendering failed"}'
        return 200, "image/png", PNG

    idx = gs.render(d, [DASH], base="http://g:3000", headers={"Authorization": "Basic x"}, panels=True, get=get)
    assert (d / "grafana" / "03-companion-gateway.png").read_bytes() == PNG
    walk = (d / "grafana" / "walk.md").read_text()
    assert "## Gateway + admission: sheds by reason" in walk and "(03-companion-gateway.png)" in walk
    assert (d / "grafana" / "panels" / "companion-gateway--01-sheds-by-reason.png").exists()
    assert len(idx["images"]) == 3 and len(idx["errors"]) == 1 and idx["errors"][0]["status"] == 500
    assert all(h == {"Authorization": "Basic x"} for _, h in seen)
    assert json.loads((d / "grafana" / "index.json").read_text())["from"] == 1_800_000_000.0


def test_render_rejects_a_page_that_is_not_a_png(tmp_path):
    d = run_dir(tmp_path)
    idx = gs.render(d, [DASH], base="http://g:3000", headers={}, get=lambda u, h, t: (200, "text/html", b"<html>"))
    assert idx["images"] == [] and idx["errors"][0]["type"] == "text/html"


def test_check_lists_the_panels_with_no_data(tmp_path):
    d = run_dir(tmp_path)

    def get(url, headers, timeout):
        expr = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["query"]
        result = [] if expr == "empty_metric" else [{"metric": {}, "values": [[1, "1"]]}]
        return 200, "application/json", json.dumps({"status": "success", "data": {"result": result}}).encode()

    r = gs.check(d, [DASH], prometheus="http://p:9090", get=get)
    assert r["panels"] == 3 and [x["panel"] for x in r["no_data"]] == ["Queue wait p95"]
    assert (d / "grafana" / "check.json").exists()


def test_session_runs_skip_rendered_runs(tmp_path):
    a, b = run_dir(tmp_path), tmp_path / "e3-c-50"
    b.mkdir()
    (b / "window.json").write_text("{}")
    (a / "grafana").mkdir()
    (a / "grafana" / "index.json").write_text("{}")
    assert gs.session_runs(tmp_path) == [b]


def test_the_walk_covers_every_handout_dashboard_in_order():
    dashboards = gs.load_dashboards()
    assert [d["uid"] for d in dashboards] == [uid for uid, _ in gs.HANDOUT_WALK]
    names = " | ".join(name for _, name in gs.HANDOUT_WALK)
    for item in ("Cluster", "Success and failures", "Gateway + admission", "Router", "Queue depth by pod", "vLLM",
                 "KV hop store", "Pods / replicas / KEDA"):  # the eight dashboards of handout Part 8
        assert item in names
    assert gs.image_name("companion-cluster") == "01-companion-cluster.png"


def test_the_real_dashboards_fit_the_tool():
    dashboards = gs.load_dashboards()
    assert len(dashboards) == 9 and len({d["uid"] for d in dashboards}) == 9
    for d in dashboards:
        assert d["title"] and d["panels"] and gs.height_px(d) > 300
        for p in d["panels"]:
            assert isinstance(p["id"], int) and p["gridPos"]["h"] > 0 and p["title"]
            assert p["targets"] and all(t["expr"] for t in p["targets"])
