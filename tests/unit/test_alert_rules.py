from datetime import datetime, timedelta

import pytest

from elder_companion.symptoms.rules import alert_level, should_alert
from elder_companion.symptoms.schema import SymptomCatalog, SymptomDef

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 29, 15, 0)


@pytest.mark.parametrize(
    ("canonical", "severity", "status", "expected"),
    [
        ("chest_pain", "unknown", "new", "high"),
        ("shortness_of_breath", "mild", "new", "high"),
        ("fall", "unknown", "resolved", "high"),  # "I fell, but I'm fine now" still alerts
        ("self_harm", "unknown", "new", "high"),
        ("dizziness", "severe", "new", "medium"),
        ("other", "severe", "ongoing", "medium"),
        ("dizziness", "severe", "resolved", None),
        ("dizziness", "moderate", "new", None),
        ("dizziness", "unknown", "new", None),
        ("joint_pain", "mild", "improved", None),
    ],
)
def test_alert_level(canonical: str, severity: str, status: str, expected: str | None) -> None:
    assert alert_level(canonical, severity, status) == expected


def test_alert_level_reads_the_catalog() -> None:
    catalog = SymptomCatalog(
        [
            SymptomDef("hiccups", "Hiccups", "打嗝", True),
            SymptomDef("other", "Other", "其他", False),
        ]
    )
    assert alert_level("hiccups", "mild", catalog=catalog) == "high"
    assert alert_level("chest_pain", "mild", catalog=catalog) is None  # not in this catalog


@pytest.mark.parametrize(
    ("last_alert", "expected"),
    [
        (None, True),
        (NOW - timedelta(minutes=30), False),
        (NOW - timedelta(hours=1, minutes=59), False),
        (NOW - timedelta(hours=2), True),
        (NOW - timedelta(days=1), True),
    ],
)
def test_debounce(last_alert: datetime | None, expected: bool) -> None:
    assert should_alert(last_alert, NOW, timedelta(hours=2)) is expected
