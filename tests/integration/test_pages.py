import json
import re

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_root_redirects_to_elder_app(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/elder"


def test_elder_page_renders_persona_and_config(client: TestClient) -> None:
    r = client.get("/elder")
    assert r.status_code == 200
    html = r.text
    assert "<title>Sunny</title>" in html
    assert "Hello, Maggie!" in html
    assert 'id="mic-btn"' in html
    # Assert on what the page hands the scripts, not on the literal. The object keeps
    # gaining fields — voices, language, the string table — and an exact-match assertion
    # has to be edited every time one is added, which is how it broke on both sides of
    # this merge.
    cfg = json.loads(re.search(r"window\.APP_CONFIG = (\{.*?\});", html, re.S).group(1))
    assert cfg["elderId"] == 1
    assert cfg["nickname"] == "Maggie"
    assert cfg["voices"] == ["female", "male"]
    assert "call 911" in html


def test_elder_page_unknown_elder_404(client: TestClient) -> None:
    assert client.get("/elder?elder_id=99").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/static/elder.js",
        "/static/family.js",
        "/static/lang.js",
        "/static/tokens.css",
        "/static/elder.css",
        "/static/family.css",
    ],
)
def test_static_assets_served(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_family_page_renders_dashboard_and_config(client: TestClient) -> None:
    r = client.get("/family")
    assert r.status_code == 200
    html = r.text
    assert "<title>Maggie&#39;s day</title>" in html or "<title>Maggie's day</title>" in html
    for element_id in ("alert-banner", "summary-text", "timeline", "alert-list", "fall-stream"):
        assert f'id="{element_id}"' in html
    assert '"timezone": "America/Los_Angeles"' in html
    assert '"fallStreamPort": 8001' in html
    assert "not medical advice" in html


def test_family_page_unknown_elder_404(client: TestClient) -> None:
    assert client.get("/family?elder_id=99").status_code == 404


def test_family_script_served(client: TestClient) -> None:
    assert client.get("/static/family.js").status_code == 200


def test_fall_snapshots_served_from_data_dir(client: TestClient) -> None:
    snapshots = client.app.state.settings.paths.snapshots_dir
    (snapshots / "evt_1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    r = client.get("/media/snapshots/evt_1.jpg")
    assert r.status_code == 200 and r.content == b"\xff\xd8\xff\xd9"
    assert client.get("/media/snapshots/missing.jpg").status_code == 404
    assert client.get("/media/snapshots/../app.db").status_code == 404


def test_family_page_has_stage_h_panels_and_report_links(client: TestClient) -> None:
    html = client.get("/family").text
    for element_id in ("member-select", "care-list", "care-empty"):
        assert f'id="{element_id}"' in html
    assert 'href="/family/doctor"' in html and 'href="/family/memoir"' in html
    assert '"members": [{"id": 1, "name": "Amy", "relation": "daughter"}, ' in html
    assert '"memberId": null' in html


def test_family_page_has_the_reminders_panel(client: TestClient) -> None:
    html = client.get("/family").text
    for element_id in ("reminder-list", "reminders-empty", "reminder-form", "reminder-text"):
        assert f'id="{element_id}"' in html
    # The safety promise the team signed off on is on the page, not only in the prompt.
    assert "never adds a dose or medical advice" in html


@pytest.mark.parametrize(("member", "expected"), [("ben", 2), ("Ben", 2), ("1", 1), ("zoe", None)])
def test_member_query_picks_by_name_or_id(
    client: TestClient, member: str, expected: int | None
) -> None:
    html = client.get(f"/family?member={member}").text
    assert f'"memberId": {"null" if expected is None else expected}' in html
