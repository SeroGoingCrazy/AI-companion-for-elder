"""FastAPI app factory.

Run: uv run elder-web
  or uv run uvicorn --factory elder_companion.web.app:create_app
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from elder_companion import __version__
from elder_companion.alerts.bus import AlertBus
from elder_companion.consent import ConsentExtractor
from elder_companion.db import init_db, make_engine, make_sessionmaker
from elder_companion.llm import BaseLLMClient, get_llm
from elder_companion.memory.extractor import MemoryExtractor
from elder_companion.redaction import Redactor
from elder_companion.seed import seed_demo
from elder_companion.settings import Settings, get_settings
from elder_companion.summary import DailySummary
from elder_companion.symptoms.extractor import SymptomExtractor
from elder_companion.web.routes import alerts, chat, family, pages, reports


def create_app(settings: Settings | None = None, llm: BaseLLMClient | None = None) -> FastAPI:
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
    app.state.llm = llm or get_llm(settings.llm)
    app.state.symptom_extractor = SymptomExtractor(app.state.llm)
    app.state.memory_extractor = MemoryExtractor(app.state.llm)
    app.state.redactor = Redactor(app.state.llm)
    app.state.consent_extractor = ConsentExtractor(app.state.llm)
    app.state.alert_bus = AlertBus()
    app.state.daily_summary = DailySummary(
        app.state.llm,
        companion_name=settings.chat.companion_name,
        bypass_levels=settings.privacy.bypass_levels,
    )

    app.include_router(alerts.router)
    app.include_router(chat.router)
    app.include_router(chat.tts_router)
    app.include_router(family.router)
    app.include_router(pages.router)
    app.include_router(reports.router)
    app.mount("/static", StaticFiles(directory=pages.WEB_DIR / "static"), name="static")
    # Fall snapshots: alert.snapshot_path "snapshots/x.jpg" is served at /media/snapshots/x.jpg.
    snapshots_dir = settings.paths.snapshots_dir
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media/snapshots", StaticFiles(directory=snapshots_dir), name="snapshots")

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "elder_companion.web.app:create_app", factory=True, host=s.server.host, port=s.server.port
    )
