from __future__ import annotations

import argparse
import json
import sys

from secdogie_agent import config as config_mod
from secdogie_agent.providers import resolve_model_provider

from . import model as model_mod
from .pipeline import analyze_scene
from .views import load_viewpoint, parse_view_arg

DEFAULT_MODEL = "claude-sonnet-5"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-scene3d",
        description="Multi-model 3D scene analysis: N workers each analyze one view of a 3D scene, "
        "then an aggregator fuses their observations into one consolidated 3D understanding.",
    )
    parser.add_argument(
        "views",
        nargs="+",
        help="one image per viewpoint, each `label=path` or plain `path` (label defaults to the file name); "
        "give genuinely different angles of the SAME scene (front, top, left-45, ...)",
    )
    parser.add_argument("--model", default=None, help=f"model for the workers and aggregator (default: {DEFAULT_MODEL})")
    parser.add_argument("--aggregator-model", default=None, help="use a different model for the fusion step (default: same as --model)")
    parser.add_argument("--provider", choices=["anthropic", "openai"], default=None, help="force the provider instead of inferring from the model id")
    parser.add_argument(
        "--api-key",
        action="append",
        default=None,
        help="API key for the provider. Repeat it to build a key POOL -- workers are spread round-robin "
        "across the keys so concurrent views don't hammer one key's rate limit. First key is used for the aggregator.",
    )
    parser.add_argument("--config", default=None, help="config file to read the API key/model from (see secdogie-agent)")
    parser.add_argument("--max-workers", type=int, default=None, help="max concurrent worker requests (default: min(pool size, #views))")
    args = parser.parse_args(argv)

    # Resolve provider/model/first-key with the shared secdogie-agent config logic.
    first_cli_key = args.api_key[0] if args.api_key else None
    resolved = config_mod.resolve(
        cli_api_key=first_cli_key, cli_model=args.model, config_path=args.config, cli_provider=args.provider
    )
    model_id = resolved.model or DEFAULT_MODEL

    # The key pool: explicit --api-key(s) if given, else the single resolved key.
    keys: list[str | None] = args.api_key if args.api_key else [resolved.api_key]
    if any(not k for k in keys):
        print(
            f"error: missing API key for the {resolved.provider} provider. Provide --api-key (repeatable), "
            f"the {resolved.env_var} environment variable, or a config file.",
            file=sys.stderr,
        )
        return 1

    # The aggregator shares the worker provider and key pool -- there's only one
    # resolved provider/key set here. A --aggregator-model that implies a
    # different provider (e.g. gpt-* while the workers are claude-*) would
    # otherwise be sent to the wrong API and fail confusingly, so reject it.
    aggregator_model = args.aggregator_model or model_id
    if args.aggregator_model:
        agg_provider, agg_bare = resolve_model_provider(args.aggregator_model, args.provider)
        if agg_provider != resolved.provider:
            print(
                f"error: --aggregator-model '{args.aggregator_model}' is a {agg_provider} model but the "
                f"workers use {resolved.provider}; scene3d uses one provider and key pool for both stages. "
                f"Pick an aggregator model on the {resolved.provider} provider (or change --model/--provider).",
                file=sys.stderr,
            )
            return 1
        aggregator_model = agg_bare or model_id

    try:
        worker_pool = model_mod.build_model_pool(resolved.provider, model_id, keys)
        aggregator = model_mod.make_scene_model(resolved.provider, aggregator_model, keys[0])
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        viewpoints = [load_viewpoint(path, label) for label, path in (parse_view_arg(v) for v in args.views)]
    except OSError as e:
        print(f"error: could not read a view image: {e}", file=sys.stderr)
        return 1

    print(
        f"analyzing {len(viewpoints)} view(s) with {len(worker_pool)} worker model(s) "
        f"({resolved.provider}/{model_id}), then aggregating...",
        file=sys.stderr,
    )
    result = analyze_scene(worker_pool, aggregator, viewpoints, max_workers=args.max_workers)

    output = {
        "scene": result.data,
        "views": [
            {"label": o.label, "error": o.error, "observation": o.data} for o in result.observations
        ],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
