import pytest

from secdogie_android import uitree

# A trimmed but realistic uiautomator dump: a big clickable container holding a
# small button and a text field, plus a non-clickable label.
SAMPLE = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node index="0" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node index="0" text="" resource-id="com.app:id/container" class="android.widget.LinearLayout"
          content-desc="" clickable="true" bounds="[0,200][1080,800]">
      <node index="0" text="Log in" resource-id="com.app:id/login_button" class="android.widget.Button"
            content-desc="" clickable="true" bounds="[400,300][680,420]"/>
      <node index="1" text="" resource-id="com.app:id/username" class="android.widget.EditText"
            content-desc="Username field" clickable="true" bounds="[100,500][980,600]"/>
      <node index="2" text="Welcome" resource-id="" class="android.widget.TextView"
            content-desc="" clickable="false" bounds="[100,100][980,180]"/>
    </node>
  </node>
</hierarchy>"""


def test_parse_extracts_all_bounded_nodes():
    els = uitree.parse(SAMPLE)
    # 5 nodes have bounds (root frame, container, button, edit, textview).
    assert len(els) == 5
    button = next(e for e in els if e.resource_id.endswith("login_button"))
    assert button.text == "Log in"
    assert button.clickable
    assert button.bounds == (400, 300, 680, 420)
    assert button.center == (540, 360)


def test_parse_bad_xml_raises_valueerror():
    with pytest.raises(ValueError):
        uitree.parse("<hierarchy><node bounds='oops'")


def test_find_by_text_is_case_insensitive_substring():
    els = uitree.parse(SAMPLE)
    assert [e.resource_id for e in uitree.find_elements(els, text="log in")] == ["com.app:id/login_button"]


def test_find_by_resource_id_full_or_short():
    els = uitree.parse(SAMPLE)
    assert uitree.find_elements(els, resource_id="com.app:id/username")[0].cls == "android.widget.EditText"
    # short id (after the last '/') also matches
    assert uitree.find_elements(els, resource_id="username")[0].content_desc == "Username field"


def test_find_by_content_desc():
    els = uitree.parse(SAMPLE)
    assert uitree.find_elements(els, content_desc="username")[0].resource_id.endswith("username")


def test_clickable_only_filter_excludes_label():
    els = uitree.parse(SAMPLE)
    texts = [e.text for e in uitree.find_elements(els, clickable_only=True) if e.text]
    assert "Welcome" not in texts  # the TextView is not clickable
    assert "Log in" in texts


def test_smallest_clickable_at_prefers_tight_element_over_container():
    els = uitree.parse(SAMPLE)
    # A point inside both the container and the button -> the button (smaller).
    el = uitree.smallest_clickable_at(els, 540, 360)
    assert el is not None and el.resource_id.endswith("login_button")


def test_smallest_clickable_at_returns_container_when_only_it_contains_point():
    els = uitree.parse(SAMPLE)
    # (50, 700): inside the container but not the button/edit -> container.
    el = uitree.smallest_clickable_at(els, 50, 700)
    assert el is not None and el.resource_id.endswith("container")


def test_smallest_clickable_at_none_outside_everything():
    els = uitree.parse(SAMPLE)
    assert uitree.smallest_clickable_at(els, 5, 5) is None  # only the non-clickable root covers this
