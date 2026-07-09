import base64

import pytest
from secdogie_agent.providers.openai_provider import OpenAIProvider


class _FakeCompletions:
    def __init__(self, content, sink):
        self._content = content
        self._sink = sink

    def create(self, **kwargs):
        self._sink.append(kwargs)
        message = type("_Msg", (), {"content": self._content})()
        choice = type("_Choice", (), {"message": message})()
        return type("_Resp", (), {"choices": [choice]})()


class FakeClient:
    """Stands in for openai.OpenAI: records the create() kwargs and returns a
    canned assistant message."""

    def __init__(self, content):
        self.calls: list[dict] = []
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(content, self.calls)})()


def test_next_action_parses_json_and_builds_vision_message():
    client = FakeClient('{"action": "left_click", "x": 10, "y": 20, "reasoning": "the OK button"}')
    provider = OpenAIProvider(model="gpt-5.5", client=client, max_tokens=512)

    action = provider.next_action("do the thing", b"PNGBYTES", (1280, 720), [])

    assert action.kind == "left_click"
    assert action.x == 10 and action.y == 20

    kwargs = client.calls[0]
    assert kwargs["model"] == "gpt-5.5"
    # reasoning/GPT-5 models require max_completion_tokens, not max_tokens
    assert kwargs["max_completion_tokens"] == 512
    assert "max_tokens" not in kwargs

    system, user = kwargs["messages"]
    assert system["role"] == "system"
    assert "1280x720" in system["content"]  # screen size baked into the schema prompt
    assert user["role"] == "user"

    parts = {block["type"]: block for block in user["content"]}
    expected_b64 = base64.b64encode(b"PNGBYTES").decode("ascii")
    assert parts["image_url"]["image_url"]["url"] == f"data:image/png;base64,{expected_b64}"
    assert "do the thing" in parts["text"]["text"]


def test_next_action_rejects_unknown_action():
    client = FakeClient('{"action": "format_hard_drive"}')
    provider = OpenAIProvider(model="gpt-5.5", client=client)
    with pytest.raises(ValueError):
        provider.next_action("t", b"img", (100, 100), [])


def test_explain_task_returns_plain_text():
    client = FakeClient("  1. open the app\n2. click go  ")
    provider = OpenAIProvider(model="gpt-5.5", client=client)
    out = provider.explain_task("book a table", b"img", (800, 600))
    assert out == "1. open the app\n2. click go"


def test_explain_task_empty_is_none():
    client = FakeClient("   ")
    provider = OpenAIProvider(model="gpt-5.5", client=client)
    assert provider.explain_task("t", b"img", (800, 600)) is None
