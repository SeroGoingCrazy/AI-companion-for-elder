from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from elder_companion.dashboard import day_label, group_by_day, local_day_bounds
from elder_companion.models import SymptomLog

pytestmark = pytest.mark.unit

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 9, 29, 9, 0, tzinfo=LA)  # Tuesday morning, UTC-7


def _row(last_seen_utc: datetime, canonical: str = "dizziness") -> SymptomLog:
    return SymptomLog(canonical=canonical, label=canonical, last_seen=last_seen_utc)


def test_local_day_bounds_are_naive_utc() -> None:
    start, end = local_day_bounds(date(2026, 9, 29), NOW)
    assert start == datetime(2026, 9, 29, 7, 0)
    assert end == datetime(2026, 9, 30, 7, 0)


def test_local_day_bounds_across_dst_change() -> None:
    # 2026-11-01 is 25 hours long in Los Angeles (PDT -> PST)
    start, end = local_day_bounds(date(2026, 11, 1), NOW)
    assert (end - start).total_seconds() == 25 * 3600


@pytest.mark.parametrize(
    ("day", "label"),
    [
        (date(2026, 9, 29), "Today"),
        (date(2026, 9, 28), "Yesterday"),
        (date(2026, 9, 26), "Sat, Sep 26"),
    ],
)
def test_day_label(day: date, label: str) -> None:
    assert day_label(day, date(2026, 9, 29)) == label


def test_group_by_day_uses_the_elder_calendar() -> None:
    late_evening = _row(datetime(2026, 9, 29, 6, 30), "insomnia")  # 23:30 on the 28th in LA
    this_morning = _row(datetime(2026, 9, 29, 15, 0), "dizziness")  # 08:00 on the 29th
    earlier_today = _row(datetime(2026, 9, 29, 14, 0), "cough")
    too_old = _row(datetime(2026, 9, 20, 12, 0))
    groups = group_by_day([late_evening, earlier_today, this_morning, too_old], NOW, days=3)
    assert [(g.label, [s.canonical for s in g.symptoms]) for g in groups] == [
        ("Today", ["dizziness", "cough"]),  # newest first within a day
        ("Yesterday", ["insomnia"]),
        ("Sun, Sep 27", []),  # empty days are kept
    ]
