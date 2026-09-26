"""Demo seed data. Usage: uv run python -m elder_companion.seed [--reset] [--with-history]"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.db import init_db, make_engine, make_sessionmaker, reset_db
from elder_companion.models import DEFAULT_ELDER_ID, Alert, Elder, Message, SymptomLog
from elder_companion.settings import get_settings

DEMO_ELDER = {
    "id": DEFAULT_ELDER_ID,
    "name": "Margaret Lee",
    "nickname": "Maggie",
    "language": "en",
    "profile_text": (
        "78 years old, widowed, lives alone in a single-story house. "
        "Bilingual (English and Mandarin); answer in whichever language she speaks. "
        "Daughter Emily lives in another city and checks in by phone on weekends. "
        "High blood pressure — takes her pill every morning after breakfast. "
        "Mild arthritis in the right knee, worse on cold mornings. "
        "Enjoys gardening (tomatoes and roses), crossword puzzles, and video calls with "
        "her grandson Leo (10). Used to be a primary school teacher."
    ),
}


def seed_demo(session: Session) -> Elder:
    """Idempotent: create the demo elder if missing."""
    elder = session.get(Elder, DEFAULT_ELDER_ID)
    if elder is None:
        elder = Elder(**DEMO_ELDER)
        session.add(elder)
        session.commit()
    return elder


# ---------- history for the dashboard timeline (spec E3) ----------


@dataclass(frozen=True)
class Symptom:
    canonical: str
    label: str
    raw_quote: str  # must appear in the day's user lines, like real extractions
    severity: str = "unknown"
    status: str = "new"
    body_part: str | None = None
    duration: str | None = None
    onset: str | None = None


@dataclass(frozen=True)
class Alerted:
    level: str
    title: str
    content: str


@dataclass(frozen=True)
class Turn:
    at: time  # the elder's local time
    user: str
    reply: str
    symptoms: tuple[Symptom, ...] = ()
    alert: Alerted | None = None


@dataclass(frozen=True)
class Day:
    days_ago: int
    turns: tuple[Turn, ...] = field(default_factory=tuple)


# Story: knee arthritis flares on cold mornings (one bad day raises a medium alert) and is
# aching again yesterday, so today's greeting follows up on it; sleep is poor mid-week and
# then improves; one lonely evening; ordinary good days in between.
# fmt: off
HISTORY: tuple[Day, ...] = (
    Day(6, (
        Turn(time(8, 40), "Morning! It's chilly today and my knee is aching a little.",
             "Chilly mornings can be hard on a knee. Are you keeping it warm today?",
             (Symptom("joint_pain", "knee ache", "my knee is aching a little", "mild",
                      body_part="knee", onset="cold morning"),)),
        Turn(time(16, 10), "I planted the last of the roses this afternoon.",
             "Oh, how lovely! What color did you choose?"),
    )),
    Day(5, (
        Turn(time(9, 5), "I hardly slept last night, I kept waking up.",
             "I'm sorry, that sounds tiring. Did something wake you, or were you just restless?",
             (Symptom("insomnia", "waking up at night", "I hardly slept last night",
                      "moderate", onset="last night"),)),
        Turn(time(9, 8), "Just restless. I'll have a nap after lunch.",
             "A nap sounds like a good plan. Is there anything fun on for this afternoon?"),
    )),
    Day(4, (
        Turn(time(8, 15), "My knee is really bad today, I can hardly walk to the kitchen.",
             "That sounds really painful. Could you let Emily or your doctor know today?",
             (Symptom("joint_pain", "knee pain, hard to walk",
                      "My knee is really bad today, I can hardly walk", "severe",
                      "ongoing", body_part="knee"),),
             Alerted("medium", "Joint pain",
                     'Maggie said: "My knee is really bad today, I can hardly walk to the '
                     'kitchen." (severity: severe, status: ongoing)')),
        Turn(time(19, 30), "Leo called me on video, he showed me his science project!",
             "How wonderful! What was his project about?"),
    )),
    Day(3, (
        Turn(time(8, 50), "昨晚睡得好一点了，不过还是醒了两次。",
             "睡得好一点就好。醒来的时候能很快再睡着吗？",
             (Symptom("insomnia", "睡眠好一点了", "昨晚睡得好一点了", "mild", "improved",
                      onset="昨晚"),)),
        Turn(time(20, 15), "It's so quiet tonight. Emily didn't call, I feel a bit lonely.",
             "I'm right here with you, Maggie. Would you like to tell me about your day?",
             (Symptom("loneliness", "lonely evening", "I feel a bit lonely", "mild",
                      onset="evening"),)),
    )),
    Day(2, (
        Turn(time(9, 20), "The knee is a bit better today, the sun is out.",
             "I'm glad to hear that! Will you get out to the garden?",
             (Symptom("joint_pain", "knee pain", "The knee is a bit better today", "mild",
                      "improved", body_part="knee"),)),
        Turn(time(15, 0), "I finished the whole crossword this afternoon!",
             "Well done! Was there a clue that made you think hard?"),
    )),
    Day(1, (
        Turn(time(8, 30), "Slept well for once! But my knee is aching again this morning.",
             "A good night's sleep is lovely. Is the knee worse than the other day?",
             (Symptom("joint_pain", "knee pain", "my knee is aching again",
                      "moderate", "ongoing", body_part="knee", onset="morning"),)),
        Turn(time(8, 34), "Not as bad as that day. I picked tomatoes anyway.",
             "Good for you. Take it easy on the knee this afternoon, will you?"),
        Turn(time(18, 45), "Emily called, she's coming to visit next weekend.",
             "That's wonderful news! What would you like to do together?"),
    )),
)
# fmt: on


def _utc(local_now: datetime, days_ago: int, at: time) -> datetime:
    local = datetime.combine(local_now.date() - timedelta(days=days_ago), at, local_now.tzinfo)
    return local.astimezone(UTC).replace(tzinfo=None)


def seed_history(session: Session, now: datetime, elder_id: int = DEFAULT_ELDER_ID) -> bool:
    """Add the past 6 days of chat, symptoms and alerts (`now` is aware, in the elder's zone).
    Skips (returns False) if the elder already has messages, to avoid duplicating the story."""
    if session.scalar(select(Message.id).where(Message.elder_id == elder_id).limit(1)):
        return False
    for day in sorted(HISTORY, key=lambda d: -d.days_ago):
        for turn in day.turns:
            ts = _utc(now, day.days_ago, turn.at)
            user = Message(elder_id=elder_id, role="user", text=turn.user, created_at=ts)
            reply_at = ts + timedelta(seconds=4)
            reply = Message(
                elder_id=elder_id, role="assistant", text=turn.reply, created_at=reply_at
            )
            session.add_all([user, reply])
            session.flush()
            for s in turn.symptoms:
                assert s.raw_quote in turn.user, s.raw_quote
                row = SymptomLog(
                    elder_id=elder_id,
                    message_id=user.id,
                    first_seen=ts,
                    last_seen=ts,
                    **vars(s),
                )
                session.add(row)
                session.flush()
                if turn.alert:
                    session.add(
                        Alert(
                            elder_id=elder_id,
                            type="symptom",
                            level=turn.alert.level,
                            title=turn.alert.title,
                            content=turn.alert.content,
                            ref_id=str(row.id),
                            created_at=reply_at,
                            is_read=True,
                        )
                    )
    session.commit()
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed demo data")
    parser.add_argument("--reset", action="store_true", help="drop and recreate all tables first")
    parser.add_argument(
        "--with-history", action="store_true", help="add 6 days of chat/symptoms for the dashboard"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = make_engine(settings.database.url)
    if args.reset:
        reset_db(engine)
    else:
        init_db(engine)
    with make_sessionmaker(engine)() as session:
        elder = seed_demo(session)
        print(f"seeded elder #{elder.id} {elder.name} ({engine.url})")
        if args.with_history:
            added = seed_history(session, datetime.now(settings.chat.tz), elder.id)
            print("added 6 days of history" if added else "history skipped: messages exist")


if __name__ == "__main__":
    main()
