"""Settings for `edge` (system design section 6). Env prefix: EDGE_."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class EdgeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDGE_")

    # Guard stage 1 (the rules)
    served_model: str = "companion"
    model_aliases: list[str] = ["companion"]
    tenants: list[str] = ["owner", "sweep", "noisy"]
    tenant_prefixes: list[str] = ["load-"]
    max_model_len: int = 32768
    max_prompt_tokens: int = 30000
    max_tokens_interactive: int = 1024
    max_tokens_batch: int = 2048
    max_images: int = 2
    max_image_bytes: int = 4 * 1024 * 1024
    image_tokens: int = 560  # Gemma 4 image budget (Gate G2 decides)
    max_tool_schema_tokens: int = 8000

    # Guard stage 2 (NeMo Guardrails `/v1/checks`)
    guard_enabled: bool = True
    guard_url: str = "http://guard.guard.svc.cluster.local:8000"
    injection_url: str = "http://guard-injection.guard.svc.cluster.local:8000"
    guard_config_id: str = "companion"
    guard_timeout_s: float = 1.0
    guard_cache_ttl_s: int = 3600

    # Upstream: the Agent Router, then the llm-d router
    upstream_url: str = "http://agent-router.gateway.svc.cluster.local"
    upstream_timeout_s: float = 600.0
    header_fairness: str = "x-llm-d-inference-fairness-id"
    header_objective: str = "x-llm-d-inference-objective"
    header_session: str = "x-session-id"
    header_dropped_reason: str = "x-llm-d-request-dropped-reason"
    header_ttl: str = "x-llm-d-inference-ttl"
    priority_interactive: int = 0
    priority_batch: int = 10

    # Overflow (ADR-008)
    overflow_enabled: bool = True
    overflow_url: str = "https://api.superlinked.com/v1"
    overflow_model: str = "qwen3.8-27b"
    overflow_api_key: str = ""
    overflow_rpm: int = 20
    overflow_tpm: int = 60000
    overflow_in_flight: int = 4
    overflow_usd_per_day: float = 15.0
    overflow_usd_per_1k_tokens: float = 0.002  # estimate until action A3 confirms the price
    overflow_p50_s: float = 4.0

    # State and logs
    redis_url: str = "redis://redis.platform.svc.cluster.local:6379/0"
    access_log_path: str = ""  # empty: JSON lines to stdout
