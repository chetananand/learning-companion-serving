import base64

import pytest

from control.edge.config import EdgeSettings
from control.edge.guard import (
    content_key,
    inspect,
    last_user_content,
    parse_checks_response,
)
from control.edge.tokens import estimate_tokens

S = EdgeSettings()
H = {"X-Tenant-Id": "owner", "X-Request-Class": "interactive"}
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"0" * 100).decode()


def run(payload, headers=H, settings=S):
    return inspect(payload, headers=headers, count_text=estimate_tokens, settings=settings)


def chat(content="What is continuous batching?", **extra):
    return {"model": "companion", "messages": [{"role": "user", "content": content}], **extra}


def test_valid_request_passes_and_gets_default_max_tokens():
    g = run(chat())
    assert g.ok and g.payload["max_tokens"] == S.max_tokens_interactive and g.tenant == "owner"


def test_model_not_in_allow_list():
    g = run(chat(model="gpt-4o"))
    assert (g.ok, g.status, g.reason) == (False, 400, "model_not_allowed")


@pytest.mark.parametrize("headers", [
    {"X-Request-Class": "interactive"},
    {"X-Tenant-Id": "mallory", "X-Request-Class": "interactive"},
    {"X-Tenant-Id": "owner", "X-Request-Class": "urgent"},
    {"X-Tenant-Id": "load-", "X-Request-Class": "batch"},
])
def test_bad_headers(headers):
    assert run(chat(), headers=headers).reason == "bad_headers"


def test_load_tenants_with_a_suffix_are_valid():
    assert run(chat(), headers={"X-Tenant-Id": "load-7", "X-Request-Class": "batch"}).ok


@pytest.mark.parametrize("value", [0, -5, "12", 1.5, True])
def test_bad_max_tokens(value):
    assert run(chat(max_tokens=value)).reason == "bad_max_tokens"


def test_max_tokens_is_clamped_by_class():
    assert run(chat(max_tokens=99999)).payload["max_tokens"] == S.max_tokens_interactive
    batch = {"X-Tenant-Id": "sweep", "X-Request-Class": "batch"}
    assert run(chat(max_tokens=99999), headers=batch).payload["max_tokens"] == S.max_tokens_batch


def test_max_completion_tokens_is_clamped_and_removed():
    g = run(chat(max_completion_tokens=5000))
    assert g.payload["max_tokens"] == S.max_tokens_interactive and "max_completion_tokens" not in g.payload


def test_empty_prompt():
    assert run(chat(content="   ")).reason == "empty_prompt"


def test_prompt_too_long():
    g = run(chat(content="x" * (4 * S.max_prompt_tokens + 100)))
    assert (g.status, g.reason) == (413, "prompt_too_long")


def image_msg(*urls):
    parts = [{"type": "text", "text": "What does this show?"}]
    parts += [{"type": "image_url", "image_url": {"url": u}} for u in urls]
    return {"model": "companion", "messages": [{"role": "user", "content": parts}]}


def test_image_rules():
    assert run(image_msg(PNG)).ok
    assert run(image_msg(PNG, PNG, PNG)).reason == "image_too_large"
    assert run(image_msg("https://example.com/cat.png")).reason == "bad_image"
    assert run(image_msg("data:image/gif;base64,R0lGOD")).reason == "bad_image"
    big = "data:image/png;base64," + "A" * (6 * 1024 * 1024)
    assert run(image_msg(big)).reason == "image_too_large"


def test_tool_rules():
    good = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}]
    assert run(chat(tools=good)).ok
    assert run(chat(tools=[{"type": "function", "function": {"name": "bad name!"}}])).reason == "bad_tools"
    assert run(chat(tools=[{"type": "retrieval"}])).reason == "bad_tools"
    huge = [{"type": "function", "function": {"name": "t", "description": "d" * 40000}}]
    assert run(chat(tools=huge)).reason == "bad_tools"


def test_last_user_content_takes_only_the_newest_user_message():
    payload = {"messages": [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "answer"},
        {"role": "tool", "content": "page text"},
        {"role": "user", "content": [{"type": "text", "text": "second"},
                                     {"type": "image_url", "image_url": {"url": PNG}}]},
    ]}
    text, images = last_user_content(payload)
    assert text == "second" and images == [PNG]


def test_content_key_depends_on_images():
    assert content_key("a", []) != content_key("a", [PNG])


def test_parse_checks_response():
    assert parse_checks_response({"status": "PASSED"}).ok
    blocked = parse_checks_response({"status": "blocked", "rail": "llama prompt guard injection"})
    assert (blocked.ok, blocked.status, blocked.reason) == (False, 400, "prompt_injection")
    unsafe = parse_checks_response({"status": "BLOCKED", "rail": "content safety check input"})
    assert unsafe.reason == "unsafe_content"
    with pytest.raises(ValueError):
        parse_checks_response({"status": "weird"})
