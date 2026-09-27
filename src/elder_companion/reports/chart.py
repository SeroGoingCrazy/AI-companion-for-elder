"""Geometry for the weekly mood line (H7).

The page has no build step and no chart library (ADR 10), so the SVG is drawn from plain
coordinates computed here. Keeping the arithmetic out of the template makes it testable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

MOOD_MIN, MOOD_MAX = 1, 5


@dataclass(frozen=True)
class ChartBox:
    width: float = 640
    height: float = 180
    pad_x: float = 30
    pad_top: float = 14
    pad_bottom: float = 26


@dataclass(frozen=True)
class MoodDot:
    x: float
    y: float
    score: int
    label: str
    index: int


def _x(index: int, count: int, box: ChartBox) -> float:
    if count <= 1:
        return box.width / 2
    span = box.width - 2 * box.pad_x
    return box.pad_x + span * index / (count - 1)


def _y(score: int, box: ChartBox) -> float:
    span = box.height - box.pad_top - box.pad_bottom
    ratio = (score - MOOD_MIN) / (MOOD_MAX - MOOD_MIN)
    return box.pad_top + span * (1 - ratio)


def mood_points(moods: Sequence, box: ChartBox | None = None) -> list[MoodDot]:
    """One dot per day she chatted. Days with no score are left out, so the line skips them
    instead of pretending she was neutral."""
    box = box or ChartBox()
    count = len(moods)
    return [
        MoodDot(_x(i, count, box), _y(m.score, box), m.score, m.label, i)
        for i, m in enumerate(moods)
        if m.score is not None
    ]


def mood_path(moods: Sequence, box: ChartBox | None = None) -> str:
    """An SVG polyline `points` string through the days that have a score."""
    return " ".join(f"{d.x:.1f},{d.y:.1f}" for d in mood_points(moods, box))


def gridlines(box: ChartBox | None = None) -> list[tuple[int, float]]:
    """(score, y) for each of the five mood levels."""
    box = box or ChartBox()
    return [(score, _y(score, box)) for score in range(MOOD_MIN, MOOD_MAX + 1)]
