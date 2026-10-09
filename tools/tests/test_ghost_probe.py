"""Test the E7 ghost probe against the fake OpenAI server."""

from app.tests.fakes import FakeEdge, Reply
from tools import ghost_probe as gp


async def test_sessions_span_the_clear_and_their_history_grows():
    edge = FakeEdge(lambda call: Reply(content="one fact"))
    r = await gp.run("http://edge.test/v1", sessions=3, interval=0.05, duration=0.4, prefix_tokens=200,
                     capture=None, clear_at=0.2, transport=edge.transport())
    s = r["summary"]
    assert s["sessions"] == 3 and s["sessions_spanning_the_clear"] == 3 and s["turns_per_session_median"] >= 3
    assert s["before_clear"]["ok"] > 0 and s["after_clear"]["ok"] > 0
    by_session = {}
    for c in edge.calls:
        by_session.setdefault(c.headers["x-session-id"], []).append(len(c.body["messages"]))
    assert all(sizes == sorted(sizes) and sizes[-1] > sizes[0] for sizes in by_session.values())  # the history grows
    assert {c.headers["x-request-class"] for c in edge.calls} == {"interactive"}
