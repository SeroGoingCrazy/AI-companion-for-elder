from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from elder_companion.alerts.schemas import AlertIn
from elder_companion.elders import get_elder
from elder_companion.models import Alert, SymptomLog


def create_alert(session: Session, data: AlertIn) -> Alert:
    elder = get_elder(session, data.elder_id)
    alert = Alert(**data.model_dump(exclude={"elder_id"}), elder_id=elder.id)
    session.add(alert)
    session.commit()
    return alert


class AlertNotFound(LookupError):
    pass


def mark_read(session: Session, alert_id: int) -> Alert:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise AlertNotFound(alert_id)
    alert.is_read = True
    session.commit()
    return alert


def last_symptom_alert_at(session: Session, elder_id: int, canonical: str) -> datetime | None:
    """When the last symptom alert for `canonical` was raised (symptom alerts keep the
    symptom_log id in ref_id). Used to debounce repeat alerts."""
    stmt = (
        select(func.max(Alert.created_at))
        .join(SymptomLog, Alert.ref_id == cast(SymptomLog.id, String))
        .where(
            Alert.elder_id == elder_id,
            Alert.type == "symptom",
            SymptomLog.canonical == canonical,
        )
    )
    return session.scalar(stmt)
