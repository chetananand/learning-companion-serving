"""Build the quick agent (`create_agent`) and the verified Deep Agent (`create_deep_agent`).

Deep Agents settings from the 0.7.19 source (ADR-006):
1. `write_todos` is not a default tool, so the main agent gets LangChain `TodoListMiddleware`.
2. A harness profile for `openai:<served model>` removes `execute`, `glob`, `grep`, `write_file`,
   `edit_file`, and `delete`, and it turns off the default `general-purpose` subagent. The agents
   keep `ls` and `read_file` for large tool results.
3. The `fact-checker` returns a ClaimCheck through `ToolStrategy`. `FactCheckMiddleware`
   applies rule F2 to it.
"""

from __future__ import annotations

from typing import Any

from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, create_deep_agent, register_harness_profile
from langchain.agents import create_agent
from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    ModelCallLimitMiddleware,
    TodoListMiddleware,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_openai import ChatOpenAI

from app import prompts
from app.agent.middleware import FACT_CHECKER, AnswerGate, FactCheckMiddleware, FlattenSystem
from app.agent.schemas import ClaimCheck
from app.agent.tools import ANSWER_NOW, Toolbox
from app.config import AppSettings
from app.metrics import AppMetrics

EXCLUDED_TOOLS = frozenset({"execute", "glob", "grep", "write_file", "edit_file", "delete"})
AGENT_TOOL_ROUNDS = 6
VERIFY_MODEL_CALLS = 6


def register_profile(served_model: str) -> str:
    key = f"openai:{served_model}"
    register_harness_profile(key, HarnessProfile(
        excluded_tools=EXCLUDED_TOOLS,
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ))
    return key


def build_quick_agent(model: ChatOpenAI, toolbox: Toolbox, settings: AppSettings, metrics: AppMetrics,
                      checkpointer: Any = None) -> Any:
    return create_agent(
        model,
        tools=toolbox.quick_tools(),
        system_prompt=prompts.QUICK,
        middleware=[
            ContextEditingMiddleware(edits=[ClearToolUsesEdit(trigger=settings.context_edit_trigger_tokens, keep=2,
                                                              exclude_tools=(ANSWER_NOW,))]),
            AnswerGate(agent="quick", max_tool_rounds=settings.quick_tool_rounds,
                       answer_by_s=settings.quick_deadline_s / 2, metrics=metrics),
        ],
        checkpointer=checkpointer,
        name="quick",
    )


def fact_checker_spec(model: ChatOpenAI, toolbox: Toolbox) -> dict[str, Any]:
    return {
        "name": FACT_CHECKER,
        "description": "Checks one claim against the live web. Give the claim, its bookmark label, and the question.",
        "system_prompt": prompts.VERIFY,
        "tools": toolbox.verify_tools(),
        "model": model,
        "response_format": ToolStrategy(ClaimCheck),
        "middleware": [ModelCallLimitMiddleware(run_limit=VERIFY_MODEL_CALLS, exit_behavior="end"),
                       FlattenSystem()],
    }


def build_verified_agent(agent_model: ChatOpenAI, verify_model: ChatOpenAI, toolbox: Toolbox,
                         settings: AppSettings, metrics: AppMetrics) -> Any:
    register_profile(settings.served_model)
    return create_deep_agent(
        model=agent_model,
        tools=toolbox.agent_tools(),
        system_prompt=prompts.AGENT,
        subagents=[fact_checker_spec(verify_model, toolbox)],
        middleware=[
            TodoListMiddleware(system_prompt=prompts.TODO_PROMPT, tool_description=prompts.TODO_TOOL),
            FactCheckMiddleware(max_checks=settings.max_fact_checks, answer_by_s=settings.final_answer_by_s,
                                tiers=toolbox.tiers, metrics=metrics),
            AnswerGate(agent="agent", max_tool_rounds=AGENT_TOOL_ROUNDS, answer_by_s=settings.final_answer_by_s,
                       metrics=metrics),
            FlattenSystem(),
        ],
        name="verifier",
    )
