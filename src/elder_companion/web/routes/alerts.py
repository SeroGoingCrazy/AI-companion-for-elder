from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, status
from sse_starlette import EventSourceResponse, ServerSentEvent

from elder_companion.alerts.bus import StreamEvent
from elder_companion.alerts.schemas import AlertIn, AlertOut
from elder_companion.alerts.service import AlertNotFound, create_alert, list_alerts, mark_read
from elder_companion.elders import ElderNotFound
from elder_companion.web.deps import AlertBusDep, SessionDep

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

SSE_PING_SECONDS = 15  # keeps proxies and the browser from dropping an idle stream


@router.post("", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
def post_alert(body: AlertIn, session: SessionDep, bus: AlertBusDep) -> AlertOut:
    """Create an alert. Called by fall-mcp (type=fall); also pushed to the SSE stream."""
    try:
        alert = create_alert(session, body)
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    out = AlertOut.model_validate(alert)
    bus.publish(StreamEvent.alert(out))
    return out


@router.get("", response_model=list[AlertOut])
def get_alerts(session: SessionDep, limit: int = Query(50, ge=1, le=200)) -> list[AlertOut]:
    return [AlertOut.model_validate(a) for a in list_alerts(session, limit=limit)]


@router.post("/{alert_id}/read", response_model=AlertOut)
def read_alert(alert_id: int, session: SessionDep) -> AlertOut:
    try:
        return AlertOut.model_validate(mark_read(session, alert_id))
    except AlertNotFound as e:
        raise HTTPException(status_code=404, detail=f"alert {alert_id} not found") from e


@router.get("/stream", response_class=EventSourceResponse)
async def stream_alerts(bus: AlertBusDep) -> EventSourceResponse:
    """SSE for the dashboard, no replay (it loads GET /api/alerts first):
    `event: alert` + AlertOut JSON for every new alert; `event: activity` after each elder
    turn has been processed, so the timeline and history can reload."""

    async def events() -> AsyncIterator[ServerSentEvent]:
        with bus.subscribe() as queue:
            while True:
                ev: StreamEvent = await queue.get()
                yield ServerSentEvent(data=ev.data, event=ev.event, id=ev.id)

    return EventSourceResponse(events(), ping=SSE_PING_SECONDS)
