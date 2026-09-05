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


def test_next_action_omits_the_image_block_when_screenshot_is_none():
    client = FakeClient('{"action": "click_element", "element": "e1", "reasoning": "Save"}')
    provider = OpenAIProvider(model="gpt-5.5", client=client)
    action = provider.next_action("save it", None, (1280, 720), [])
    assert action.kind == "click_element" and action.element == "e1"
    parts = {block["type"]: block for block in client.calls[0]["messages"][1]["content"]}
    assert "image_url" not in parts
    assert "No screenshot this step" in parts["text"]["text"]


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


def test_complete_falls_back_to_max_tokens_when_new_param_rejected():
    class FlipClient:
        def __init__(self, content):
            self.calls = []
            self._content = content

            class _Comp:
                def __init__(self, outer):
                    self._outer = outer

                def create(self, **kwargs):
                    self._outer.calls.append(kwargs)
                    if "max_completion_tokens" in kwargs:
                        raise RuntimeError("Unsupported parameter: 'max_completion_tokens'")
                    message = type("_Msg", (), {"content": self._outer._content})()
                    choice = type("_Choice", (), {"message": message})()
                    return type("_Resp", (), {"choices": [choice]})()

            self.chat = type("_Chat", (), {"completions": _Comp(self)})()

    client = FlipClient('{"action": "done", "text": "ok"}')
    provider = OpenAIProvider(model="openai/gpt-4o", client=client, max_tokens=256)
    action = provider.next_action("t", None, (100, 100), [])
    assert action.kind == "done"
    assert len(client.calls) == 2
    assert "max_completion_tokens" in client.calls[0]
    assert client.calls[1]["max_tokens"] == 256
