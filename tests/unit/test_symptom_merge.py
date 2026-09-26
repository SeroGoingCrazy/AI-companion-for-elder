from datetime import datetime, timedelta

import pytest

from elder_companion.models import SymptomLog
from elder_companion.symptoms.merge import find_match, max_severity, merge_symptom
from elder_companion.symptoms.schema import SymptomItem

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 29, 15, 0)
WINDOW = timedelta(hours=24)


def _item(**kw) -> SymptomItem:
    base = {
        "canonical": "dizziness",
        "label": "morning dizziness",
        "body_part": "head",
        "severity": "mild",
        "duration": "2 days",
        "onset": "mornings",
        "status": "new",
        "raw_quote": "头有点晕",
    }
    return SymptomItem(**{**base, **kw})


def _row(hours_ago: float = 1, **kw) -> SymptomLog:
    base = {
        "id": 7,
        "elder_id": 1,
        "canonical": "dizziness",
        "label": "morning dizziness",
        "body_part": "head",
        "severity": "moderate",
        "duration": "2 days",
        "onset": "mornings",
        "status": "new",
        "raw_quote": "old words",
        "count": 1,
        "first_seen": NOW - timedelta(hours=hours_ago),
        "last_seen": NOW - timedelta(hours=hours_ago),
    }
    return SymptomLog(**{**base, **kw})


def test_new_symptom_is_inserted() -> None:
    r = merge_symptom(None, _item(), NOW, message_id=3)
    assert r.is_new
    assert r.values["canonical"] == "dizziness"
    assert r.values["count"] == 1
    assert r.values["first_seen"] == r.values["last_seen"] == NOW
    assert r.values["message_id"] == 3
    assert r.values["raw_quote"] == "头有点晕"


def test_merge_within_window_bumps_count_and_last_seen() -> None:
    existing = _row(hours_ago=23)
    r = merge_symptom(existing, _item(), NOW, message_id=9, window=WINDOW)
    assert not r.is_new
    assert r.values["count"] == 2
    assert r.values["last_seen"] == NOW
    assert r.values["raw_quote"] == "头有点晕"
    assert r.values["message_id"] == 9
    assert "first_seen" not in r.values


def test_outside_window_starts_a_new_row() -> None:
    assert merge_symptom(_row(hours_ago=25), _item(), NOW, window=WINDOW).is_new


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("moderate", "mild", "moderate"),
        ("mild", "severe", "severe"),
        ("unknown", "mild", "mild"),
        ("severe", "unknown", "severe"),
    ],
)
def test_severity_takes_the_max(old: str, new: str, expected: str) -> None:
    assert max_severity(old, new) == expected
    r = merge_symptom(_row(severity=old), _item(severity=new), NOW)
    assert r.values["severity"] == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("new", "ongoing"),
        ("ongoing", "ongoing"),
        ("improved", "improved"),
        ("resolved", "resolved"),
    ],
)
def test_merged_status(status: str, expected: str) -> None:
    assert merge_symptom(_row(), _item(status=status), NOW).values["status"] == expected


def test_merge_keeps_earlier_details_when_not_repeated() -> None:
    r = merge_symptom(_row(), _item(body_part=None, duration=None, onset="at night"), NOW)
    assert r.values["body_part"] == "head"
    assert r.values["duration"] == "2 days"
    assert r.values["onset"] == "at night"


def test_other_merges_by_label() -> None:
    rash = _row(canonical="other", label="Itchy rash")
    assert not merge_symptom(rash, _item(canonical="other", label="itchy rash "), NOW).is_new
    assert merge_symptom(rash, _item(canonical="other", label="sore throat"), NOW).is_new


def test_different_canonical_never_merges() -> None:
    assert merge_symptom(_row(), _item(canonical="headache"), NOW).is_new


def test_find_match_picks_latest_same_symptom_in_window() -> None:
    old = _row(id=1, hours_ago=30)
    recent = _row(id=2, hours_ago=5)
    latest = _row(id=3, hours_ago=1)
    other_symptom = _row(id=4, hours_ago=0.5, canonical="headache")
    candidates = [old, recent, latest, other_symptom]
    assert find_match(candidates, _item(), NOW, WINDOW) is latest
    assert find_match([old], _item(), NOW, WINDOW) is None
    assert find_match([], _item(), NOW, WINDOW) is None
