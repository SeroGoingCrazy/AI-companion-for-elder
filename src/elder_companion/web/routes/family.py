"""Family dashboard data: symptom timeline and conversation history (spec 5.5).

Every read goes through `privacy` (spec 3.9)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from elder_companion.dashboard import symptom_timeline
from elder_companion.db import UtcDateTime
from elder_companion.elders import ElderNotFound, get_elder
from elder_companion.family_loop.claims import (
    ClaimError,
    create_claim,
    list_claims,
    list_members,
    mark_done,
)
from elder_companion.family_loop.reminders import (
    ReminderError,
    ReminderView,
    create_reminder,
    deactivate,
    list_with_adherence,
)
from elder_companion.memory.care_list import CARE_KINDS, care_list
from elder_companion.models import Elder
from elder_companion.privacy import SymptomView, history_page, visible_memory
from elder_companion.symptoms.schema import Severity, Status, get_catalog
from elder_companion.web.deps import SessionDep

router = APIRouter(prefix="/api", tags=["family"])


class SymptomOut(BaseModel):
    id: int
    canonical: str
    label: str
    display_en: str
    display_zh: str
    red_flag: bool
    body_part: str | None
    severity: Severity
    duration: str | None
    onset: str | None
    status: Status
    raw_quote: str
    count: int
    first_seen: UtcDateTime
    last_seen: UtcDateTime

    @classmethod
    def of(cls, row: SymptomView) -> SymptomOut:
        catalog = get_catalog()
        d = catalog.get(row.canonical) if row.canonical in catalog else catalog.get("other")
        return cls(
            id=row.id,
            canonical=row.canonical,
            label=row.label,
            display_en=d.en,
            display_zh=d.zh,
            red_flag=d.red_flag,
            body_part=row.body_part,
            severity=row.severity,
            duration=row.duration,
            onset=row.onset,
            status=row.status,
            raw_quote=row.raw_quote,
            count=row.count,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
        )


class DayOut(BaseModel):
    date: date
    label: str  # "Today", "Yesterday", "Sat, Sep 26" (elder's calendar)
    symptoms: list[SymptomOut]


class TimelineOut(BaseModel):
    timezone: str
    days: list[DayOut]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    text: str  # "" when private
    private: bool = False
    created_at: UtcDateTime


def elder_or_404(session: SessionDep, elder_id: int | None) -> Elder:
    try:
        return get_elder(session, elder_id)
    except ElderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def elder_now(request: Request) -> datetime:
    return datetime.now(request.app.state.settings.chat.tz)


@router.get("/symptoms", response_model=TimelineOut)
def get_symptoms(
    request: Request,
    session: SessionDep,
    days: int = Query(7, ge=1, le=31),
    elder_id: int | None = None,
) -> TimelineOut:
    """Symptom log grouped by the elder's calendar day, newest day first (empty days included)."""
    elder = elder_or_404(session, elder_id)
    groups = symptom_timeline(
        session,
        elder.id,
        elder_now(request),
        days,
        request.app.state.settings.privacy.bypass_levels,
    )
    return TimelineOut(
        timezone=request.app.state.settings.chat.timezone,
        days=[
            DayOut(date=g.date, label=g.label, symptoms=[SymptomOut.of(s) for s in g.symptoms])
            for g in groups
        ],
    )


@router.get("/messages", response_model=list[MessageOut])
def get_messages(
    session: SessionDep,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None, description="page back: messages older than this id"),
    elder_id: int | None = None,
) -> list[MessageOut]:
    """Conversation history, oldest first; private messages are placeholders."""
    elder = elder_or_404(session, elder_id)
    return [MessageOut.model_validate(m) for m in history_page(session, elder.id, limit, before_id)]


class SummaryOut(BaseModel):
    summary: str
    generated_at: UtcDateTime
    fallback: bool
    empty: bool
    has_private: bool = False


@router.get("/summary/today", response_model=SummaryOut)
def get_today_summary(
    request: Request,
    session: SessionDep,
    refresh: bool = Query(False, description="regenerate even if the cached summary is current"),
    elder_id: int | None = None,
) -> SummaryOut:
    """2-3 sentence summary of the elder's day; cached while nothing new has happened."""
    elder = elder_or_404(session, elder_id)
    r = request.app.state.daily_summary.get(session, elder.id, elder_now(request), refresh=refresh)
    return SummaryOut(
        summary=r.summary,
        generated_at=r.generated_at,
        fallback=r.fallback,
        empty=r.empty,
        has_private=r.has_private,
    )


class CareItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str  # person | topic
    subject: str
    text: str
    raw_quote: str  # her latest words about it ("" when those were private)
    mention_count: int
    first_seen: UtcDateTime
    last_seen: UtcDateTime


