"""secdogie-perceive: read one high-resolution screenshot at full detail by
tiling it across several models, and print a single element map in full-image
coordinates.

Shares secdogie-scene3d's key-pool idea -- repeat --api-key to spread the tiles
over several keys so they run concurrently without tripping one key's rate
limit. Point it at a big frame (e.g. a 2160x1440 video editor) that a single
model reads too coarsely to click accurately.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from secdogie_agent import config as config_mod

from . import model as model_mod
from .perceive import perceive_screen

DEFAULT_MODEL = "claude-sonnet-5"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-perceive",
        description="High-resolution tiled perception: split one screenshot into a grid, analyze every "
        "tile at native resolution across a pool of models, and merge the result into one element map.",
    )
    parser.add_argument("image", help="path to the screenshot to perceive (PNG/JPEG/...)")
    parser.add_argument("--model", default=None, help=f"model for every tile worker (default: {DEFAULT_MODEL})")
    parser.add_argument("--provider", choices=["anthropic", "openai"], default=None,
                        help="force the provider instead of inferring it from the model id")
    parser.add_argument(
        "--api-key",
        action="append",
        default=None,
        help="API key for the provider. Repeat it to build a key POOL -- tiles are spread round-robin "
        "across the keys so they don't hammer one key's rate limit.",
    )
    parser.add_argument("--config", default=None, help="config file to read the API key/model from (see secdogie-agent)")
    parser.add_argument("--cols", type=int, default=3, help="grid columns (default 3)")
    parser.add_argument("--rows", type=int, default=3, help="grid rows (default 3; 3x3 = 9 tiles)")
    parser.add_argument("--overlap", type=float, default=0.12,
                        help="fraction each tile grows into its neighbours so seam elements stay whole (default 0.12)")
    parser.add_argument("--max-workers", type=int, default=None, help="max concurrent tile requests (default: min(pool, tiles))")
    args = parser.parse_args(argv)

    first_cli_key = args.api_key[0] if args.api_key else None
    resolved = config_mod.resolve(
        cli_api_key=first_cli_key, cli_model=args.model, config_path=args.config, cli_provider=args.provider
    )
    model_id = resolved.model or DEFAULT_MODEL

    keys: list[str | None] = args.api_key if args.api_key else [resolved.api_key]
    if any(not k for k in keys):
        print(
            f"error: missing API key for the {resolved.provider} provider. Provide --api-key (repeatable), "
            f"the {resolved.env_var} environment variable, or a config file.",
            file=sys.stderr,
        )
        return 1

    try:
        pool = model_mod.build_model_pool(resolved.provider, model_id, keys)
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        image_png = Path(args.image).read_bytes()
    except OSError as e:
        print(f"error: could not read image: {e}", file=sys.stderr)
        return 1

    print(
        f"tiling into {args.cols}x{args.rows} = {args.cols * args.rows} tile(s), {len(pool)} worker model(s) "
        f"({resolved.provider}/{model_id})...",
        file=sys.stderr,
    )
    perc = perceive_screen(
        image_png, pool, cols=args.cols, rows=args.rows, overlap=args.overlap, max_workers=args.max_workers
    )

    output = {
        "screen": {"width": perc.width, "height": perc.height},
        "elements": [
            {"label": e.label, "type": e.kind, "box": list(e.box), "center": list(e.center),
             "confidence": e.confidence, "tiles": [list(t) for t in e.tiles]}
            for e in perc.elements
        ],
        "errors": perc.errors,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
