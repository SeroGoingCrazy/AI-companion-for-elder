"""H7: weekly aggregation from mock digests (pure functions, no DB and no model)."""

from datetime import date, datetime, timedelta

import pytest

from elder_companion.privacy import MentionView, SymptomView
from elder_companion.reports.chart import ChartBox, gridlines, mood_path, mood_points
from elder_companion.reports.digest import DigestView, clamp_mood, normalize_topics
from elder_companion.reports.weekly import (
    average_mood,
    mood_direction,
    mood_line,
    symptom_stats,
    topic_counts,
    week_days,
)

pytestmark = pytest.mark.unit

END = date(2026, 9, 26)
DAYS = week_days(END)


def digest(day: date, mood: int | None, topics=(), has_private: bool = False) -> DigestView:  # noqa: ANN001
    return DigestView(
        date=day,
        summary="...",
        mood_score=mood,
        topics=tuple(topics),
        has_private=has_private,
        generated_at=datetime(2026, 9, 26, 12),
        empty=mood is None,
    )


def mention(at: datetime, severity: str = "unknown") -> MentionView:
    return MentionView(message_id=1, raw_quote="q", severity=severity, created_at=at)


def symptom(canonical: str, label: str, mentions, *, status="new", red_flag=False) -> SymptomView:  # noqa: ANN001
    return SymptomView(
        id=abs(hash((canonical, label))) % 1000,
        canonical=canonical,
        label=label,
        body_part=None,
        severity=mentions[-1].severity if mentions else "unknown",
        duration=None,
        onset=None,
        status=status,
        raw_quote="q",
        count=len(mentions),
        first_seen=mentions[0].created_at,
        last_seen=mentions[-1].created_at,
        red_flag=red_flag,
        mentions=tuple(mentions),
    )


# ---------- the week window ----------


def test_week_is_seven_days_ending_today_oldest_first() -> None:
    assert len(DAYS) == 7 and DAYS[-1] == END and DAYS[0] == date(2026, 9, 20)


# ---------- mood ----------


def test_mood_line_keeps_a_slot_for_every_day() -> None:
    points = mood_line([digest(DAYS[0], 4), digest(DAYS[3], 2)], DAYS)
    assert len(points) == 7
    assert [p.score for p in points] == [4, None, None, 2, None, None, None]
    assert points[0].label == "Sun"


def test_average_ignores_days_she_did_not_chat() -> None:
    points = mood_line([digest(DAYS[0], 4), digest(DAYS[1], 2)], DAYS)
    assert average_mood(points) == 3.0


def test_average_of_an_empty_week_is_none() -> None:
    assert average_mood(mood_line([], DAYS)) is None


@pytest.mark.parametrize(
    "scores,expected",
    [
        ([1, 1, 1, 5, 5, 5], "better"),
        ([5, 5, 5, 1, 1, 1], "worse"),
        ([3, 3, 3, 3, 3, 3], "same"),
        ([3, 3, 3, 3, 4, 3], "same"),  # a small wobble is not a direction
        ([4], "same"),  # one day says nothing about direction
    ],
)
def test_mood_direction(scores: list[int], expected: str) -> None:
    digests = [digest(DAYS[i], s) for i, s in enumerate(scores)]
    assert mood_direction(mood_line(digests, DAYS)) == expected


def test_a_private_day_is_marked_on_the_line() -> None:
    points = mood_line([digest(DAYS[2], 3, has_private=True)], DAYS)
    assert points[2].has_private and not points[0].has_private


# ---------- topics ----------


def test_topics_count_days_not_mentions_and_sort_by_days() -> None:
    digests = [
        digest(DAYS[0], 3, ["garden", "knee"]),
        digest(DAYS[1], 3, ["knee"]),
        digest(DAYS[2], 3, ["knee", "Leo"]),
    ]
    # Ties break alphabetically, so the order is stable between page loads.
    assert [(t.topic, t.days) for t in topic_counts(digests)] == [
        ("knee", 3),
        ("garden", 1),
        ("Leo", 1),
    ]


def test_topics_are_grouped_case_insensitively() -> None:
    digests = [digest(DAYS[0], 3, ["Garden"]), digest(DAYS[1], 3, ["garden"])]
    [top] = topic_counts(digests)
    assert top.days == 2


def test_topics_are_capped() -> None:
    digests = [digest(DAYS[0], 3, [f"t{i}" for i in range(9)])]
    assert len(topic_counts(digests, limit=5)) == 5


