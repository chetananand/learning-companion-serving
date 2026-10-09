"""Tests of the raw /metrics filter (tools/scrape_now.py)."""

from tools.scrape_now import keep, metrics_port

TEXT = """# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="companion"} 3.0
# HELP python_gc_objects_collected_total Objects collected during gc
# TYPE python_gc_objects_collected_total counter
python_gc_objects_collected_total{generation="0"} 1234.0
# HELP orch_shed_total Sheds by stage and reason
orch_shed_total{stage="flow_control",reason="timeout_queue"} 11.0
"""


def test_keep_only_our_families():
    out = keep(TEXT, ("vllm:", "orch_"))
    assert "vllm:num_requests_running{" in out and "# TYPE vllm:num_requests_running gauge" in out
    assert "orch_shed_total{stage=" in out and "# HELP orch_shed_total" in out
    assert "python_gc" not in out


def test_metrics_port_by_name_or_9090():
    pod = {"spec": {"containers": [{"ports": [{"name": "grpc", "containerPort": 9002}]},
                                   {"ports": [{"name": "metrics", "containerPort": 9090}]}]}}
    assert metrics_port(pod) == 9090
    assert metrics_port({"spec": {"containers": [{"ports": [{"name": "x", "containerPort": 1}]}]}}) is None
