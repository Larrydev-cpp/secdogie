"""The thinker on Claude, through the official Anthropic SDK (``pip install
secdogie-citadel[thinker]``).

- One Messages request.
- The reply is constrained to the thinker's JSON schema with structured
  outputs (``output_config.format``).
- Effort is ``low``: this is a quick second reading, not a long task.
- Server-side refusal fallbacks are on (``fallbacks: "default"``), so a
  declined request is retried on a model chosen by the decline's category.
- A refusal from the whole chain, a truncated reply or any API error leaves
  the gate as it is. The caller treats every exception as "no advice".
"""
from __future__ import annotations

import json

from .thinker import SCHEMA, TIMEOUT, ThinkerConfig

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def ask(config: ThinkerConfig, system: str, question: str):
    """The model's JSON reply as an object, or None when it declined or was cut off."""
    import anthropic

    client = anthropic.Anthropic(api_key=config.api_key, base_url=config.base or None, timeout=TIMEOUT, max_retries=1)
    response = client.beta.messages.create(
        model=config.model_name,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": question}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if response.stop_reason != "end_turn":  # refusal, max_tokens: no advice
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text) if text else None


__all__ = ["FALLBACK_BETA", "ask"]
