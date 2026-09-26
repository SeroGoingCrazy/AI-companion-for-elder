from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.alerts.schemas import AlertIn
from elder_companion.elders import get_elder
from elder_companion.models import DEFAULT_ELDER_ID, Alert


def create_alert(session: Session, data: AlertIn) -> Alert:
    elder = get_elder(session, data.elder_id)
    alert = Alert(**data.model_dump(exclude={"elder_id"}), elder_id=elder.id)
    session.add(alert)
    session.commit()
    return alert


def list_alerts(session: Session, elder_id: int = DEFAULT_ELDER_ID, limit: int = 50) -> list[Alert]:
    stmt = (
        select(Alert)
        .where(Alert.elder_id == elder_id)
        .order_by(Alert.created_at.desc(), Alert.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))
