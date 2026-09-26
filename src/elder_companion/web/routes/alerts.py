from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from elder_companion.alerts.schemas import AlertIn, AlertOut
from elder_companion.alerts.service import ElderNotFound, create_alert, list_alerts
from elder_companion.web.deps import SessionDep

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.post("", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
def post_alert(body: AlertIn, session: SessionDep) -> AlertOut:
    """Create an alert. Called by fall-mcp (type=fall) and the symptom pipeline."""
    try:
        alert = create_alert(session, body)
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return AlertOut.model_validate(alert)


@router.get("", response_model=list[AlertOut])
def get_alerts(session: SessionDep, limit: int = Query(50, ge=1, le=200)) -> list[AlertOut]:
    return [AlertOut.model_validate(a) for a in list_alerts(session, limit=limit)]
