"""On-machine glue for `secdogie-commander run`: turn the tactician's abstract
decisions into real Node A / Node B executions under the input baton.

Everything here needs the real machine (a display, a GPU, the game) and the
sibling packages installed, so it is imported lazily by cli.py and is NOT
exercised by the test suite -- the tactician core (tactician.py) is what's proven
headless. This module is the documented seam where the user plugs in perception:
`load_state_reader` imports a StateReader factory the user writes (HUD + YOLO).
"""
from __future__ import annotations

import argparse
import importlib
import os
from collections.abc import Callable

from .tactician import Decision, StateReader


def load_state_reader(spec: str) -> StateReader:
    """Import a StateReader from a 'module:factory' spec and call the factory.

    The user owns perception -- reading health/arrows off the HUD and counting
    crystals / judging the dragon's pose with YOLO -- so they hand it in here
    rather than the commander guessing pixels."""
    if ":" not in spec:
        raise RuntimeError(f"--state-reader must be 'module:factory', got {spec!r}")
    mod_name, _, factory_name = spec.partition(":")
    try:
        module = importlib.import_module(mod_name)
        factory = getattr(module, factory_name)
    except (ImportError, AttributeError) as e:
        raise RuntimeError(f"could not load StateReader {spec!r}: {e}") from e
    reader = factory()
    if not isinstance(reader, StateReader):
        raise RuntimeError(f"{spec!r} did not return an object with a read() -> GameState method")
    return reader


def build_runners(
    args: argparse.Namespace,
) -> tuple[Callable[[Decision], None], Callable[[str], None]]:
    """Build the (run_logistics, run_combat) pair the tactician dispatches to.

    Node B (combat) is aim.engage against a fresh YOLO detection each frame;
    Node A (logistics) is the agent loop replaying a macro. Each takes the
    cross-process input baton for its turn -- the commander runs them
    sequentially, but taking the baton keeps a separately-launched Node A
    process honest too."""
    import mss
    from secdogie_aim import AimConfig, engage, open_mouse
    from secdogie_aim.yolo import YoloDetector
    from secdogie_handoff import InputArbiter

    arbiter = InputArbiter(args.lock_dir)
    detector = YoloDetector(args.weights)  # load the model once, reuse every engagement
    mouse = open_mouse()
    aim_cfg = AimConfig(gain=getattr(args, "gain", 0.5))

    sct = mss.mss()
    monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
    frame_size = (monitor["width"], monitor["height"])

    def capture() -> bytes:
        shot = sct.grab(monitor)
        return mss.tools.to_png(shot.rgb, shot.size)

    def run_combat(label: str) -> None:
        arbiter.request_yield("node-b")
        with arbiter.hold("node-b"):
            engage(capture, detector, mouse, frame_size, aim_cfg, label=label)

    def run_logistics(decision: Decision) -> None:
        _run_agent_logistics(arbiter, args, decision)

    return run_logistics, run_combat


def _run_agent_logistics(arbiter, args: argparse.Namespace, decision: Decision) -> None:
    from secdogie_agent.loop import AgentConfig, run
    from secdogie_agent.providers import API_KEY_ENV, make_provider, resolve_model_provider

    task = (
        "disengage from the dragon, get to safety, and heal"
        if decision.kind == "retreat"
        else "restock: refill the hotbar, potions, and arrows, then return to the fight"
    )
    provider_id, bare_model = resolve_model_provider(getattr(args, "model", None))
    provider = make_provider(provider_id, bare_model, os.environ.get(API_KEY_ENV[provider_id]))
    config = AgentConfig(
        task=task,
        macro_path=args.macro,
        # Node A yields the baton the moment Node B raises its hand (exit code 5).
        should_stop=lambda: arbiter.yield_requested() is not None,
    )
    with arbiter.hold("node-a"):
        run(provider, config)
