"""The default decision policy: cheapest, disjoint, distinct models first,
central only when the mesh runs out, never a partial quorum."""
from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from secdogie_identity import Identity  # noqa: E402
from secdogie_inference.protocol import MODE_LOCKDOWN, MODE_NORMAL, VIA_CENTRAL, make_request  # noqa: E402
from secdogie_inference.router import NoRoute, Router, View  # noqa: E402

USER = Identity.generate()


def req(**kw):
    return make_request(USER, kw.pop("models", ["m/a", "m/b"]), "hi", clock=lambda: 1.0, **kw)


def chain_view(**kw):
    #   L - w1 - s - x - w4 - R      (s decides; the diagram's chain)
    adj = {"L": {"w1"}, "w1": {"L", "s"}, "s": {"w1", "x"}, "x": {"s", "w4"}, "w4": {"x", "R"}, "R": {"w4"}}
    base = dict(me="s", mode=MODE_NORMAL, adjacency=adj, transit=frozenset({"w1", "s", "x", "w4"}),
                candidates=[("L", "m/a"), ("R", "m/b")])
    base.update(kw)
    return View(**base)


def test_cheapest_first():
    legs = Router().plan(chain_view(), req(), quorum=1)
    assert [(leg.provider, leg.path) for leg in legs] == [("L", ("s", "w1", "L"))]


def test_quorum_takes_distinct_models():
    view = chain_view(candidates=[("L", "m/a"), ("L2", "m/a"), ("R", "m/b")])
    view.adjacency["L2"] = {"w1"}
    view.adjacency["w1"].add("L2")
    legs = Router().plan(view, req(quorum=2), quorum=2)
    assert {leg.provider for leg in legs} == {"L", "R"}  # not L and L2: two vendors beat one twice


def test_later_legs_avoid_earlier_interiors_when_they_can():
    #  s - a - p ; s - b - p2 ; a - p2   (p2 is cheaper through a, but a is taken)
    adj = {"s": {"a", "b"}, "a": {"s", "p", "p2"}, "b": {"s", "p2"}, "p": {"a"}, "p2": {"a", "b"}}
    view = View("s", MODE_NORMAL, adj, frozenset({"s", "a", "b"}), [("p", "m/a"), ("p2", "m/b")])
    legs = Router().plan(view, req(quorum=2), quorum=2)
    assert [leg.path for leg in legs] == [("s", "a", "p"), ("s", "b", "p2")]


def test_central_fallback_only_when_the_mesh_runs_out():
    view = chain_view(central={"C": frozenset({"L", "R"})})
    assert all(leg.via != VIA_CENTRAL for leg in Router().plan(view, req(), quorum=1))
    cut = chain_view(central={"C": frozenset({"L"})})
    legs = Router().plan(cut, req(models=["m/a"]), quorum=1, avoid={"w1"})
    assert [(leg.path, leg.via) for leg in legs] == [(("s", "C", "L"), VIA_CENTRAL)]
    with pytest.raises(NoRoute):
        Router().plan(chain_view(mode=MODE_LOCKDOWN, central={"C": frozenset({"L"})}), req(models=["m/a"]),
                      quorum=1, avoid={"w1"})


def test_never_a_partial_quorum():
    view = chain_view(candidates=[("L", "m/a")])
    with pytest.raises(NoRoute, match="1 of 2"):
        Router().plan(view, req(quorum=2), quorum=2)


def test_pins_are_the_route():
    other = Identity.generate().did
    pinned = req(quorum=1, pins=[(other, "m/b")])
    with pytest.raises(NoRoute, match="pinned"):
        Router().plan(chain_view(), pinned, quorum=1)
    view = chain_view(candidates=[("L", "m/a"), (other, "m/b")])
    view.adjacency[other] = {"x"}
    view.adjacency["x"].add(other)
    legs = Router().plan(view, pinned, quorum=1)
    assert [leg.provider for leg in legs] == [other]
