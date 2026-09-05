"""OpenRouter model catalogue.

The live list lives at https://openrouter.ai/api/v1/models. Fetching it is
optional — the agent already accepts any model string — so a network failure
returns the static SUGGESTED_MODELS fallback instead of crashing import or
the GUI.
"""
from __future__ import annotations

from . import OPENROUTER_BASE_URL, SUGGESTED_MODELS, OPENROUTER_PROVIDER_ID

_FALLBACK = list(SUGGESTED_MODELS[OPENROUTER_PROVIDER_ID])


def fallback_models() -> list[str]:
    return list(_FALLBACK)


def fetch_openrouter_models(timeout: float = 8.0) -> list[str]:
    """Return OpenRouter model ids, vision-capable first when the field exists.

    Never raises: a transport / parse failure yields the static fallback.
    """
    try:
        import json
        import urllib.request

        req = urllib.request.Request(
            f"{OPENROUTER_BASE_URL}/models",
            headers={"User-Agent": "secdogie-agent"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        data = payload.get("data") or []
        ids: list[str] = []
        vision: list[str] = []
        for item in data:
            mid = item.get("id")
            if not isinstance(mid, str) or not mid:
                continue
            arch = item.get("architecture") or {}
            modalities = arch.get("input_modalities") or item.get("modality") or ""
            blob = modalities if isinstance(modalities, str) else " ".join(map(str, modalities))
            if "image" in blob.lower() or "vision" in blob.lower():
                vision.append(mid)
            else:
                ids.append(mid)
        ordered = vision + ids
        return ordered or fallback_models()
    except Exception:
        return fallback_models()
