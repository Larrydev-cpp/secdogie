"""Zero trust by default (Wave B): the fleet coordinator and node refuse to run
unauthenticated unless told so explicitly (``insecure_dev=True``)."""
from __future__ import annotations

import pytest
from secdogie_fleet import node as node_mod
from secdogie_fleet.server import FleetServer


def test_the_coordinator_needs_authentication_or_an_explicit_insecure_dev():
    with pytest.raises(ValueError, match="insecure_dev"):
        FleetServer(host="127.0.0.1", port=0)
    s = FleetServer(host="127.0.0.1", port=0, insecure_dev=True)
    s.shutdown()


def test_a_node_needs_authentication_or_an_explicit_insecure_dev():
    with pytest.raises(ValueError, match="insecure_dev"):
        node_mod.connect_and_serve("127.0.0.1", 1)
