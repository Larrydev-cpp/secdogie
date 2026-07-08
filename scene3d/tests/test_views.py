from secdogie_scene3d.views import Viewpoint, load_viewpoint, parse_view_arg


def test_load_viewpoint_reads_bytes_and_defaults_label_to_stem(tmp_path):
    p = tmp_path / "top.png"
    p.write_bytes(b"\x89PNG-data")
    vp = load_viewpoint(str(p))
    assert vp == Viewpoint(label="top", image_png=b"\x89PNG-data", hint="")


def test_load_viewpoint_explicit_label_and_hint(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"x")
    vp = load_viewpoint(str(p), label="front", hint="looking north")
    assert vp.label == "front" and vp.hint == "looking north"


def test_parse_view_arg_label_equals_path():
    assert parse_view_arg("front=/a/b/front.png") == ("front", "/a/b/front.png")


def test_parse_view_arg_plain_path_has_no_label():
    assert parse_view_arg("/a/b/front.png") == (None, "/a/b/front.png")


def test_parse_view_arg_blank_label_falls_back_to_none():
    assert parse_view_arg("=x.png") == (None, "x.png")
