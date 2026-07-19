"""VisionProvider backed by the OpenAI Chat Completions vision API.

Sibling to AnthropicProvider: same action schema (see providers/prompts.py),
different transport. It sends the screenshot as an `image_url` data URL plus a
text instruction and asks for one JSON action back, so the agent loop is
identical no matter which model answered. Works with any OpenAI-compatible
chat endpoint that accepts image inputs (set `base_url` via the client).
"""
from __future__ import annotations

import base64

from .base import VALID_ACTIONS, Action, HistoryStep, VisionProvider, parse_action_json, parse_plan
from .prompts import BRIEFING_PROMPT, CHECK_PROMPT, PLAN_PROMPT, SYSTEM_PROMPT


class OpenAIProvider(VisionProvider):
    def __init__(
        self,
        model: str = "gpt-5.5",
        api_key: str | None = None,
        max_tokens: int = 1024,
        client=None,
    ):
        # `client` lets tests inject a fake; production builds one from the SDK.
        if client is not None:
            self._client = client
        else:
            try:
                import openai
            except ImportError as e:
                raise RuntimeError(
                    "the 'openai' package is required for OpenAIProvider: "
                    "pip install 'secdogie-agent[openai]' (or: pip install openai)"
                ) from e
            self._client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
        self.model = model
        self.max_tokens = max_tokens

    def _complete(self, system: str, user_text: str, screenshot_png: bytes) -> str:
        """One vision turn: system + (text, image) -> assistant text."""
        b64 = base64.b64encode(screenshot_png).decode("ascii")
        response = self._client.chat.completions.create(
            model=self.model,
            # Reasoning/GPT-5-era models require max_completion_tokens; max_tokens
            # is rejected by them, so we use the current canonical parameter.
            max_completion_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                },
            ],
        )
        return response.choices[0].message.content or ""

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

        text = self._complete(system, user_text, screenshot_png)
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
        text = self._complete(BRIEFING_PROMPT, f"Task: {task}", screenshot_png)
        return text.strip() or None

    def plan_task(
        self,
        task: str,
        screenshot_png: bytes,
        screen_size: tuple[int, int],
    ) -> list[str] | None:
        text = self._complete(PLAN_PROMPT, f"Task: {task}", screenshot_png)
        return parse_plan(text) or None

    def check_condition(
        self,
        question: str,
        screenshot_png: bytes,
        screen_size: tuple[int, int],
    ) -> bool:
        text = self._complete(CHECK_PROMPT, f"Question: {question}", screenshot_png)
        return text.strip().lower().startswith("yes")
