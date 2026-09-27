"""Today's summary for the family dashboard (spec 5.4).

Since H7 the text comes from that day's `daily_digest` row (spec 3.10), so the dashboard and
the weekly report agree and a day is summarized once. Privacy is handled in the digest: private
messages never reach the model, and a day with a private segment ends with a fixed note added
in code.

The digest row and its fingerprint are the cache: a stored digest is reused until the day's
messages, symptoms, alerts or privacy marks change. There is deliberately no TTL cache in
front of that. One would serve a stale summary for minutes after she says something, which is
exactly the moment the family is looking at the page.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from elder_companion.db import utcnow
from elder_companion.elders import get_elder
from elder_companion.llm import BaseLLMClient
from elder_companion.privacy import DEFAULT_BYPASS_LEVELS
from elder_companion.reports.digest import EMPTY_SUMMARY, DigestService, DigestView

__all__ = ["EMPTY_SUMMARY", "DailySummary", "SummaryResult"]


@dataclass(frozen=True)
class SummaryResult:
    summary: str
    generated_at: datetime  # naive UTC
    fallback: bool = False  # True when the LLM failed and a plain rule-based summary was used
    empty: bool = False  # True when she has not chatted today
    has_private: bool = False  # part of today's conversation was private (the note is added)

    @classmethod
    def of(cls, digest: DigestView) -> SummaryResult:
        return cls(
            summary=digest.summary,
            generated_at=digest.generated_at,
            fallback=digest.fallback,
            empty=digest.empty,
            has_private=digest.has_private,
        )


class DailySummary:
    def __init__(
        self,
        llm: BaseLLMClient,
        *,
        companion_name: str,
        clock: Callable[[], datetime] = utcnow,
        bypass_levels: Collection[str] = DEFAULT_BYPASS_LEVELS,
    ) -> None:
        self._digests = DigestService(
            llm, companion_name=companion_name, bypass_levels=bypass_levels, clock=clock
        )

    def get(
        self, session: Session, elder_id: int | None, now: datetime, *, refresh: bool = False
    ) -> SummaryResult:
        """Summary of the elder's local day containing `now` (an aware datetime in her zone)."""
        elder = get_elder(session, elder_id)
        digest = self._digests.get(session, elder, now.date(), now, refresh=refresh)
        return SummaryResult.of(digest)
