"""Demo seed data. Usage: uv run python -m elder_companion.seed [--reset] [--with-history]"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.db import init_db, make_engine, make_sessionmaker, reset_db
from elder_companion.models import (
    DEFAULT_ELDER_ID,
    Alert,
    ChatSession,
    Elder,
    FamilyMember,
    MemoryItem,
    Message,
    Reminder,
    ReminderLog,
    SymptomLog,
    SymptomMention,
)
from elder_companion.settings import get_settings

DEMO_ELDER = {
    "id": DEFAULT_ELDER_ID,
    "name": "Margaret Lee",
    "nickname": "Maggie",
    "language": "en",
    "profile_text": (
        "78 years old, widowed, lives alone in a single-story house. "
        "Bilingual (English and Mandarin); answer in whichever language she speaks. "
        "Daughter Amy lives in another city and checks in by phone on weekends; "
        "son Ben lives an hour away. "
        "High blood pressure — takes her pill every morning after breakfast. "
        "Mild arthritis in the right knee, worse on cold mornings. "
        "Enjoys gardening (tomatoes and roses), crossword puzzles, and video calls with "
        "her grandson Leo (10). Used to be a primary school teacher."
    ),
}


# Siblings share one dashboard (?member=<id>); the first one is the default acting member
# and the name the companion uses in its privacy disclosure.
DEMO_FAMILY = (("Amy", "daughter"), ("Ben", "son"))

# Amy's standing reminder. Her wording is the only wording the companion uses (spec 2.6).
DEMO_REMINDER = {"text": "blood pressure pill with breakfast", "schedule_time": "08:00"}


def seed_demo(session: Session) -> Elder:
    """Idempotent: create the demo elder, family members and Amy's reminder if missing."""
    elder = session.get(Elder, DEFAULT_ELDER_ID)
    if elder is None:
        elder = Elder(**DEMO_ELDER)
        session.add(elder)
        session.flush()
    if session.scalar(select(FamilyMember.id).where(FamilyMember.elder_id == elder.id)) is None:
        session.add_all(
            FamilyMember(elder_id=elder.id, name=name, relation=relation)
            for name, relation in DEMO_FAMILY
        )
        session.flush()
    if session.scalar(select(Reminder.id).where(Reminder.elder_id == elder.id)) is None:
        amy = session.scalars(
            select(FamilyMember).where(FamilyMember.elder_id == elder.id).order_by(FamilyMember.id)
        ).first()
        session.add(
            Reminder(
                elder_id=elder.id,
                from_member_id=amy.id if amy else None,
                **DEMO_REMINDER,
            )
        )
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
class Memory:
    kind: str
    subject: str
    text: str
    raw_quote: str  # must appear in the turn's user line
    due_in_days: int | None = None  # follow_up: counted from the day it was said


@dataclass(frozen=True)
class Turn:
    at: time  # the elder's local time
    user: str
    reply: str
    symptoms: tuple[Symptom, ...] = ()
    alert: Alerted | None = None
    memory: tuple[Memory, ...] = ()
    private: bool = False  # "keep this between us": hidden from the family views


@dataclass(frozen=True)
class Day:
    days_ago: int
    turns: tuple[Turn, ...] = field(default_factory=tuple)


