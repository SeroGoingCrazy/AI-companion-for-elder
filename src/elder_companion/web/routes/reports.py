"""Printable family pages (spec 3.10): the doctor one-pager and the memoir."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from elder_companion.reports.chart import mood_path, mood_points
from elder_companion.reports.doctor import build_doctor_report
from elder_companion.reports.memoir import build_memoir
from elder_companion.web.deps import SessionDep
from elder_companion.web.routes.family import elder_now, elder_or_404
from elder_companion.web.routes.pages import templates

router = APIRouter(include_in_schema=False)


def fmt_date(dt: datetime) -> str:
    return f"{dt:%b} {dt.day}, {dt.year}"


def fmt_when(dt: datetime) -> str:
    """e.g. "Sep 26, 3:05 PM" (no platform-specific strftime flags)."""
    return f"{dt:%b} {dt.day}, {dt.hour % 12 or 12}:{dt:%M} {dt:%p}"


@router.get("/family/doctor", response_class=HTMLResponse)
def doctor_page(
    request: Request,
    session: SessionDep,
    days: int = Query(30, ge=1, le=365),
    elder_id: int | None = None,
) -> HTMLResponse:
    elder = elder_or_404(session, elder_id)
    report = build_doctor_report(
        session,
        elder,
        elder_now(request),
        days,
        bypass_levels=request.app.state.settings.privacy.bypass_levels,
    )
    return templates.TemplateResponse(
        request,
        "doctor.html",
        {"r": report, "fmt_date": fmt_date, "fmt_when": fmt_when},
    )


@router.get("/family/report/weekly", response_class=HTMLResponse)
def weekly_report_page(
    request: Request,
    session: SessionDep,
    week: int = Query(0, ge=0, le=52, description="0 = the last 7 days, 1 = the week before"),
    elder_id: int | None = None,
) -> HTMLResponse:
    """One printable page: mood line, topics, symptom trends and reminder adherence."""
    elder = elder_or_404(session, elder_id)
    report = request.app.state.weekly_report.get(
        session, elder, elder_now(request), weeks_back=week
    )
    return templates.TemplateResponse(
        request,
        "report_weekly.html",
        {
            "elder": elder,
            "r": report,
            "week": week,
            "mood_path": mood_path,
            "mood_points": mood_points,
            "fmt_date": fmt_date,
        },
    )


@router.get("/family/memoir", response_class=HTMLResponse)
def memoir_page(request: Request, session: SessionDep, elder_id: int | None = None) -> HTMLResponse:
    elder = elder_or_404(session, elder_id)
    entries = build_memoir(session, request.app.state.llm, elder, elder_now(request))
    return templates.TemplateResponse(
        request,
        "memoir.html",
        {"elder": elder, "entries": entries, "fmt_date": fmt_date},
    )
