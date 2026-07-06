"""Reference VisionProvider backed by the Anthropic API.

Uses the plain vision message API (an image content block + a text
instruction asking for one JSON action back) rather than the "computer use"
beta tool, so the same action schema works if you swap in a different
vision-capable model/provider later -- see providers/base.py.
"""
from __future__ import annotations

import base64

from .base import Action, HistoryStep, VisionProvider, VALID_ACTIONS, parse_action_json

SYSTEM_PROMPT = """You are operating a real computer on behalf of a user, one step at a time.
You are shown a screenshot of the current screen and the task to accomplish.
Reply with EXACTLY ONE JSON object describing the next action -- nothing else,
no markdown fences, no commentary outside the JSON.

Screen resolution: {width}x{height}. Coordinates are pixels from the top-left.

Action schema (choose exactly one "action"):
  {{"action": "left_click", "x": int, "y": int, "reasoning": str}}
  {{"action": "right_click", "x": int, "y": int, "reasoning": str}}
  {{"action": "double_click", "x": int, "y": int, "reasoning": str}}
  {{"action": "move", "x": int, "y": int, "reasoning": str}}
  {{"action": "drag", "x": int, "y": int, "to_x": int, "to_y": int, "reasoning": str}}
  {{"action": "type", "text": str, "reasoning": str}}
  {{"action": "key", "keys": [str, ...], "reasoning": str}}   e.g. ["ctrl","c"] or ["Return"]
  {{"action": "scroll", "x": int, "y": int, "dx": int, "dy": int, "reasoning": str}}
  {{"action": "wait", "seconds": number, "reasoning": str}}
  {{"action": "done", "text": str}}        -- task is complete, text = summary for the user
  {{"action": "ask_user", "text": str}}    -- you need clarification or explicit permission before continuing

Rules:
- Always include "reasoning": a one-sentence explanation of why this action moves toward the goal.
- If the task would require entering credentials, making a payment, sending a message on the
  user's behalf, deleting data, or anything else with real-world consequences the user has not
  explicitly asked for, use "ask_user" and explain what you need confirmed instead of doing it.
- If you believe the task is complete, use "done", don't keep clicking around.
- One action per reply. You will be shown the result and a fresh screenshot before the next one.
"""


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
