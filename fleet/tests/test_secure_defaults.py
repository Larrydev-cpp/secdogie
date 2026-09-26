"""Fleet secure mode is all or nothing.

Half a configuration used to degrade silently: a node with an identity but no
coordinator allowlist accepted UNSIGNED assignments, and a coordinator with a
signer but no node allowlist accepted any validly signed DID. Both are now
refused, at the library and at the CLI, and the CLI will not run without
authentication unless told to with --insecure-dev."""
import pytest
from secdogie_fleet import cli
from secdogie_fleet import node as node_mod
from secdogie_fleet.server import FleetServer


def _exit_code(argv) -> int:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    return exc.value.code


@pytest.mark.parametrize("argv", [
    ["coordinator", "--host", "127.0.0.1", "--port", "0"],
    ["node", "--connect", "127.0.0.1:1", "--once"],
])
def test_cli_refuses_to_run_unauthenticated_by_default(argv, capsys):
    assert _exit_code(argv) == 2
    assert "--insecure-dev" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["coordinator", "--host", "127.0.0.1", "--port", "0", "--identity", "coord.key"],
    ["coordinator", "--host", "127.0.0.1", "--port", "0", "--authorized", "nodes.allow"],
    ["node", "--connect", "127.0.0.1:1", "--once", "--identity", "node.key"],
    ["node", "--connect", "127.0.0.1:1", "--once", "--authorized", "coordinators.allow"],
])
def test_cli_refuses_half_a_secure_configuration(argv, capsys):
    assert _exit_code(argv) == 2
    assert "needs both --identity and --authorized" in capsys.readouterr().err


def test_insecure_dev_is_an_explicit_opt_in():
    # Nothing listens on port 1, so a --once node gives up right away.
    assert cli.main(["node", "--connect", "127.0.0.1:1", "--once", "--insecure-dev"]) == 0


def test_library_refuses_half_a_secure_configuration():
    pytest.importorskip("nacl")
    from secdogie_identity import Allowlist, Identity

    with pytest.raises(ValueError):
        FleetServer(host="127.0.0.1", port=0, signer=Identity.generate())
    with pytest.raises(ValueError):
        FleetServer(host="127.0.0.1", port=0, node_allowlist=Allowlist())
    # Refused before dialing: nothing listens on port 1.
    with pytest.raises(ValueError):
        node_mod.connect_and_serve("127.0.0.1", 1, identity=Identity.generate())
    with pytest.raises(ValueError):
        node_mod.connect_and_serve("127.0.0.1", 1, coordinator_allowlist=Allowlist())
