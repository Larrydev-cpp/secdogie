"""The minimal model interface this component needs, plus thin real adapters.

The agent's VisionProvider is action-oriented (screenshot -> one Action). 3D
analysis instead wants free-form structured description, so we use a smaller
contract: `describe(system, user, image?) -> text`. The image is optional so
the same interface serves both the per-view workers (image in) and the
aggregator (text-only fusion of their observations).

Adapters mirror secdogie_agent's providers so they talk to the same SDKs; the
pipeline logic depends only on the protocol, so tests inject a fake.
"""
from __future__ import annotations

import base64
from typing import Protocol

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_OPENAI_MODEL = "gpt-5.5"


class SceneModel(Protocol):
    def describe(self, system: str, user: str, image_png: bytes | None = None) -> str:
        """One vision (or text-only) turn: system + user (+ optional image) ->
        the model's text reply."""
        ...


class AnthropicSceneModel:
    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL, api_key: str | None = None,
                 max_tokens: int = 2048, client=None):
        if client is not None:
            self._client = client
        else:
            try:
                import anthropic
            except ImportError as e:
                raise RuntimeError("the 'anthropic' package is required: pip install anthropic") from e
            self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def describe(self, system: str, user: str, image_png: bytes | None = None) -> str:
        content: list = []
        if image_png is not None:
            b64 = base64.b64encode(image_png).decode("ascii")
            content.append(
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}
            )
        content.append({"type": "text", "text": user})
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")


class OpenAISceneModel:
    def __init__(self, model: str = DEFAULT_OPENAI_MODEL, api_key: str | None = None,
                 max_tokens: int = 2048, client=None):
        if client is not None:
            self._client = client
        else:
            try:
                import openai
            except ImportError as e:
                raise RuntimeError("the 'openai' package is required: pip install openai") from e
            self._client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
        self.model = model
        self.max_tokens = max_tokens

    def describe(self, system: str, user: str, image_png: bytes | None = None) -> str:
        content: list = [{"type": "text", "text": user}]
        if image_png is not None:
            b64 = base64.b64encode(image_png).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        resp = self._client.chat.completions.create(
            model=self.model,
            max_completion_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        return resp.choices[0].message.content or ""


def make_scene_model(provider: str, model: str, api_key: str | None) -> SceneModel:
    if provider == "anthropic":
        return AnthropicSceneModel(model=model, api_key=api_key)
    if provider == "openai":
        return OpenAISceneModel(model=model, api_key=api_key)
    raise ValueError(f"unknown provider {provider!r} (expected 'anthropic' or 'openai')")


def build_model_pool(provider: str, model: str, api_keys: list[str | None]) -> list[SceneModel]:
    """One SceneModel per API key -- the worker pool. Several keys let the
    concurrent workers spread load across keys instead of hammering one (the
    original motivation: high concurrency shouldn't trip one key's rate limit)."""
    if not api_keys:
        raise ValueError("build_model_pool needs at least one API key")
    return [make_scene_model(provider, model, key) for key in api_keys]
