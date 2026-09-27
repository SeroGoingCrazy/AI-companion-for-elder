"""H8: care list from companion memory."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from elder_companion.memory.care_list import care_list
from elder_companion.models import MemoryItem
from elder_companion.seed import seed_history
from elder_companion.settings import Settings

pytestmark = pytest.mark.unit

T0 = datetime(2026, 9, 29, 16, 0)


def _i(id: int, kind: str, count: int, private: bool = False, hours_ago: int = 0) -> MemoryItem:
    return MemoryItem(
        id=id,
        elder_id=1,
        kind=kind,
        subject=f"s{id}",
        text="t",
        raw_quote="q",
        mention_count=count,
        private=private,
        first_seen=T0,
        last_seen=T0 - timedelta(hours=hours_ago),
    )


def test_people_and_topics_over_the_threshold_most_recent_first() -> None:
    items = [
        _i(1, "person", 3, hours_ago=5),
        _i(2, "topic", 2, hours_ago=1),
        _i(3, "person", 1),  # a single mention
        _i(4, "person", 4, private=True),
        _i(5, "follow_up", 5),
        _i(6, "story", 2),
    ]
    assert [i.id for i in care_list(items, min_mentions=2)] == [2, 1]
    assert [i.id for i in care_list(items, min_mentions=1)] == [3, 2, 1]


def test_endpoint_lists_the_seeded_neighbor_and_grandson(settings: Settings) -> None:
    from elder_companion.web.app import create_app

    with TestClient(create_app(settings)) as c:
        with c.app.state.sessionmaker() as s:
            seed_history(s, datetime.now(settings.chat.tz))
        body = c.get("/api/care-list").json()
    assert [(i["subject"], i["mention_count"]) for i in body] == [
        ("grandson Leo", 2),
        ("Rosa", 3),
    ]
    assert body[0]["raw_quote"] == "Leo mailed me a drawing of my roses"
