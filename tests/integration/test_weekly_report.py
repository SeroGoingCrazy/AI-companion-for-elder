"""H7: the weekly report page and the daily digests behind it (mock LLM)."""

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import DailyDigest, Message
from elder_companion.seed import seed_history
from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def llm() -> MockLLMClient:
    return MockLLMClient.from_config()


@pytest.fixture
def client(settings: Settings, llm: MockLLMClient) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=llm)) as c:
        yield c


@pytest.fixture
def seeded(settings: Settings, client: TestClient) -> TestClient:
    with client.app.state.sessionmaker() as s:
        seed_history(s, datetime.now(settings.chat.tz))
    return client


def _page(c: TestClient, query: str = "") -> str:
    r = c.get(f"/family/report/weekly{query}")
    assert r.status_code == 200, r.text
    return r.text


# ---------- the page ----------


def test_the_seeded_week_shows_moods_topics_symptoms_and_adherence(seeded: TestClient) -> None:
    html = _page(seeded)
    assert "Maggie's week" in html
    # Six seeded days have a mood; today has none yet, so six dots and a gap.
    assert html.count('class="mood-dot"') == 6
    assert "chatted on 6 of 7 days" in html
    for topic in ("knee", "garden", "sleep"):
        assert f">{topic}<" in html
    assert "Joint pain" in html and "Poor sleep" in html
    assert "blood pressure pill with breakfast" in html and "4 of 6" in html


def test_symptom_rows_are_grouped_not_one_per_log_row(seeded: TestClient) -> None:
    """The seeded week has four joint_pain rows; the family should see one line."""
    assert _page(seeded).count("Joint pain") == 1


def test_an_empty_week_says_so_without_charts(client: TestClient) -> None:
    html = _page(client)
    assert "has not chatted this week yet" in html
    assert "mood-dot" not in html


def test_earlier_weeks_are_reachable(seeded: TestClient) -> None:
    html = _page(seeded, "?week=1")
    assert "has not chatted this week yet" in html  # nothing was seeded that far back
    assert "week=2" in html  # and you can keep going back


def test_the_dashboard_links_to_it(client: TestClient) -> None:
    assert 'href="/family/report/weekly"' in client.get("/family").text


def test_the_page_is_printable(seeded: TestClient) -> None:
    html = _page(seeded)
    assert "window.print()" in html and 'class="report-nav no-print"' in html
    assert "not medical advice" in html.lower()


# ---------- digests behind it ----------


def test_the_week_is_built_from_stored_digests_without_new_calls(
    seeded: TestClient, llm: MockLLMClient
) -> None:
    """Seeded days are already summarized, so only the overview costs a call."""
    before = len([1 for name, _ in llm.calls if name == "extract_json"])
    _page(seeded)
    after = len([1 for name, _ in llm.calls if name == "extract_json"])
    assert after == before  # no digest was regenerated


def test_todays_chat_produces_a_digest_shared_with_the_dashboard(client: TestClient) -> None:
    """The dashboard card and the weekly page read the same stored digest, so today's mood
    point cannot disagree with the text the family just read."""
    client.post("/api/chat", json={"text": "I watered the roses"})
    summary = client.get("/api/summary/today").json()["summary"]
    with client.app.state.sessionmaker() as s:
        [row] = list(s.scalars(select(DailyDigest)))
        assert row.summary == summary and row.mood_score is not None
    assert 'class="mood-dot"' in _page(client)  # that day now has a score on the line


def test_a_private_day_is_counted_but_never_quoted(client: TestClient) -> None:
    client.post("/api/chat/greet")
    client.post(
        "/api/chat",
        json={"text": "My friend Linda got bad news from her doctor. Keep this between us."},
    )
    html = _page(client)
    assert "Linda" not in html and "bad news" not in html
    assert "asked to keep part of the conversation private" in html


def test_a_late_privacy_request_rebuilds_the_day(client: TestClient) -> None:
    """The digest is usually written before she asks; the request must undo it."""
    client.post("/api/chat", json={"text": "My friend Linda got bad news from her doctor"})
    client.get("/api/summary/today")  # the family reads the day, which stores the digest
    with client.app.state.sessionmaker() as s:
        assert list(s.scalars(select(DailyDigest)))  # summarized already
    client.post("/api/chat", json={"text": "Please keep this between us"})
    with client.app.state.sessionmaker() as s:
        assert list(s.scalars(select(DailyDigest))) == []  # dropped, will be rebuilt
    assert "Linda" not in _page(client)


def test_a_day_she_never_chatted_stores_no_digest(seeded: TestClient) -> None:
    with seeded.app.state.sessionmaker() as s:
        today = max(d.date for d in s.scalars(select(DailyDigest)))
        assert today < datetime.now().date()  # today has no row until she says something


def test_the_overview_degrades_when_the_model_is_down(settings: Settings) -> None:
    from elder_companion.llm import LLMError

    class DownChat(MockLLMClient):
        def chat(self, messages, *, max_tokens=None):  # noqa: ANN001
            raise LLMError("down")

    with TestClient(create_app(settings, llm=DownChat.from_config())) as c:
        with c.app.state.sessionmaker() as s:
            seed_history(s, datetime.now(settings.chat.tz))
        html = _page(c)
        # The numbers are computed in code, so they survive a model outage.
        assert "chatted on 6 of 7 days" in html and "Joint pain" in html
        assert "Maggie chatted on 6 of 7 days" in html


def test_history_older_than_the_week_is_not_counted(seeded: TestClient) -> None:
    """A message from two weeks ago must not land in this week's numbers."""
    with seeded.app.state.sessionmaker() as s:
        s.add(
            Message(
                elder_id=1,
                role="user",
                text="an old chat",
                created_at=datetime.now() - timedelta(days=20),
            )
        )
        s.commit()
    assert "chatted on 6 of 7 days" in _page(seeded)
