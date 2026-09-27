"""H1: Stage H tables, new columns, the forward-only sqlite migration, symptom occurrences."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text

from elder_companion.db import init_db, make_engine, make_sessionmaker
from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import Alert, Message, SymptomLog, SymptomMention
from elder_companion.seed import seed_demo
from elder_companion.settings import SymptomSettings
from elder_companion.symptoms.extractor import SymptomExtractor
from elder_companion.symptoms.service import SymptomService

pytestmark = pytest.mark.unit

OLD_SCHEMA = (
    "CREATE TABLE elder (id INTEGER PRIMARY KEY, name VARCHAR(100), nickname VARCHAR(50),"
    " language VARCHAR(10), profile_text TEXT, created_at DATETIME)",
    "CREATE TABLE message (id INTEGER PRIMARY KEY, elder_id INTEGER, role VARCHAR(10),"
    " text TEXT, audio_path VARCHAR(255), created_at DATETIME)",
    "INSERT INTO elder VALUES (1, 'M', 'M', 'en', '', '2026-09-01')",
    "INSERT INTO message VALUES (1, 1, 'user', 'hi', NULL, '2026-09-01')",
)
STAGE_H_TABLES = {"family_member", "chat_session", "symptom_mention", "memory_item", "claim"}


def test_init_db_creates_stage_h_tables_and_columns() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    insp = inspect(engine)
    assert STAGE_H_TABLES <= set(insp.get_table_names())
    assert {"private", "session_id"} <= {c["name"] for c in insp.get_columns("message")}
    assert "privacy_disclosed_at" in {c["name"] for c in insp.get_columns("elder")}
    assert "message_id" in {c["name"] for c in insp.get_columns("alert")}


def test_old_database_gets_new_columns_with_defaults(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    with create_engine(url).begin() as conn:  # a v0.1 schema with one message
        for ddl in OLD_SCHEMA:
            conn.execute(text(ddl))
    engine = make_engine(url)
    init_db(engine)
    with make_sessionmaker(engine)() as s:
        msg = s.get_one(Message, 1)
        assert msg.private is False and msg.session_id is None
        assert STAGE_H_TABLES <= set(inspect(engine).get_table_names())


def test_every_symptom_occurrence_gets_a_mention() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with make_sessionmaker(engine)() as s:
        seed_demo(s)
        service = SymptomService(
            s, SymptomExtractor(MockLLMClient.from_config()), SymptomSettings()
        )
        ids = []
        for line in ("I'm a bit dizzy", "Still dizzy today", "My chest feels tight"):
            m = Message(elder_id=1, role="user", text=line)
            s.add(m)
            s.commit()
            service.process_message(m.id)
            ids.append(m.id)
        dizzy = s.scalars(select(SymptomLog).where(SymptomLog.canonical == "dizziness")).one()
        mentions = s.scalars(
            select(SymptomMention).where(SymptomMention.symptom_log_id == dizzy.id)
        ).all()
        assert dizzy.count == 2 and [m.message_id for m in mentions] == ids[:2]
        assert all(m.raw_quote == "dizzy" for m in mentions)
        alert = s.scalars(select(Alert)).first()
        assert alert.message_id == ids[2]  # the chest line raised it
