"""Test the E14 queue-order tool against the fake OpenAI server."""

from app.tests.fakes import FakeEdge, Reply
from tools import queue_order as qo


async def test_rounds_send_one_retrieve_and_five_steps_with_their_classes():
    edge = FakeEdge(lambda call: Reply(content="ok done"))
    result = await qo.run("http://edge.test/v1", "batch", rounds=2, capture=None, tokens=2000,
                          transport=edge.transport())
    assert len(result["rounds"]) == 2 and all(len(r) == 6 for r in result["rounds"])
    classes = sorted(c.headers["x-request-class"] for c in edge.calls)
    assert classes.count("batch") == 2 and classes.count("interactive") == 10
    s = result["summary"]
    assert s["retrieve_32k"]["n"] == 2 and s["agent_step"]["n"] == 10
    assert sum(s["first_token_went_to"].values()) == 2
    long_calls = [c for c in edge.calls if c.headers["x-request-class"] == "batch"]
    assert len(long_calls[0].body["messages"][0]["content"]) > 7000  # the long retrieve


async def test_a_tool_call_answer_counts_as_the_first_token():
    """Session 2: the agent steps answer with a tool call first, and a check of content alone gave n = 0."""
    edge = FakeEdge(lambda call: Reply(tool_calls=[("web_search", {"query": "vllm 0.30"})]))
    result = await qo.run("http://edge.test/v1", "interactive", rounds=1, capture=None, tokens=2000,
                          transport=edge.transport())
    s = result["summary"]
    assert s["retrieve_32k"]["n"] == 1 and s["agent_step"]["n"] == 5
