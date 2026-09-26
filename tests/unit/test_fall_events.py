"""F5: event store persistence and the alert reporter (httpx.MockTransport)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from elder_companion.alerts.schemas import AlertIn
from fall_detector.events import EventStore
from fall_detector.reporter import Reporter, alert_payload

pytestmark = pytest.mark.unit

T0 = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "fall_events.jsonl", tmp_path / "snapshots")


def _add(store: EventStore, minutes: int = 0, **kw):
    args = dict(track_id=1, confidence=0.8, source="live", video="demo/videos/fall_01.mp4")
    return store.add(**(args | kw), now=T0 + timedelta(minutes=minutes))


def test_add_writes_jsonl_and_snapshot(store: EventStore) -> None:
    rec = _add(store, snapshot_jpeg=b"\xff\xd8jpeg")
    line = json.loads(store.events_path.read_text(encoding="utf-8").strip())
    assert line["event_id"] == rec.event_id and line["source"] == "live"
    assert store.snapshot_path(rec.event_id).read_bytes() == b"\xff\xd8jpeg"
    assert rec.snapshot_id == rec.event_id


def test_event_without_snapshot(store: EventStore) -> None:
    rec = _add(store)
    assert rec.snapshot_id is None
    assert store.snapshot_path(rec.event_id) is None


def test_survives_restart(store: EventStore, tmp_path: Path) -> None:
    rec = _add(store, snapshot_jpeg=b"x")
    again = EventStore(tmp_path / "fall_events.jsonl", tmp_path / "snapshots")
    assert again.get(rec.event_id) == rec
    assert again.snapshot_path(rec.event_id) is not None


def test_query_newest_first_since_limit_source(store: EventStore) -> None:
    a = _add(store, 0)
    b = _add(store, 10, source="offline")
    c = _add(store, 20)
    assert [e.event_id for e in store.query()] == [c.event_id, b.event_id, a.event_id]
    assert [e.event_id for e in store.query(since=T0 + timedelta(minutes=5))] == [c.event_id, b.event_id]
    assert [e.event_id for e in store.query(limit=1)] == [c.event_id]
    assert [e.event_id for e in store.query(source="live")] == [c.event_id, a.event_id]
    # naive datetimes are treated as UTC
    assert len(store.query(since=(T0 + timedelta(minutes=15)).replace(tzinfo=None))) == 1


def test_sees_events_written_by_another_process(store: EventStore, tmp_path: Path) -> None:
    reader = EventStore(tmp_path / "fall_events.jsonl", tmp_path / "snapshots")
    assert reader.query() == []
    rec = _add(store)
    assert [e.event_id for e in reader.query()] == [rec.event_id]


def test_corrupt_line_is_skipped(store: EventStore, tmp_path: Path) -> None:
    rec = _add(store)
    with store.events_path.open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    again = EventStore(tmp_path / "fall_events.jsonl", tmp_path / "snapshots")
    assert [e.event_id for e in again.query()] == [rec.event_id]


def test_payload_matches_the_alert_contract(store: EventStore) -> None:
    rec = _add(store, snapshot_jpeg=b"x")
    alert = AlertIn.model_validate(alert_payload(rec, down_for_s=3))
    assert alert.type == "fall" and alert.level == "high"
    assert alert.snapshot_path == f"snapshots/{rec.event_id}.jpg"
    assert alert.ref_id == rec.event_id
    assert "3s" in alert.content


def _reporter(handler) -> tuple[Reporter, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return Reporter("http://main/api/alerts", client=client, backoff=(0,)), seen


def test_report_posts_payload(store: EventStore) -> None:
    rep, seen = _reporter(lambda r: httpx.Response(201, json={"id": 7}))
    assert rep.report_now(_add(store))
    assert json.loads(seen[0].content)["type"] == "fall"


def test_report_retries_then_gives_up(store: EventStore) -> None:
    rep, seen = _reporter(lambda r: httpx.Response(503))
    assert rep.report_now(_add(store)) is False
    assert len(seen) == 3


def test_report_survives_connection_errors(store: EventStore) -> None:
    def boom(r):
        raise httpx.ConnectError("main service down")

    rep, seen = _reporter(boom)
    assert rep.report_now(_add(store)) is False
    assert len(seen) == 3


def test_contract_rejection_is_not_retried(store: EventStore) -> None:
    rep, seen = _reporter(lambda r: httpx.Response(422, json={"detail": "bad"}))
    assert rep.report_now(_add(store)) is False
    assert len(seen) == 1


def test_background_report_is_delivered(store: EventStore) -> None:
    rep, seen = _reporter(lambda r: httpx.Response(201, json={"id": 1}))
    rep.report(_add(store))
    rep.close()  # drains the queue
    assert len(seen) == 1
