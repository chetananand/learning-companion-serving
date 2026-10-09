"""Grafana dashboards as code (system design 10.3, H-87 to H-94).

`python3 tools/dashboards.py` writes one JSON file for each dashboard into
cluster/manifests/base/monitoring/dashboards/. Kustomize puts them into ConfigMaps with the label
`grafana_dashboard: "1"`, and the Grafana sidecar of kube-prometheus-stack loads them.

Metric names: vLLM, DCGM, KEDA, and kube-state-metrics are stable. Our own metrics come from edge,
warm-controller, and the Companion API. The EPP names come from the Inference Extension v1.5.0 docs.
The llm-d P/D decision, LMCache, and rate-limit panels use name patterns. VERIFY them at G0 with
metrics/<run>/prom/_metric_names.json, then change the patterns into exact names.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "cluster/manifests/base/monitoring/dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}


def q(expr: str, legend: str = "") -> dict:
    return {"expr": expr, "legendFormat": legend, "datasource": DS, "refId": "A"}


def panel(title: str, targets: list[dict], unit: str = "short", kind: str = "timeseries", stack: bool = False,
          desc: str = "") -> dict:
    p = {"type": kind, "title": title, "description": desc, "datasource": DS,
         "targets": [{**t, "refId": chr(65 + i)} for i, t in enumerate(targets)],
         "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 1, "fillOpacity": 10,
                                                                  "stacking": {"mode": "normal" if stack else "none"}}},
                         "overrides": []},
         "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]},
                     "tooltip": {"mode": "multi"}}}
    return p


def dashboard(uid: str, title: str, panels: list[dict], per_row: int = 2) -> dict:
    w, h = 24 // per_row, 8
    for i, p in enumerate(panels):
        p["id"] = i + 1
        p["gridPos"] = {"x": (i % per_row) * w, "y": (i // per_row) * h, "w": w, "h": h}
    return {"uid": uid, "title": title, "tags": ["companion"], "timezone": "utc", "schemaVersion": 39,
            "refresh": "5s", "time": {"from": "now-30m", "to": "now"}, "panels": panels,
            "templating": {"list": []}, "annotations": {"list": []}}


def hq(metric: str, by: str, quantile: float = 0.95, window: str = "1m") -> str:
    return f"histogram_quantile({quantile}, sum by ({by}, le) (rate({metric}_bucket[{window}])))"


DASHBOARDS = [
    dashboard("companion-cluster", "Cluster", [
        panel("Node CPU used (cores)", [q("sum by (instance) (rate(node_cpu_seconds_total{mode!='idle'}[1m]))",
                                          "{{instance}}")]),
        panel("Node memory used", [q("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "{{instance}}")],
              "bytes"),
        panel("Pods by phase", [q("sum by (phase) (kube_pod_status_phase{namespace=~'companion|guard|data'})",
                                  "{{phase}}")], stack=True),
        panel("GPU use", [q("max by (Hostname, gpu, modelName) (DCGM_FI_DEV_GPU_UTIL)", "{{Hostname}} gpu{{gpu}}")],
              "percent"),
        panel("GPU framebuffer used", [q("max by (Hostname, gpu) (DCGM_FI_DEV_FB_USED) * 1024 * 1024",
                                         "{{Hostname}} gpu{{gpu}}")], "bytes"),
        panel("GPU power", [q("max by (Hostname, gpu) (DCGM_FI_DEV_POWER_USAGE)", "{{Hostname}} gpu{{gpu}}")],
              "watt"),
        panel("HAMi slices: GPU memory limit by pod", [q("max by (exported_pod, device_uuid) "
                                                         "(hami_vgpu_memory_limit_bytes)",
                                                         "{{exported_pod}}")], "bytes",
              desc="HAMi scheduler metric. VERIFY the name at G0."),
    ]),
    dashboard("companion-success", "Success and failures", [
        panel("Requests at edge by class", [q("sum by (class) (rate(orch_requests_total[30s]))", "{{class}}")],
              "reqps"),
        panel("Sheds by code and reason", [q("sum by (code, reason) (rate(orch_shed_total[30s]))",
                                             "{{code}} {{reason}}")], "reqps", stack=True),
        panel("Overflow (Superlinked)", [q("sum by (reason) (rate(orch_overflow_total[1m]))", "{{reason}}"),
                                         q("sum by (why) (rate(orch_overflow_refused_total[1m]))", "refused {{why}}")],
              "reqps"),
        panel("Success ratio (no shed)", [q("1 - sum(rate(orch_shed_total[1m])) / sum(rate(orch_requests_total[1m]))",
                                            "success")], "percentunit"),
        panel("Completed on vLLM by reason", [q("sum by (finished_reason) (rate(vllm:request_success_total[1m]))",
                                                "{{finished_reason}}")], "reqps", stack=True),
        panel("App turns by outcome", [q("sum by (mode, outcome) (rate(companion_turns_total[1m]))",
                                         "{{mode}} {{outcome}}")], "reqps"),
    ]),
    dashboard("companion-gateway", "Gateway and admission", [
        panel("Guard rejects by stage and reason", [q("sum by (stage, reason) (rate(orch_guard_reject_total[1m]))",
                                                      "stage {{stage}} {{reason}}")], "reqps", stack=True),
        panel("Guard stage 2 latency p95 (SLO-7: 0.3 s)", [q(hq("orch_guard_seconds", "stage"), "stage {{stage}}")],
              "s"),
        # Session 2: we do not scrape an Envoy rate-limit counter, so this panel was always empty. The edge counts each
        # 429 of the Agent Router token window (orch_shed_total, stage rate_limit, reason tenant_tokens).
        panel("Tenant rate-limit rejects (Agent Router, edge view)",
              [q("sum by (reason) (rate(orch_shed_total{stage='rate_limit'}[1m]))", "{{reason}}")], "reqps",
              desc="The 429s of the Agent Router token windows, as the edge counts them."),
        panel("Flow-control drops (edge view, by router reason)",
              # The edge stage name (session 2).
              [q("sum by (reason) (rate(orch_shed_total{stage='flow_control'}[1m]))", "{{reason}}")], "reqps",
              stack=True),
        panel("edge TTFT p95 by class and route", [q(hq("orch_ttft_seconds", "class, route"), "{{class}} {{route}}")],
              "s"),
        panel("edge request duration p95", [q(hq("orch_request_duration_seconds", "class, route"),
                                              "{{class}} {{route}}")], "s"),
    ]),
    dashboard("companion-router", "Router", [
        # llm-d router v0.11 names (G0, 2026-09-28): llm_d_epp_*, not the Inference Extension names.
        panel("Scheduling attempts by pod", [q("sum by (endpoint_name, status) "
                                               "(rate(llm_d_epp_scheduler_attempts_total[1m]))",
                                               "{{endpoint_name}} {{status}}")], "reqps", stack=True),
        panel("Pool saturation (1.0 = backpressure)", [q("max by (inference_pool, stage) "
                                                         "(llm_d_epp_flow_control_pool_saturation)",
                                                         "{{inference_pool}} {{stage}}")], "percentunit"),
        panel("P/D decisions", [q("sum by (decision_type) (rate(llm_d_epp_disagg_decision_total[1m]))",
                                  "{{decision_type}}")], "reqps", desc="The llm-d P/D decider: split or not."),
        panel("Prefix-cache hit ratio (vLLM)", [q("sum by (pod) (rate(vllm:prefix_cache_hits_total[1m])) / "
                                                  "sum by (pod) (rate(vllm:prefix_cache_queries_total[1m]))",
                                                  "{{pod}}")], "percentunit"),
        panel("Prefix-cache hit ratio for each pod (it drops after a cache clear, E7)",
              [q("sum by (pod) (rate(vllm:prefix_cache_hits_total[30s])) / "
                 "sum by (pod) (rate(vllm:prefix_cache_queries_total[30s]))", "{{pod}}")], "percentunit",
              desc="The ghost count comes from the Envoy access log: tools/ghosts.py."),
        panel("Plugin latency p95", [q(hq("llm_d_epp_plugin_duration_seconds", "plugin_type"),
                                       "{{plugin_type}}")], "s"),
    ]),
    dashboard("companion-queues", "Queue depth by pod", [
        panel("Flow-control queue by band (before the pick)",
              [q("sum by (priority) (llm_d_epp_flow_control_queue_size)", "priority {{priority}}")],
              stack=True),
        panel("Flow-control queue by tenant", [q("sum by (fairness_id) (llm_d_epp_flow_control_queue_size)",
                                                 "{{fairness_id}}")], stack=True),
        panel("Queue depth for each pod, router view (our orch_replica_queue_depth)",
              [q("max by (model_server_endpoint) (llm_d_epp_per_endpoint_queue_size)", "{{model_server_endpoint}}")]),
        panel("vLLM waiting by pod (after the pick)", [q("sum by (pod) (vllm:num_requests_waiting)", "{{pod}}")]),
        panel("vLLM running by pod", [q("sum by (pod) (vllm:num_requests_running)", "{{pod}}")]),
        panel("Queue wait p95 by outcome", [q(hq("llm_d_epp_flow_control_request_queue_duration_seconds",
                                                 "priority, outcome"), "prio {{priority}} {{outcome}}")], "s"),
        panel("Pool average queue size", [q("max(llm_d_epp_average_queue_size)", "average")]),
    ]),
    dashboard("companion-vllm", "vLLM", [
        panel("KV cache use", [q("max by (pod) (vllm:kv_cache_usage_perc)", "{{pod}}")], "percentunit"),
        panel("Preemptions", [q("sum by (pod) (rate(vllm:num_preemptions_total[1m]))", "{{pod}}")], "ops"),
        panel("TTFT p95 (SLO-1: 1.5 s)", [q(hq("vllm:time_to_first_token_seconds", "pod"), "{{pod}}")], "s"),
        panel("ITL p95 (SLO-2: 50 ms)", [q(hq("vllm:inter_token_latency_seconds", "pod"), "{{pod}}")], "s"),
        panel("End-to-end latency p95", [q(hq("vllm:e2e_request_latency_seconds", "pod"), "{{pod}}")], "s"),
        panel("Tokens per second", [q("sum by (pod) (rate(vllm:prompt_tokens_total[1m]))", "prompt {{pod}}"),
                                    q("sum by (pod) (rate(vllm:generation_tokens_total[1m]))", "output {{pod}}")]),
    ]),
    dashboard("companion-hop", "Hop store", [
        # G0 (2026-09-28): rate() over many names drops __name__, so the series collided (HTTP 422).
        panel("NIXL transfers", [q("sum by (pod) (rate(vllm:nixl_xfer_time_seconds_count[1m]))", "{{pod}}")], "ops",
              desc="Transfers each second on the pod that pulls the KV (the decode pod)."),
        panel("NIXL transfer time p95", [q("histogram_quantile(0.95, sum by (pod, le) "
                                           "(rate({__name__=~'vllm:nixl_xfer_time_seconds_bucket'}[1m])))",
                                           "{{pod}}")], "s"),
        panel("Failed and expired transfers",
              [q("sum by (pod, __name__) ({__name__=~'vllm:nixl_num_(failed|kv_expired).*_total'})",  # not _created
                 "{{pod}} {{__name__}}")]),
        panel("NIXL bytes each second", [q("sum by (pod) (rate(vllm:nixl_bytes_transferred_sum[1m]))", "{{pod}}")],
              "Bps", desc="The hop records per request come from the Envoy access log (tools/hop_records.py)."),
        panel("LMCache lookups: hit and requested tokens", [
            q("sum(rate(lmcache_mp_lookup_hit_tokens_total[1m]))", "hit tokens"),
            q("sum(rate(lmcache_mp_lookup_requested_tokens_total[1m]))", "requested tokens"),
            q("sum(rate(lmcache_mp_num_finished_stores_total[1m]))", "stores")], "short",
            desc="The LMCache MP server (lmcache-server PodMonitor), names from G0."),
        panel("LMCache CPU tier use", [q("sum(lmcache_mp_l1_memory_usage_bytes)", "L1 bytes")], "bytes"),
    ]),
    dashboard("companion-scaling", "Pods, replicas, KEDA", [
        panel("Desired against actual replicas", [q("max by (pool) (companion:planner_desired_replicas)",
                                                    "desired {{pool}}"),
                                                  q("max by (deployment) (kube_deployment_status_replicas_available"
                                                    "{namespace='companion', deployment=~'vllm-.*'})",
                                                    "available {{deployment}}")]),
        panel("Worker states", [q("sum by (state) (orch_worker_state)", "{{state}}")], stack=True),
        panel("Warmup time p95", [q(hq("orch_warmup_seconds", "phase", window="10m"), "{{phase}}")], "s"),
        panel("Ramp weight by pod", [q("max by (pod) (orch_ramp_weight)", "{{pod}}")], "percentunit"),
        panel("First TTFT: cold against warm", [q(hq("orch_first_ttft_seconds", "warm", 0.5, "10m"), "warm={{warm}}")],
              "s"),
        panel("KEDA scaler metric", [q("max by (scaledObject, metric) (keda_scaler_metrics_value)",
                                       "{{scaledObject}} {{metric}}")]),
    ]),
    dashboard("companion-app", "App", [
        panel("Turn latency p95 by phase (SLO-3, SLO-4)", [q(hq("companion_turn_seconds", "mode, phase"),
                                                             "{{mode}} {{phase}}")], "s"),
        panel("Tool latency p95", [q(hq("companion_tool_seconds", "tool"), "{{tool}}")], "s"),
        panel("Tool errors by reason", [q("sum by (tool, reason) (rate(companion_tool_errors_total[5m]))",
                                          "{{tool}} {{reason}}")], "ops", stack=True),
        panel("Verdicts by status and rule path", [q("sum by (status, path) (increase(companion_verdicts_total[10m]))",
                                                     "{{status}} {{path}}")], stack=True),
        panel("Answer gate decisions", [q("sum by (agent, reason) (increase(companion_answer_gate_total[10m]))",
                                          "{{agent}} {{reason}}")], stack=True),
        panel("Page check: removed windows", [q("sum by (tool) (increase(companion_page_check_removed_total[10m]))",
                                                "{{tool}}")]),
    ]),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for d in DASHBOARDS:
        (OUT / f"{d['uid']}.json").write_text(json.dumps(d, indent=1) + "\n")
    print(f"wrote {len(DASHBOARDS)} dashboards to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
