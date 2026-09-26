"""Removed CLI flags fail loudly instead of being silently ignored."""
import argparse

import pytest
from secdogie_agent import cli_common


def _parser():
    parser = argparse.ArgumentParser(prog="secdogie-agent")
    parser.add_argument("task", nargs="?")
    cli_common.add_loop_args(parser)
    return parser


def test_allow_risky_is_gone_and_says_why(capsys):
    with pytest.raises(SystemExit) as exc:
        _parser().parse_args(["do something", "--auto", "--allow-risky"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--allow-risky was removed" in err and "always ask for confirmation" in err


def test_the_removed_flag_leaves_no_trace_in_the_namespace():
    args = _parser().parse_args(["do something", "--auto"])
    assert not hasattr(args, "allow_risky")
    assert "confirm_high_risk" not in cli_common.loop_config_kwargs(
        argparse.Namespace(**{**vars(args), "max_steps": None, "log_file": None, "grid": False,
                              "watch": False, "watch_interval": None, "max_image_edge": None,
                              "action_pause": None, "stall_limit": None}),
        task="do something",
    )
