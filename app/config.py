"""Settings for the Companion API, the ingest job, and the sweep job. Env prefix: APP_.

The LLM URL is `edge` only (H-34). The app has no router or vLLM URL.
"""

from __future__ import annotations

from functools import cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")

    # LLM path (product spec, section 9)
    edge_url: str = "http://edge.companion.svc.cluster.local:8080/v1"
    served_model: str = "companion"
    edge_api_key: str = "not-used"  # edge does not check it, the OpenAI client needs a value
    max_input_tokens: int = 30000  # the edge limit for prompt tokens
    max_output_tokens: int = 1024  # the edge clamp for interactive requests
    request_timeout_s: float = 120.0
    max_retries: int = 0  # sheds go to the user with the reason; the retry is the choice of the user
    tenant: str = "owner"
    allow_overflow: bool = True
    supports_images: bool = True

    # Budgets (product spec, section 5)
    quick_tool_rounds: int = 3
    quick_deadline_s: float = 20.0
    verified_deadline_s: float = 60.0  # SLO-4 (the owner, 2026-09-29: option B; 30 s timed out ~70% of checks)
    final_answer_by_s: float = 50.0
    max_fact_checks: int = 3
    max_tokens_quick: int = 900
    max_tokens_agent: int = 900
    max_tokens_verify: int = 300
    context_edit_trigger_tokens: int = 14000

    # Data plane (ADR-007)
    qdrant_url: str = "http://qdrant.data.svc.cluster.local:6333"
    collection: str = "bookmarks"
    sie_embed_url: str = "http://sie-embed.data.svc.cluster.local:8080"
    sie_ocr_url: str = "http://sie-ocr.data.svc.cluster.local:8080"
    embed_model: str = "BAAI/bge-m3"
    embed_dim: int = 1024
    rerank_model: str = "Qwen/Qwen3-Reranker-4B"
    ocr_model: str = "lightonai/LightOnOCR-2-1B"  # Gate G2 picks the OCR model
    sie_timeout_s: float = 30.0
    top_k: int = 8
    candidates: int = 30

    # Live web (ADR-009, product spec section 6)
    tavily_api_key: str = Field(default="", validation_alias=AliasChoices("APP_TAVILY_API_KEY", "TAVILY_API_KEY"))
    tavily_url: str = "https://api.tavily.com"
    search_max_results: int = 5
    search_cache_ttl_s: int = 24 * 3600
    browser_url: str = "http://browser.data.svc.cluster.local:8000"
    fetch_timeout_s: float = 8.0
    fetch_max_bytes: int = 2 * 1024 * 1024
    fetch_domain_interval_s: float = 1.0
    user_agent: str = "LearningCompanionBot/0.1 (private research agent)"
    fetch_allow_hosts: list[str] = []  # private hosts that the fetcher may open, for the D-12 test page
    ocr_min_chars: int = 500
    page_max_tokens: int = 1500

    # Page check (ADR-011, rule 2)
    injection_url: str = "http://guard-injection.guard.svc.cluster.local:8000"
    page_check_timeout_s: float = 5.0

    # State and traces
    redis_url: str = "redis://redis.platform.svc.cluster.local:6379/1"
    trace_path: str = "/data/traces/llm-calls.jsonl"  # FR-14. Empty: no trace file.
    capture_path: str = ""  # a capture run saves each LLM request body here (the replayer input)

    # Notion ingest (FR-01)
    notion_api_key: str = Field(default="", validation_alias=AliasChoices("APP_NOTION_API_KEY", "NOTION_API_KEY"))
    notion_data_source_id: str = "1449b9ed-d198-44ab-b6f4-e5c570d2e8fa"
    notion_version: str = "2025-09-03"


@cache
def get_settings() -> AppSettings:
    return AppSettings()
