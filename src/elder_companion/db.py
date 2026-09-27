"""SQLAlchemy engine / session factory. Timestamps are stored as naive UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator
from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from elder_companion.settings import PROJECT_ROOT

_SQLITE_PREFIX = "sqlite:///"


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


# For API schemas: DB values are naive UTC; without an offset in the JSON, browsers would
# parse them as local time.
UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]


def resolve_sqlite_url(url: str) -> str:
    """Make relative sqlite paths absolute (against the project root) and create the directory."""
    if not url.startswith(_SQLITE_PREFIX) or url == f"{_SQLITE_PREFIX}:memory:":
        return url
    path = Path(url.removeprefix(_SQLITE_PREFIX))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"{_SQLITE_PREFIX}{path.as_posix()}"


def make_engine(url: str) -> Engine:
    url = resolve_sqlite_url(url)
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(url, connect_args={"check_same_thread": False} if is_sqlite else {})
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record) -> None:
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    from elder_companion import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    add_missing_columns(engine)


def add_missing_columns(engine: Engine) -> None:
    """Tiny forward-only migration: ALTER TABLE ADD COLUMN for columns that a newer model has
    but an existing database lacks (e.g. Stage H's message.private). New columns must be
    nullable or have a server default."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                col_type = col.type.compile(engine.dialect)
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}"
                if col.server_default is not None:
                    ddl += f" DEFAULT {col.server_default.arg.compile(dialect=engine.dialect)}"
                conn.execute(text(ddl))


def reset_db(engine: Engine) -> None:
    from elder_companion import models  # noqa: F401

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
