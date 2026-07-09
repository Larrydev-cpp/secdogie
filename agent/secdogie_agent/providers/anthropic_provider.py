"""Reference VisionProvider backed by the Anthropic API.

Uses the plain vision message API (an image content block + a text
instruction asking for one JSON action back) rather than the "computer use"
beta tool, so the same action schema works if you swap in a different
vision-capable model/provider later -- see providers/base.py.
"""
from __future__ import annotations

import base64

from .base import VALID_ACTIONS, Action, HistoryStep, VisionProvider, parse_action_json
from .prompts import BRIEFING_PROMPT, SYSTEM_PROMPT


class AnthropicProvider(VisionProvider):
    def __init__(self, model: str = "claude-sonnet-5", api_key: str | None = None, max_tokens: int = 1024):
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError(
                "the 'anthropic' package is required for AnthropicProvider: pip install anthropic"
            ) from e
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def next_action(
        self,
        task: str,
        screenshot_png: bytes,
        screen_size: tuple[int, int],
        history: list[HistoryStep],
    ) -> Action:
        width, height = screen_size
        system = SYSTEM_PROMPT.format(width=width, height=height)

        history_text = "\n".join(
            f"- did {h.action.kind}({h.action.raw}) -> {h.result}" for h in history[-10:]
        )
        user_text = f"Task: {task}\n"
        if history_text:
            user_text += f"\nActions so far:\n{history_text}\n"
        user_text += "\nHere is the current screenshot. Respond with the next action's JSON only."

        b64 = base64.b64encode(screenshot_png).decode("ascii")
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": b64},
                        },
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        data = parse_action_json(text)
        if data.get("action") not in VALID_ACTIONS:
            raise ValueError(f"model returned an unrecognized action: {data!r}")
        return Action.from_dict(data)

    def explain_task(
        self,
        task: str,
        screenshot_png: bytes,
        screen_size: tuple[int, int],
    ) -> str | None:
        b64 = base64.b64encode(screenshot_png).decode("ascii")
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=BRIEFING_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": b64},
                        },
                        {"type": "text", "text": f"Task: {task}"},
                    ],
                }
            ],
        )
        return "".join(block.text for block in response.content if block.type == "text").strip() or None