@router.get("/care-list", response_model=list[CareItemOut])
def get_care_list(
    request: Request, session: SessionDep, elder_id: int | None = None
) -> list[CareItemOut]:
    """People and topics she keeps mentioning, most recent first (private ones never)."""
    elder = elder_or_404(session, elder_id)
    items = care_list(
        visible_memory(session, elder.id, CARE_KINDS),
        request.app.state.settings.memory.care_list_min_mentions,
    )
    return [CareItemOut.model_validate(i) for i in items]


# ---------- sibling sharing (spec 2.6) ----------


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    relation: str


@router.get("/family/members", response_model=list[MemberOut])
def get_members(session: SessionDep, elder_id: int | None = None) -> list[MemberOut]:
    elder = elder_or_404(session, elder_id)
    return [MemberOut.model_validate(m) for m in list_members(session, elder.id)]


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["alert", "memory_item", "symptom_log"]
    target_id: int
    member_id: int
    note: str = Field(default="", max_length=500)
    elder_id: int | None = None


class ClaimOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_type: str
    target_id: int
    member_id: int
    member_name: str
    note: str
    created_at: UtcDateTime
    done_at: UtcDateTime | None


@router.get("/claims", response_model=list[ClaimOut])
def get_claims(session: SessionDep, elder_id: int | None = None) -> list[ClaimOut]:
    """Who is handling what, newest first (done ones included, with done_at)."""
    elder = elder_or_404(session, elder_id)
    return [ClaimOut.model_validate(c) for c in list_claims(session, elder.id)]


@router.post("/claims", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
def post_claim(body: ClaimIn, session: SessionDep) -> ClaimOut:
    """ "Ben: I'll call her doctor" on an alert, care-list item or symptom."""
    elder = elder_or_404(session, body.elder_id)
    try:
        claim = create_claim(
            session,
            elder.id,
            target_type=body.target_type,
            target_id=body.target_id,
            member_id=body.member_id,
            note=body.note,
        )
    except ClaimError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return ClaimOut.model_validate(claim)


@router.post("/claims/{claim_id}/done", response_model=ClaimOut)
def post_claim_done(claim_id: int, session: SessionDep) -> ClaimOut:
    try:
        return ClaimOut.model_validate(mark_done(session, claim_id))
    except ClaimError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


class DayStatusOut(BaseModel):
    date: date
    status: str | None  # None = not raised that day


class ReminderOut(BaseModel):
    id: int
    text: str
    schedule_time: str | None
    schedule_date: date | None
    active: bool
    from_member_id: int | None
    from_member_name: str | None
    created_at: UtcDateTime
    today_status: str | None
    history: list[DayStatusOut]  # oldest first
    confirmed_days: int
    raised_days: int

    @classmethod
    def of(cls, view: ReminderView) -> ReminderOut:
        return cls(
            **{k: v for k, v in vars(view).items() if k != "history"},
            history=[DayStatusOut(date=d.date, status=d.status) for d in view.history],
        )


class ReminderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    from_member_id: int | None = None
    schedule_time: str | None = Field(default=None, description="daily, HH:MM in her time zone")
    schedule_date: date | None = Field(default=None, description="one-off, her local date")
    elder_id: int | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v.strip()

    @model_validator(mode="after")
    def _one_schedule(self) -> ReminderIn:
        if (self.schedule_time is None) == (self.schedule_date is None):
            raise ValueError("give exactly one of schedule_time or schedule_date")
        return self


@router.get("/reminders", response_model=list[ReminderOut])
def get_reminders(
    request: Request, session: SessionDep, elder_id: int | None = None
) -> list[ReminderOut]:
    """Reminders with the last 7 local days of adherence, active ones first."""
    elder = elder_or_404(session, elder_id)
    views = list_with_adherence(session, elder.id, elder_now(request).date())
    return [ReminderOut.of(v) for v in views]


@router.post("/reminders", response_model=ReminderOut, status_code=status.HTTP_201_CREATED)
def post_reminder(body: ReminderIn, request: Request, session: SessionDep) -> ReminderOut:
    """Set a reminder. The companion raises it in the next greeting after it is due, using
    this wording and nothing else (spec 2.6)."""
    elder = elder_or_404(session, body.elder_id)
    try:
        created = create_reminder(
            session,
            elder.id,
            body.text,
            from_member_id=body.from_member_id,
            schedule_time=body.schedule_time,
            schedule_date=body.schedule_date,
        )
    except ReminderError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    views = list_with_adherence(session, elder.id, elder_now(request).date())
    return ReminderOut.of(next(v for v in views if v.id == created.id))


@router.delete("/reminders/{reminder_id}", response_model=ReminderOut)
def delete_reminder(
    reminder_id: int, request: Request, session: SessionDep, elder_id: int | None = None
) -> ReminderOut:
    """Deactivate a reminder. Its history stays, so adherence keeps its past days."""
    elder = elder_or_404(session, elder_id)
    try:
        deactivate(session, elder.id, reminder_id)
    except ReminderError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    views = list_with_adherence(session, elder.id, elder_now(request).date())
    return ReminderOut.of(next(v for v in views if v.id == reminder_id))
