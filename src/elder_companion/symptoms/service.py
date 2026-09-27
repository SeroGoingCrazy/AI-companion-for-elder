"""Symptom pipeline for one elder message: extract -> merge -> alert (spec 3.4.3, 5.4)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from elder_companion.alerts.bus import AlertBus, StreamEvent
from elder_companion.alerts.schemas import AlertIn, AlertLevel, AlertOut
from elder_companion.alerts.service import last_symptom_alert_at
from elder_companion.db import utcnow
from elder_companion.llm import ChatMessage, LLMError
from elder_companion.models import Alert, Elder, Message, SymptomLog, SymptomMention
from elder_companion.settings import SymptomSettings
from elder_companion.symptoms.extractor import SymptomExtractor
from elder_companion.symptoms.merge import find_match, merge_symptom
from elder_companion.symptoms.rules import alert_level, should_alert
from elder_companion.symptoms.schema import SymptomCatalog, SymptomItem, get_catalog

logger = logging.getLogger(__name__)

CONTEXT_TURNS = 3  # earlier user+assistant pairs given to the extractor for "it", "still" ...
MAX_QUOTE_CHARS = 300


def alert_text(
    item: SymptomItem, elder: Elder, utterance: str, catalog: SymptomCatalog
) -> tuple[str, str]:
    """(title, content) for the family, e.g. ("Chest pain", 'Maggie said: "..." ...')."""
    title = item.label if item.canonical == "other" else catalog.get(item.canonical).en
    said = utterance if len(utterance) <= MAX_QUOTE_CHARS else utterance[:MAX_QUOTE_CHARS] + "…"
    details = [
        f"severity: {item.severity}" if item.severity != "unknown" else None,
        f"for {item.duration}" if item.duration else None,
        f"status: {item.status}" if item.status != "new" else None,
    ]
    content = f'{elder.nickname} said: "{said}"'
    if extra := [d for d in details if d]:
        content += f" ({', '.join(extra)})"
    return title, content


@dataclass
class ProcessResult:
    symptoms: list[SymptomLog] = field(default_factory=list)  # rows inserted or updated
    alerts: list[Alert] = field(default_factory=list)


class SymptomService:
    def __init__(
        self,
        session: Session,
        extractor: SymptomExtractor,
        settings: SymptomSettings,
        catalog: SymptomCatalog | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session = session
        self._extractor = extractor
        self._catalog = catalog or get_catalog()
        self._window = timedelta(hours=settings.merge_window_hours)
        self._debounce = timedelta(hours=settings.alert_debounce_hours)
        self._now = now

    def process_message(self, message_id: int) -> ProcessResult:
        """Log the symptoms in one elder message and raise any alerts they call for.
        Raises LLMError if extraction fails (nothing is written then)."""
        result = ProcessResult()
        msg = self._session.get(Message, message_id)
        if msg is None or msg.role != "user":
            return result
        items = self._extractor.extract(msg.text, self._recent_turns(msg))
        if not items:
            return result
        elder = self._session.get_one(Elder, msg.elder_id)
        now = self._now()
        for item in items:
            row = self._merge(msg, item, now)
            result.symptoms.append(row)
            level = alert_level(item.canonical, item.severity, item.status, self._catalog)
            if level and should_alert(
                last_symptom_alert_at(self._session, elder.id, item.canonical), now, self._debounce
            ):
                result.alerts.append(self._alert(elder, msg, item, row, level, now))
        self._session.commit()
        return result

    def _recent_turns(self, msg: Message) -> list[ChatMessage]:
        stmt = (
            select(Message)
            .where(Message.elder_id == msg.elder_id, Message.id < msg.id)
            .order_by(Message.id.desc())
            .limit(2 * CONTEXT_TURNS)
        )
        rows = reversed(self._session.scalars(stmt).all())
        return [{"role": m.role, "content": m.text} for m in rows]

    def _merge(self, msg: Message, item: SymptomItem, now: datetime) -> SymptomLog:
        stmt = select(SymptomLog).where(
            SymptomLog.elder_id == msg.elder_id,
            SymptomLog.canonical == item.canonical,
            SymptomLog.last_seen >= now - self._window,
        )
        existing = find_match(self._session.scalars(stmt), item, now, self._window)
        result = merge_symptom(existing, item, now, message_id=msg.id, window=self._window)
        if result.is_new:
            row = SymptomLog(elder_id=msg.elder_id, **result.values)
            self._session.add(row)
        else:
            row = existing
            for key, value in result.values.items():
                setattr(row, key, value)
        self._session.flush()  # assigns row.id for the alert's ref_id
        # One row per occurrence: the family view counts and quotes only visible ones.
        self._session.add(
            SymptomMention(
                symptom_log_id=row.id,
                message_id=msg.id,
                raw_quote=item.raw_quote,
                severity=item.severity,
                created_at=now,
            )
        )
        return row

    def _alert(
        self,
        elder: Elder,
        msg: Message,
        item: SymptomItem,
        row: SymptomLog,
        level: AlertLevel,
        now: datetime,
    ) -> Alert:
        title, content = alert_text(item, elder, msg.text, self._catalog)
        data = AlertIn(
            type="symptom", level=level, title=title, content=content, ref_id=str(row.id)
        )
        # created_at uses the service clock so debounce comparisons stay consistent.
        alert = Alert(
            **data.model_dump(exclude={"elder_id"}),
            elder_id=elder.id,
            message_id=msg.id,
            created_at=now,
        )
        self._session.add(alert)
        self._session.flush()
        return alert


def run_symptom_pipeline(
    session_factory: sessionmaker[Session],
    extractor: SymptomExtractor,
    settings: SymptomSettings,
    bus: AlertBus | None,
    message_id: int,
    publish_now: Callable[[AlertOut], bool] | None = None,
) -> tuple[list[AlertOut], int]:
    """Extract, merge and alert; publish each new alert right away (only those `publish_now`
    accepts, when given: the caller publishes the rest). Never raises.
    Returns (alerts, number of symptom rows touched)."""
    outs: list[AlertOut] = []
    symptoms = 0
    try:
        with session_factory() as session:
            result = SymptomService(session, extractor, settings).process_message(message_id)
            outs = [AlertOut.model_validate(a) for a in result.alerts]
            symptoms = len(result.symptoms)
    except LLMError:
        logger.warning("symptom extraction failed for message %s", message_id, exc_info=True)
    except Exception:
        logger.exception("symptom pipeline crashed for message %s", message_id)
    if bus is not None:
        for out in outs:
            if publish_now is None or publish_now(out):
                bus.publish(StreamEvent.alert(out))
    return outs, symptoms


def process_message_symptoms(
    session_factory: sessionmaker[Session],
    extractor: SymptomExtractor,
    settings: SymptomSettings,
    bus: AlertBus | None,
    message_id: int,
) -> list[AlertOut]:
    """Background task after a chat turn. Never raises: a failure here must not affect chat.
    Publishes each new alert, then an `activity` event (also after a failed extraction: the
    message itself is new for the dashboard's history)."""
    outs, symptoms = run_symptom_pipeline(session_factory, extractor, settings, bus, message_id)
    if bus is not None:
        bus.publish(StreamEvent.activity(message_id, symptoms))
    return outs
