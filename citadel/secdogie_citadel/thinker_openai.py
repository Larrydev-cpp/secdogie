"""The thinker on an OpenAI-compatible chat completions endpoint (OpenAI,
DeepSeek, OpenRouter, a local server…), over plain HTTP with the standard
library.

It sends one ``POST {base}/chat/completions`` that asks for a JSON object
(``response_format``), and reads the first choice's message. Any error, or a
reply that is not JSON, gives no advice.
"""
from __future__ import annotations

import json
import urllib.request

from .thinker import TIMEOUT, ThinkerConfig


def ask(config: ThinkerConfig, system: str, question: str):
    body = json.dumps({
        "model": config.model_name,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}],
        "response_format": {"type": "json_object"},
        "max_tokens": 900,
    }).encode("utf-8")
    request = urllib.request.Request(f"{config.base}/chat/completions", data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {config.api_key}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:  # noqa: S310 - the owner's own endpoint
        data = json.loads(reply.read().decode("utf-8"))
    text = data["choices"][0]["message"]["content"] or ""
    return json.loads(text)


__all__ = ["ask"]
