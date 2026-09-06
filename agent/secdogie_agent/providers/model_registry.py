"""Live OpenRouter model catalogue with a small offline fallback.

OpenRouter exposes its model directory at ``/api/v1/models``. The agent does
not need to whitelist models, so this module is intentionally catalogue-only:
new models appear in the UI without a secdogie release.
"""
from __future__ import annotations

from . import OPENROUTER_BASE_URL, OPENROUTER_PROVIDER_ID, SUGGESTED_MODELS

_FALLBACK = list(SUGGESTED_MODELS[OPENROUTER_PROVIDER_ID])


def fallback_models() -> list[str]:
    return list(_FALLBACK)


def fetch_openrouter_catalog(timeout: float = 8.0) -> list[dict]:
    """Return normalized OpenRouter model records, or the fallback list.

    Only records with an id are retained. Vision-capable records are marked so
    callers can offer a "vision only" filter without maintaining a model list.
    """
    try:
        import json
        import urllib.request

        req = urllib.request.Request(
            f"{OPENROUTER_BASE_URL}/models",
            headers={"Accept": "application/json", "User-Agent": "secdogie-agent"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        data = payload.get("data") or []
        records: list[dict] = []
        for item in data:
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            arch = item.get("architecture") or {}
            modalities = arch.get("input_modalities") or item.get("modality") or []
            if isinstance(modalities, str):
                modalities = [modalities]
            modalities = [str(x).lower() for x in modalities]
            records.append(
                {
                    "id": model_id,
                    "name": item.get("name") or model_id,
                    "context_length": item.get("context_length"),
                    "input_modalities": modalities,
                    "vision": "image" in modalities or "vision" in modalities,
                    "pricing": item.get("pricing") or {},
                    "created": item.get("created"),
                }
            )
        if records:
            return records
    except Exception:
        pass

    return [{"id": model_id, "name": model_id, "vision": True} for model_id in _FALLBACK]


def fetch_openrouter_models(timeout: float = 8.0) -> list[str]:
    """Backward-compatible helper returning ids, vision-capable first."""
    records = fetch_openrouter_catalog(timeout=timeout)
    vision = [item["id"] for item in records if item.get("vision")]
    other = [item["id"] for item in records if not item.get("vision")]
    return vision + other
