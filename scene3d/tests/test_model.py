import pytest

from secdogie_scene3d.model import (
    AnthropicSceneModel,
    OpenAISceneModel,
    build_model_pool,
    detect_media_type,
    make_scene_model,
)

# Minimal real magic-byte prefixes for each format.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 8
GIF = b"GIF89a" + b"\x00" * 8
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "


# -- detect_media_type ---------------------------------------------------------

@pytest.mark.parametrize(
    "data, expected",
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (GIF, "image/gif"),
        (b"GIF87a" + b"\x00" * 8, "image/gif"),
        (WEBP, "image/webp"),
        (b"not an image at all", "image/png"),  # unrecognized -> png fallback
        (b"", "image/png"),  # empty -> no crash, png fallback
    ],
)
def test_detect_media_type(data, expected):
    assert detect_media_type(data) == expected


def test_detect_media_type_riff_but_not_webp_is_not_misread():
    # A RIFF container that isn't WebP (e.g. a WAV) must not be labeled webp.
    wav = b"RIFF\x00\x00\x00\x00WAVEfmt "
    assert detect_media_type(wav) == "image/png"


# -- adapters pass the detected media type through -----------------------------

class FakeAnthropicClient:
    def __init__(self):
        self.captured = None

        class _Messages:
            def create(_self, **kw):
                self.captured = kw

                class _Block:
                    type = "text"
                    text = "reply"

                class _Resp:
                    content = [_Block()]

                return _Resp()

        self.messages = _Messages()


class FakeOpenAIClient:
    def __init__(self):
        self.captured = None

        class _Completions:
            def create(_self, **kw):
                self.captured = kw

                class _Msg:
                    content = "reply"

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_anthropic_adapter_labels_jpeg_correctly():
    client = FakeAnthropicClient()
    model = AnthropicSceneModel(client=client)
    model.describe("sys", "look", JPEG)
    image_block = client.captured["messages"][0]["content"][0]
    assert image_block["source"]["media_type"] == "image/jpeg"


def test_openai_adapter_labels_gif_correctly():
    client = FakeOpenAIClient()
    model = OpenAISceneModel(client=client)
    model.describe("sys", "look", GIF)
    image_block = client.captured["messages"][1]["content"][1]
    assert image_block["image_url"]["url"].startswith("data:image/gif;base64,")


def test_adapter_text_only_turn_sends_no_image():
    client = FakeAnthropicClient()
    model = AnthropicSceneModel(client=client)
    model.describe("sys", "just text", None)
    content = client.captured["messages"][0]["content"]
    assert all(block["type"] != "image" for block in content)


# -- factory / pool ---------------------------------------------------------

def test_make_scene_model_rejects_unknown_provider():
    with pytest.raises(ValueError):
        make_scene_model("nope", "m", None)


def test_build_model_pool_requires_a_key():
    with pytest.raises(ValueError):
        build_model_pool("anthropic", "m", [])