# Story: knee arthritis flares on cold mornings (one bad day raises a medium alert) and is
# aching again yesterday; sleep is poor mid-week and then improves; one lonely evening;
# ordinary good days in between. Companion memory (Stage H): neighbor Rosa comes up three
# times and grandson Leo twice (care list); two stories for the memoir; she plans to repot
# her orchid "in a couple of days", so today's greeting asks how it went; and one evening
# she asks to keep her money worries between the two of them (a private segment).
# fmt: off
HISTORY: tuple[Day, ...] = (
    Day(6, (
        Turn(time(8, 40), "Morning! It's chilly today and my knee is aching a little.",
             "Chilly mornings can be hard on a knee. Are you keeping it warm today?",
             (Symptom("joint_pain", "knee ache", "my knee is aching a little", "mild",
                      body_part="knee", onset="cold morning"),)),
        Turn(time(16, 10), "I planted the last of the roses this afternoon.",
             "Oh, how lovely! What color did you choose?"),
        Turn(time(17, 30), "Rosa from next door came over for tea.",
             "How nice to have company. What did you two talk about?",
             memory=(Memory("person", "Rosa", "her neighbor Rosa, who visits for tea",
                            "Rosa from next door came over for tea"),)),
    )),
    Day(5, (
        Turn(time(9, 5), "I hardly slept last night, I kept waking up.",
             "I'm sorry, that sounds tiring. Did something wake you, or were you just restless?",
             (Symptom("insomnia", "waking up at night", "I hardly slept last night",
                      "moderate", onset="last night"),)),
        Turn(time(9, 8), "Just restless. I'll have a nap after lunch.",
             "A nap sounds like a good plan. Is there anything fun on for this afternoon?"),
        Turn(time(18, 20), "Rosa brought me some of her tamales, they were delicious.",
             "What a kind neighbor! Did she make them herself?",
             memory=(Memory("person", "Rosa", "her neighbor Rosa, who brings her food",
                            "Rosa brought me some of her tamales"),)),
    )),
    Day(4, (
        Turn(time(8, 15), "My knee is really bad today, I can hardly walk to the kitchen.",
             "That sounds really painful. Could you let Amy or your doctor know today?",
             (Symptom("joint_pain", "knee pain, hard to walk",
                      "My knee is really bad today, I can hardly walk", "severe",
                      "ongoing", body_part="knee"),),
             Alerted("medium", "Joint pain",
                     'Maggie said: "My knee is really bad today, I can hardly walk to the '
                     'kitchen." (severity: severe, status: ongoing)')),
        Turn(time(19, 30), "Leo called me on video, he showed me his science project!",
             "How wonderful! What was his project about?",
             memory=(Memory("person", "grandson Leo", "her grandson Leo, who video-calls her",
                            "Leo called me on video"),)),
    )),
    Day(3, (
        Turn(time(8, 50), "昨晚睡得好一点了，不过还是醒了两次。",
             "睡得好一点就好。醒来的时候能很快再睡着吗？",
             (Symptom("insomnia", "睡眠好一点了", "昨晚睡得好一点了", "mild", "improved",
                      onset="昨晚"),)),
        Turn(time(20, 15), "It's so quiet tonight. Amy didn't call, I feel a bit lonely.",
             "I'm right here with you, Maggie. Would you like to tell me about your day?",
             (Symptom("loneliness", "lonely evening", "I feel a bit lonely", "mild",
                      onset="evening"),)),
        Turn(time(20, 22), "When I started teaching in 1972, my first class had forty-two "
             "children, and I knew every one of their names by the second week.",
             "Forty-two! That's remarkable. Do you still remember any of them?",
             memory=(Memory("story", "first teaching class",
                            "her first class as a teacher in 1972 had forty-two children",
                            "When I started teaching in 1972, my first class had forty-two "
                            "children"),)),
        Turn(time(20, 40), "The roof repair cost more than I expected and I'm worried about "
             "money. Please keep this between us, Amy worries too much.",
             "Of course, this stays between us. Just so you know, if it's ever about your "
             "safety, like a fall or chest pain, I'll always let Amy know.",
             memory=(Memory("topic", "roof repair costs", "worried about the roof repair bill",
                            "The roof repair cost more than I expected"),),
             private=True),
    )),
    Day(2, (
        Turn(time(9, 20), "The knee is a bit better today, the sun is out.",
             "I'm glad to hear that! Will you get out to the garden?",
             (Symptom("joint_pain", "knee pain", "The knee is a bit better today", "mild",
                      "improved", body_part="knee"),)),
        Turn(time(15, 0), "I finished the whole crossword this afternoon!",
             "Well done! Was there a clue that made you think hard?"),
        Turn(time(16, 30), "Rosa and I sat in the garden. I'm going to repot my orchid in a "
             "couple of days, it has outgrown its pot.",
             "That sounds like a lovely afternoon. Which pot will you move it to?",
             memory=(Memory("person", "Rosa", "her neighbor Rosa, who sits in the garden with her",
                            "Rosa and I sat in the garden"),
                     Memory("follow_up", "orchid", "planning to repot her orchid",
                            "I'm going to repot my orchid in a couple of days", 2))),
    )),
    Day(1, (
        Turn(time(8, 30), "Slept well for once! But my knee is aching again this morning.",
             "A good night's sleep is lovely. Is the knee worse than the other day?",
             (Symptom("joint_pain", "knee pain", "my knee is aching again",
                      "moderate", "ongoing", body_part="knee", onset="morning"),)),
        Turn(time(8, 34), "Not as bad as that day. I picked tomatoes anyway.",
             "Good for you. Take it easy on the knee this afternoon, will you?"),
        Turn(time(18, 45), "Amy called, she's coming to visit next weekend.",
             "That's wonderful news! What would you like to do together?",
             memory=(Memory("follow_up", "Amy's visit", "Amy is coming to visit next weekend",
                            "she's coming to visit next weekend", 8),)),
        Turn(time(19, 10), "Leo mailed me a drawing of my roses. My father used to draw for me "
             "too, he sketched our little house in Suzhou on every birthday card.",
             "What a treasure. Do you still have any of those cards?",
             memory=(Memory("person", "grandson Leo", "her grandson Leo, who draws for her",
                            "Leo mailed me a drawing of my roses"),
                     Memory("story", "father's birthday sketches",
                            "her father sketched their house in Suzhou on every birthday card",
                            "he sketched our little house in Suzhou on every birthday card"))),
    )),
)
# fmt: on