def test_normalize_topics_trims_dedupes_and_caps() -> None:
    assert normalize_topics(["  garden ", "Garden", "", "knee", "sleep", "Leo", "tea"]) == (
        "garden",
        "knee",
        "sleep",
        "Leo",
    )


@pytest.mark.parametrize("raw,expected", [(0, 1), (1, 1), (3, 3), (5, 5), (9, 5), (-2, 1)])
def test_mood_score_is_clamped(raw: int, expected: int) -> None:
    assert clamp_mood(raw) == expected


# ---------- symptoms ----------


def test_log_rows_of_one_symptom_become_one_line() -> None:
    """Four knee rows across a week are one problem, not four."""
    t = datetime(2026, 9, 20, 9)
    rows = [
        symptom("joint_pain", "knee ache", [mention(t, "mild")]),
        symptom("joint_pain", "knee pain", [mention(t + timedelta(days=2), "severe")]),
        symptom("joint_pain", "knee", [mention(t + timedelta(days=4), "moderate")]),
        symptom("insomnia", "poor sleep", [mention(t + timedelta(days=1), "mild")]),
    ]
    stats = symptom_stats(rows)
    assert len(stats) == 2
    knee = next(s for s in stats if s.canonical == "joint_pain")
    assert (knee.days, knee.mentions, knee.max_severity) == (3, 3, "severe")
    assert knee.display == "Joint pain"


def test_red_flags_come_first() -> None:
    t = datetime(2026, 9, 24, 9)
    rows = [
        symptom("insomnia", "poor sleep", [mention(t), mention(t + timedelta(days=1))]),
        symptom("chest_pain", "tightness", [mention(t)], red_flag=True),
    ]
    assert [s.canonical for s in symptom_stats(rows)] == ["chest_pain", "insomnia"]


def test_other_symptoms_are_kept_apart_by_label() -> None:
    t = datetime(2026, 9, 24, 9)
    rows = [
        symptom("other", "itchy rash", [mention(t)]),
        symptom("other", "ringing ears", [mention(t)]),
    ]
    stats = symptom_stats(rows)
    assert {s.display for s in stats} == {"itchy rash", "ringing ears"}


@pytest.mark.parametrize(
    "severities,status,expected",
    [
        (["mild", "severe"], "ongoing", "worse"),
        (["severe", "mild"], "ongoing", "better"),
        (["mild", "mild"], "ongoing", "same"),
        (["severe"], "improved", "better"),
        (["mild"], "new", "new"),
    ],
)
def test_symptom_direction(severities: list[str], status: str, expected: str) -> None:
    t = datetime(2026, 9, 22, 9)
    mentions = [mention(t + timedelta(days=i), s) for i, s in enumerate(severities)]
    [stat] = symptom_stats([symptom("joint_pain", "knee", mentions, status=status)])
    assert stat.trend == expected


def test_days_counts_distinct_days_not_mentions() -> None:
    t = datetime(2026, 9, 22, 9)
    mentions = [mention(t), mention(t + timedelta(hours=2)), mention(t + timedelta(days=1))]
    [stat] = symptom_stats([symptom("joint_pain", "knee", mentions)])
    assert (stat.days, stat.mentions) == (2, 3)


# ---------- the mood chart ----------


def test_chart_skips_days_with_no_score() -> None:
    points = mood_line([digest(DAYS[0], 5), digest(DAYS[6], 1)], DAYS)
    dots = mood_points(points)
    assert len(dots) == 2
    box = ChartBox()
    assert dots[0].x == pytest.approx(box.pad_x)  # first day, left edge
    assert dots[1].x == pytest.approx(box.width - box.pad_x)  # last day, right edge
    assert dots[0].y < dots[1].y  # 5 sits above 1


def test_chart_path_has_one_pair_per_scored_day() -> None:
    points = mood_line([digest(d, 3) for d in DAYS[:3]], DAYS)
    assert len(mood_path(points).split(" ")) == 3


def test_an_empty_week_draws_no_line() -> None:
    assert mood_path(mood_line([], DAYS)) == ""


def test_gridlines_cover_every_mood_level_top_to_bottom() -> None:
    lines = gridlines()
    assert [score for score, _ in lines] == [1, 2, 3, 4, 5]
    assert lines[0][1] > lines[-1][1]  # 1 is drawn below 5
