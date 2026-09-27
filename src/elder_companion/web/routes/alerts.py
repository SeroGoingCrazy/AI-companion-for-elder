from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette import EventSourceResponse, ServerSentEvent

from elder_companion.alerts.bus import StreamEvent
from elder_companion.alerts.schemas import AlertIn, AlertOut
from elder_companion.alerts.service import AlertNotFound, create_alert, list_alerts, mark_read
from elder_companion.elders import ElderNotFound
from elder_companion.i18n import LANG_COOKIE, resolve
from elder_companion.models import Alert, SymptomLog
from elder_companion.symptoms.schema import get_catalog
from elder_companion.web.deps import AlertBusDep, SessionDep

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

SSE_PING_SECONDS = 15  # keeps proxies and the browser from dropping an idle stream


def _localized_title(session: Session, ref_id: str | None, lang: str) -> str | None:
    """The symptom's name in `lang`, or None when there is nothing to localize."""
    if lang == "en" or not (ref_id or "").isdigit():
        return None
    row = session.get(SymptomLog, int(ref_id))
    if row is None or row.canonical == "other":
        return None
    catalog = get_catalog()
    return getattr(catalog.get(row.canonical), lang, None) if row.canonical in catalog else None


def _localized(session: Session, alerts: list[Alert], lang: str) -> list[AlertOut]:
    """Present stored alert titles in the reader's language.

    The stored title is the record of what happened and stays as written, in English.
    A symptom alert's `ref_id` is its SymptomLog row, and that row's canonical has a name
    in every language we ship (config/symptoms.yaml), so the title can be re-derived for
    display. Fall alerts and free-text "other" symptoms have no canonical and are left
    alone. Nothing is rewritten in the database.
    """
    out = [AlertOut.model_validate(a) for a in alerts]
    if lang == "en":
        return out

    ref_ids = {int(a.ref_id) for a in alerts if a.type == "symptom" and (a.ref_id or "").isdigit()}
    if not ref_ids:
        return out
    rows = session.execute(select(SymptomLog).where(SymptomLog.id.in_(ref_ids))).scalars()
    canonical_by_ref = {str(r.id): r.canonical for r in rows}

    catalog = get_catalog()
    for item in out:
        canonical = canonical_by_ref.get(item.ref_id or "")
        if canonical and canonical != "other" and canonical in catalog:
            item.title = getattr(catalog.get(canonical), lang, item.title)
    return out


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
def get_alerts(
    request: Request, session: SessionDep, limit: int = Query(50, ge=1, le=200)
) -> list[AlertOut]:
    lang = resolve(
        request.query_params.get("lang"),
        request.cookies.get(LANG_COOKIE),
        request.headers.get("accept-language"),
    )
    return _localized(session, list_alerts(session, limit=limit), lang)


@router.post("/{alert_id}/read", response_model=AlertOut)
def read_alert(alert_id: int, session: SessionDep) -> AlertOut:
    try:
        return AlertOut.model_validate(mark_read(session, alert_id))
    except AlertNotFound as e:
        raise HTTPException(status_code=404, detail=f"alert {alert_id} not found") from e


@router.get("/stream", response_class=EventSourceResponse)
async def stream_alerts(request: Request, bus: AlertBusDep) -> EventSourceResponse:
    """SSE for the dashboard, no replay (it loads GET /api/alerts first):
    `event: alert` + AlertOut JSON for every new alert; `event: activity` after each elder
    turn has been processed, so the timeline and history can reload.

    The language is fixed when the stream opens, from `?lang=` or the cookie. A session is
    opened per event rather than held for the life of the stream, which can be hours.
    """
    lang = resolve(
        request.query_params.get("lang"),
        request.cookies.get(LANG_COOKIE),
        request.headers.get("accept-language"),
    )
    sessionmaker = request.app.state.sessionmaker

    def translate(data: str) -> str:
        try:
            payload = json.loads(data)
        except (TypeError, ValueError):
            return data
        if payload.get("type") != "symptom":
            return data
        with sessionmaker() as session:
            title = _localized_title(session, payload.get("ref_id"), lang)
        if not title:
            return data
        payload["title"] = title
        return json.dumps(payload)

    async def events() -> AsyncIterator[ServerSentEvent]:
        with bus.subscribe() as queue:
            while True:
                ev: StreamEvent = await queue.get()
                data = translate(ev.data) if ev.event == "alert" and lang != "en" else ev.data
                yield ServerSentEvent(data=data, event=ev.event, id=ev.id)

    return EventSourceResponse(events(), ping=SSE_PING_SECONDS)
