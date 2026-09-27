from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette import EventSourceResponse, ServerSentEvent

from elder_companion.alerts.bus import StreamEvent
from elder_companion.alerts.schemas import AlertIn, AlertOut
from elder_companion.alerts.service import AlertNotFound, create_alert, mark_read
from elder_companion.elders import ElderNotFound
from elder_companion.i18n import LANG_COOKIE, resolve, strings
from elder_companion.models import DEFAULT_ELDER_ID, Alert, SymptomLog
from elder_companion.privacy import visible_alerts
from elder_companion.symptoms.schema import get_catalog
from elder_companion.web.deps import AlertBusDep, SessionDep

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

SSE_PING_SECONDS = 15  # keeps proxies and the browser from dropping an idle stream


def _fall_body(stored: str, t: dict[str, str]) -> str:
    """Rebuild fall-mcp's sentence in `lang`.

    fall_detector writes one fixed English sentence, optionally carrying how long the
    person has been down. The stored text stays the record; the only thing recovered from
    it is that number, which is why this reads it back out with a regex rather than
    translating prose.
    """
    match = re.search(r"for (\d+)s", stored)
    for_s = t["fall_alert_for"].format(n=match.group(1)) if match else ""
    return t["fall_alert_body"].format(for_s=for_s)


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

    fall = strings(lang)["family"]
    for item in out:
        if item.type == "fall":
            item.title = fall["fall_alert_title"]
            item.content = _fall_body(item.content, fall)

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
    """Newest first. Non-urgent symptom alerts from a private segment are left out.

    Privacy first, language second: what she asked to keep private must not reach the page
    in any language, so the filter runs before anything is translated for display.
    """
    bypass = request.app.state.settings.privacy.bypass_levels
    alerts = visible_alerts(session, DEFAULT_ELDER_ID, limit=limit, bypass_levels=bypass)
    lang = resolve(
        request.query_params.get("lang"),
        request.cookies.get(LANG_COOKIE),
        request.headers.get("accept-language"),
    )
    return _localized(session, list(alerts), lang)


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
        kind = payload.get("type")
        if kind == "fall":
            fall = strings(lang)["family"]
            payload["title"] = fall["fall_alert_title"]
            payload["content"] = _fall_body(payload.get("content") or "", fall)
            return json.dumps(payload)
        if kind != "symptom":
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
