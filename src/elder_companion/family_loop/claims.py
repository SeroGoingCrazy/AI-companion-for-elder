"""Sibling sharing (spec 2.6): family members and who is handling what."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.db import utcnow
from elder_companion.models import Alert, Claim, FamilyMember, MemoryItem, SymptomLog

TARGET_MODELS = {"alert": Alert, "memory_item": MemoryItem, "symptom_log": SymptomLog}


class ClaimError(LookupError):
    """The member, target or claim does not exist for this elder."""


@dataclass(frozen=True)
class ClaimView:
    id: int
    target_type: str
    target_id: int
    member_id: int
    member_name: str
    note: str
    created_at: datetime
    done_at: datetime | None


def list_members(session: Session, elder_id: int) -> list[FamilyMember]:
    stmt = select(FamilyMember).where(FamilyMember.elder_id == elder_id).order_by(FamilyMember.id)
    return list(session.scalars(stmt))


def _view(claim: Claim, member: FamilyMember) -> ClaimView:
    return ClaimView(
        claim.id,
        claim.target_type,
        claim.target_id,
        member.id,
        member.name,
        claim.note,
        claim.created_at,
        claim.done_at,
    )


def create_claim(
    session: Session,
    elder_id: int,
    *,
    target_type: str,
    target_id: int,
    member_id: int,
    note: str = "",
) -> ClaimView:
    member = session.get(FamilyMember, member_id)
    if member is None or member.elder_id != elder_id:
        raise ClaimError(f"family member {member_id} not found")
    model = TARGET_MODELS.get(target_type)
    target = session.get(model, target_id) if model else None
    if target is None or target.elder_id != elder_id or getattr(target, "private", False):
        raise ClaimError(f"{target_type} {target_id} not found")
    claim = Claim(
        elder_id=elder_id,
        target_type=target_type,
        target_id=target_id,
        member_id=member.id,
        note=note.strip(),
        created_at=utcnow(),
    )
    session.add(claim)
    session.commit()
    return _view(claim, member)


def mark_done(session: Session, claim_id: int) -> ClaimView:
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise ClaimError(f"claim {claim_id} not found")
    if claim.done_at is None:
        claim.done_at = utcnow()
        session.commit()
    return _view(claim, session.get_one(FamilyMember, claim.member_id))


def list_claims(session: Session, elder_id: int) -> list[ClaimView]:
    """Newest first. Claims on memory items that have since become private are left out."""
    stmt = (
        select(Claim, FamilyMember)
        .join(FamilyMember, Claim.member_id == FamilyMember.id)
        .where(Claim.elder_id == elder_id)
        .order_by(Claim.id.desc())
    )
    rows = session.execute(stmt).all()
    hidden = set(
        session.scalars(
            select(MemoryItem.id).where(
                MemoryItem.elder_id == elder_id, MemoryItem.private.is_(True)
            )
        )
    )
    return [
        _view(c, m)
        for c, m in rows
        if not (c.target_type == "memory_item" and c.target_id in hidden)
    ]