def _utc(local_now: datetime, days_ago: int, at: time) -> datetime:
    local = datetime.combine(local_now.date() - timedelta(days=days_ago), at, local_now.tzinfo)
    return local.astimezone(UTC).replace(tzinfo=None)


def seed_history(session: Session, now: datetime, elder_id: int = DEFAULT_ELDER_ID) -> bool:
    """Add the past 6 days of chat, symptoms, alerts and companion memory (`now` is aware, in
    the elder's zone). Skips (returns False) if the elder already has messages, to avoid
    duplicating the story."""
    if session.scalar(select(Message.id).where(Message.elder_id == elder_id).limit(1)):
        return False
    memory: dict[tuple[str, str], MemoryItem] = {}
    for day in sorted(HISTORY, key=lambda d: -d.days_ago):
        chat = ChatSession(elder_id=elder_id, started_at=_utc(now, day.days_ago, day.turns[0].at))
        session.add(chat)
        session.flush()
        for turn in day.turns:
            ts = _utc(now, day.days_ago, turn.at)
            reply_at = ts + timedelta(seconds=4)
            user = Message(
                elder_id=elder_id,
                session_id=chat.id,
                role="user",
                text=turn.user,
                private=turn.private,
                created_at=ts,
            )
            reply = Message(
                elder_id=elder_id,
                session_id=chat.id,
                role="assistant",
                text=turn.reply,
                private=turn.private,
                created_at=reply_at,
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
                session.add(
                    SymptomMention(
                        symptom_log_id=row.id,
                        message_id=user.id,
                        raw_quote=s.raw_quote,
                        severity=s.severity,
                        created_at=ts,
                    )
                )
                if turn.alert:
                    session.add(
                        Alert(
                            elder_id=elder_id,
                            type="symptom",
                            level=turn.alert.level,
                            title=turn.alert.title,
                            content=turn.alert.content,
                            ref_id=str(row.id),
                            message_id=user.id,
                            created_at=reply_at,
                            is_read=True,
                        )
                    )
            for m in turn.memory:
                _remember(session, memory, m, user, now.date() - timedelta(days=day.days_ago))
    _seed_adherence(session, elder_id, now.date())
    elder = session.get_one(Elder, elder_id)
    # She has chatted for days: the privacy rule was explained in the first session.
    elder.privacy_disclosed_at = elder.privacy_disclosed_at or _utc(now, 6, HISTORY[0].turns[0].at)
    session.commit()
    return True


# Six days of the pill reminder: mostly taken, one day she never got back to it, one "not yet".
ADHERENCE = {
    6: "confirmed",
    5: "confirmed",
    4: "no_response",
    3: "confirmed",
    2: "declined",
    1: "confirmed",
}


def _seed_adherence(session: Session, elder_id: int, today: date) -> None:
    reminder = session.scalars(
        select(Reminder).where(Reminder.elder_id == elder_id).order_by(Reminder.id)
    ).first()
    if reminder is None:
        return
    for days_ago, status in ADHERENCE.items():
        session.add(
            ReminderLog(
                reminder_id=reminder.id,
                date=today - timedelta(days=days_ago),
                status=status,
            )
        )


def _remember(
    session: Session,
    items: dict[tuple[str, str], MemoryItem],
    m: Memory,
    msg: Message,
    said_on: date,
) -> None:
    assert m.raw_quote in msg.text, m.raw_quote
    key = (m.kind, m.subject)
    item = items.get(key)
    if item is None:
        due = said_on + timedelta(days=m.due_in_days or 1) if m.kind == "follow_up" else None
        item = MemoryItem(
            elder_id=msg.elder_id,
            kind=m.kind,
            subject=m.subject,
            text=m.text,
            raw_quote=m.raw_quote,
            message_id=msg.id,
            private=msg.private,
            mention_count=1,
            due_date=due,
            status="open",
            first_seen=msg.created_at,
            last_seen=msg.created_at,
        )
        items[key] = item
        session.add(item)
    else:
        item.text, item.raw_quote, item.message_id = m.text, m.raw_quote, msg.id
        item.mention_count += 1
        item.last_seen = msg.created_at
        item.private = item.private and msg.private
    session.flush()


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
