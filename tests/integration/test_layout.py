"""Nothing may push the page wider than the phone viewport.

A horizontal overflow on a phone drags the whole layout sideways, and the fixed tab bar
goes with it: the buttons stop being under the thumb and the app reads as broken. This
happened for real with an unconstrained 640x480 fall snapshot.
"""

import re

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_alert_snapshots_are_constrained(client: TestClient) -> None:
    css = client.get("/static/family.css").text
    block = re.search(r"\.alert-item img\s*\{([^}]*)\}", css)
    assert block, ".alert-item img rule is missing"
    body = block.group(1)
    assert "max-width: 100%" in body
    assert "height: auto" in body


def test_media_inside_cards_is_capped(client: TestClient) -> None:
    css = client.get("/static/family.css").text
    assert re.search(r"\.card img[^{]*\{[^}]*max-width:\s*100%", css)


def test_tab_bar_sits_above_content(client: TestClient) -> None:
    """It is fixed and on top; content scrolling under it must not cover it."""
    css = client.get("/static/family.css").text
    bar = re.search(r"\.tabbar\s*\{([^}]*)\}", css)
    assert bar, ".tabbar rule is missing"
    assert "position: fixed" in bar.group(1)
    assert "z-index" in bar.group(1)


def test_every_tab_has_a_panel(client: TestClient) -> None:
    """Whatever the tab bar offers must exist; the set grows as features land, so this
    checks the pairing rather than a fixed list."""
    html = client.get("/family").text
    tabs = set(re.findall(r'data-panel="([a-z]+)"', html))
    panels = set(re.findall(r'id="panel-([a-z]+)"', html))
    assert tabs, "no tabs found"
    assert tabs == panels, f"tabs without panels: {tabs - panels}; orphan panels: {panels - tabs}"
    assert "today" in tabs


def test_tabs_are_deep_linkable(client: TestClient) -> None:
    """?tab= opens one screen directly, so a link can point at the camera, and so tools
    that import a page at a time can reach all four."""
    js = client.get("/static/tabs.js").text
    assert 'get("tab")' in js
    assert "URLSearchParams" in js
    # an unknown value must fall back rather than render nothing
    assert 'show(start || "today"' in js


def test_elder_app_scrolls_inside_the_conversation(client: TestClient) -> None:
    """Not as a page. A page scroll carried the header and the talk bar off the display as
    the chat grew, and the talk bar is the one control she must always be able to reach."""
    css = client.get("/static/elder.css").text
    body = re.search(r"\nbody \{([^}]*)\}", css)
    assert body and "overflow: hidden" in body.group(1)
    app = re.search(r"\.elder-app \{([^}]*)\}", css)
    assert app and "height: 100dvh" in app.group(1)
    conv = re.search(r"\.conversation \{([^}]*)\}", css)
    assert conv and "overflow-y: auto" in conv.group(1)


def test_mock_chinese_utterances_get_chinese_replies(client: TestClient) -> None:
    """The offline demo is what a judge sees. A Chinese speaker must not be answered in
    English, least of all on the emergency turn."""
    for text in ("我胸口有点闷，喘不上气", "这两天早上起来头有点晕", "昨天晚上没睡好"):
        reply = client.post("/api/chat", json={"elder_id": 1, "text": text}).json()["reply_text"]
        assert re.search(r"[一-鿿]", reply), f"{text!r} -> {reply!r}"
