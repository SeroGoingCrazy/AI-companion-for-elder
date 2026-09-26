"""FastAPI app factory.

Run: uv run elder-web
  or uv run uvicorn --factory elder_companion.web.app:create_app
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from elder_companion import __version__
from elder_companion.db import init_db, make_engine, make_sessionmaker
from elder_companion.seed import seed_demo
from elder_companion.settings import Settings, get_settings
from elder_companion.web.routes import alerts


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    engine = make_engine(settings.database.url)
    init_db(engine)
    session_factory = make_sessionmaker(engine)
    with session_factory() as session:
        seed_demo(session)

    app = FastAPI(title="AI Companion for Elder", version=__version__)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = session_factory

    app.include_router(alerts.router)

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "elder_companion.web.app:create_app", factory=True, host=s.server.host, port=s.server.port
    )
