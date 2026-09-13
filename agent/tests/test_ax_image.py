"""AX-box schematic is the Mac image — no screenshot pixels."""
from secdogie_agent import ax_image, axtree, elements


def _tree():
    return [
        axtree.AxElement(role="Window", name="App", automation_id="", bounds=(0, 0, 800, 600)),
        axtree.AxElement(role="Button", name="Save", automation_id="saveBtn", bounds=(100, 100, 200, 140)),
        axtree.AxElement(role="Edit", name="Filename", automation_id="fileBox", bounds=(100, 200, 400, 230)),
    ]


def test_render_paints_a_png_from_bounds_not_pixels():
    png, size = ax_image.render(_tree())
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert size[0] >= 800 and size[1] >= 600


def test_render_empty_tree_still_builds_a_pad():
    png, size = ax_image.render([])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert size == (640, 400)


def test_render_respects_region_origin():
    png, size = ax_image.render(_tree(), region=(100, 100, 200, 80))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert size == (200, 80)


def test_render_from_backend_uses_snapshot():
    class P:
        def snapshot(self):
            return _tree()

    class B:
        ax_provider = P()

    png, size = ax_image.render_from_backend(B())
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert size[0] >= 800


def test_render_from_backend_survives_missing_provider():
    class B:
        pass

    png, size = ax_image.render_from_backend(B())
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert size == (640, 400)


def test_interactable_refs_match_listing_order():
    targets = elements.interactable_targets(_tree())
    assert [el.name for el in targets] == ["Save", "Filename"]
    png, _ = ax_image.render(_tree(), targets=targets)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
