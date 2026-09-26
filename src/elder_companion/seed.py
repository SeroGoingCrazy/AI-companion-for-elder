"""Demo seed data. Usage: uv run python -m elder_companion.seed [--reset]"""

from __future__ import annotations

import argparse

from sqlalchemy.orm import Session

from elder_companion.db import init_db, make_engine, make_sessionmaker, reset_db
from elder_companion.models import DEFAULT_ELDER_ID, Elder
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed demo data")
    parser.add_argument("--reset", action="store_true", help="drop and recreate all tables first")
    args = parser.parse_args(argv)

    engine = make_engine(get_settings().database.url)
    if args.reset:
        reset_db(engine)
    else:
        init_db(engine)
    with make_sessionmaker(engine)() as session:
        elder = seed_demo(session)
    print(f"seeded elder #{elder.id} {elder.name} ({engine.url})")


if __name__ == "__main__":
    main()
