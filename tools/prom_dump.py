"""Save the Prometheus range queries of a run window as JSON (06-experiments.md, rule 5).

The notebook makes the plots from these files, not from screenshots. The query list covers
vLLM, edge, the router, LMCache, the GPUs, the planner and KEDA, and the app.
Usage: python3 tools/prom_dump.py --start <epoch> --end <epoch> --out metrics/<run-id>/prom
       [--prom http://127.0.0.1:9090] [--step 5]
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

Q = {
    # vLLM (per pod)
    "vllm_running": "sum by (pod) (vllm:num_requests_running)",
    "vllm_waiting": "sum by (pod) (vllm:num_requests_waiting)",
    "vllm_kv_usage": "max by (pod) (vllm:kv_cache_usage_perc)",
    "vllm_preemptions_rate": "sum by (pod) (rate(vllm:num_preemptions_total[30s]))",
    "vllm_prompt_tokens_rate": "sum by (pod) (rate(vllm:prompt_tokens_total[30s]))",
    "vllm_generation_tokens_rate": "sum by (pod) (rate(vllm:generation_tokens_total[30s]))",
    "vllm_ttft_p95": "histogram_quantile(0.95, sum by (pod, le) (rate(vllm:time_to_first_token_seconds_bucket[1m])))",
    "vllm_ttft_p50": "histogram_quantile(0.50, sum by (pod, le) (rate(vllm:time_to_first_token_seconds_bucket[1m])))",
    "vllm_itl_p95": "histogram_quantile(0.95, sum by (pod, le) (rate(vllm:inter_token_latency_seconds_bucket[1m])))",
    "vllm_e2e_p95": "histogram_quantile(0.95, sum by (pod, le) (rate(vllm:e2e_request_latency_seconds_bucket[1m])))",
    "vllm_prefix_hit_ratio": "sum by (pod) (rate(vllm:prefix_cache_hits_total[1m])) / "
                             "sum by (pod) (rate(vllm:prefix_cache_queries_total[1m]))",
    "vllm_request_success_rate": "sum by (pod, finished_reason) (rate(vllm:request_success_total[1m]))",
    # NIXL hop (VERIFY the names at G0)
    "nixl_transfers_rate": "sum by (pod) (rate(vllm:nixl_xfer_time_seconds_count[1m]))",
    "nixl_bytes_rate": "sum by (pod) (rate(vllm:nixl_bytes_transferred_sum[1m]))",
    "nixl_xfer_p95": "histogram_quantile(0.95, sum by (pod, le) (rate(vllm:nixl_xfer_time_seconds_bucket[1m])))",
    # llm-d router v0.11 names (G0, 2026-09-28): llm_d_epp_*
    "flow_queue_by_priority": "sum by (priority) (llm_d_epp_flow_control_queue_size)",
    # Our orch_replica_queue_depth (handout L671): the queue of each pod as the router sees it.
    "router_pod_queue": "max by (model_server_endpoint) (llm_d_epp_per_endpoint_queue_size)",
    "flow_queue_by_tenant": "sum by (fairness_id) (llm_d_epp_flow_control_queue_size)",
    "flow_saturation": "max by (inference_pool, stage) (llm_d_epp_flow_control_pool_saturation)",
    "flow_queue_wait_p95": "histogram_quantile(0.95, sum by (priority, outcome, le) "
                           "(rate(llm_d_epp_flow_control_request_queue_duration_seconds_bucket[1m])))",
    "scheduler_attempts_rate": "sum by (endpoint_name, status) (rate(llm_d_epp_scheduler_attempts_total[1m]))",
    "pd_decisions_rate": "sum by (decision_type) (rate(llm_d_epp_disagg_decision_total[1m]))",
    "lmcache_hit_tokens_rate": "sum(rate(lmcache_mp_lookup_hit_tokens_total[1m]))",
    "lmcache_requested_tokens_rate": "sum(rate(lmcache_mp_lookup_requested_tokens_total[1m]))",
    "hami_vgpu_memory_used": "max by (exported_pod, device_uuid) (hami_vgpu_memory_used_bytes)",
    # edge
    "edge_requests_rate": "sum by (tenant, class, step) (rate(orch_requests_total[30s]))",
    "edge_sheds_rate": "sum by (reason, code, stage) (rate(orch_shed_total[30s]))",
    "edge_ttft_p95": "histogram_quantile(0.95, sum by (class, route, le) (rate(orch_ttft_seconds_bucket[1m])))",
    "edge_duration_p95": "histogram_quantile(0.95, sum by (class, route, le) "
                         "(rate(orch_request_duration_seconds_bucket[1m])))",
    "edge_guard_p95": "histogram_quantile(0.95, sum by (stage, le) (rate(orch_guard_seconds_bucket[1m])))",
    "edge_guard_rejects": "sum by (stage, reason) (increase(orch_guard_reject_total[1m]))",
    "edge_overflow_rate": "sum by (model, reason) (rate(orch_overflow_total[1m]))",
    # warm-controller and planner
    "worker_state": "max by (pod, state) (orch_worker_state)",
    "ramp_weight": "max by (pod) (orch_ramp_weight)",
    "first_ttft_mean": "sum by (pod, warm) (orch_first_ttft_seconds_sum) / "
                       "sum by (pod, warm) (orch_first_ttft_seconds_count)",
    "planner_desired": "max by (pool) (companion:planner_desired_replicas)",
    "deploy_replicas": "max by (deployment) (kube_deployment_status_replicas{namespace=\"companion\"})",
    # GPUs
    "gpu_util": "max by (Hostname, gpu, modelName) (DCGM_FI_DEV_GPU_UTIL)",
    "gpu_fb_used_mib": "max by (Hostname, gpu, modelName) (DCGM_FI_DEV_FB_USED)",
    "gpu_power_w": "max by (Hostname, gpu) (DCGM_FI_DEV_POWER_USAGE)",
    # app
    "app_turn_p95": "histogram_quantile(0.95, sum by (mode, phase, le) (rate(companion_turn_seconds_bucket[1m])))",
    "app_tool_p95": "histogram_quantile(0.95, sum by (tool, le) (rate(companion_tool_seconds_bucket[1m])))",
    "app_verdicts": "sum by (status, path) (increase(companion_verdicts_total[1m]))",
}


def get(prom: str, path: str, params: dict) -> dict:
    url = f"{prom}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prom", default="http://127.0.0.1:9090")
    ap.add_argument("--step", default="5")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    names = get(a.prom, "/api/v1/label/__name__/values", {})
    (out / "_metric_names.json").write_text(json.dumps(names.get("data", []), indent=1))
    failed = []
    for name, query in Q.items():
        try:
            data = get(a.prom, "/api/v1/query_range", {"query": query, "start": a.start, "end": a.end,
                                                         "step": a.step})
        except OSError as exc:
            failed.append(f"{name}: {exc}")
            continue
        (out / f"{name}.json").write_text(json.dumps({"query": query, **data}))
    print(f"saved {len(Q) - len(failed)} queries to {out}")
    for f in failed:
        print("FAILED", f)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
