"""FastAPI app. Run with `make dev` (uvicorn landline.app:app --reload)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from landline.api import twilio
from landline.api.demo import DemoSessions
from landline.api.judge import JudgeLines
from landline.api.routes import router
from landline.config import Settings
from landline.db.catalogue import Catalogue
from landline.voice import higgs
from landline.voice.calls import RenterCalls


def _log_landline_info() -> None:
    """uvicorn sets up only its own loggers, so landline.* INFO lines (calls, transcripts, tool calls)
    would be dropped. Print them to stderr next to uvicorn's, which journalctl also collects on the VM."""
    logger = logging.getLogger("landline")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s  %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


def create_app(settings: Settings | None = None, catalogue: Catalogue | None = None) -> FastAPI:
    settings = settings or Settings.load()
    _log_landline_info()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.catalogue = catalogue or Catalogue.load(settings.database_url)
        app.state.sessions = DemoSessions(app.state.catalogue)
        app.state.judges = JudgeLines(settings.higgs_max_sessions)
        # For /health. The renter's call bridge sets "ok" or "error: …" each time it opens a Higgs session.
        app.state.higgs = "not configured"
        # The renter's phone calls (A-02, A-04). Tests swap connect_higgs for a fake.
        app.state.calls = RenterCalls(settings.higgs_max_sessions)
        app.state.connect_higgs = higgs.connect
        yield
        app.state.sessions.close()

    app = FastAPI(title="Landline", version="0.1.0", lifespan=lifespan)
    # The web app normally reaches the API through its own /api proxy (same origin, no CORS).
    # Browsers that call the API directly (NEXT_PUBLIC_API_URL) need their origin listed here.
    # No cookies or auth headers, so credentials stay off.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins(),
        allow_origin_regex=settings.cors_origin_regex,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
    app.include_router(router)
    app.include_router(twilio.router)
    return app


app = create_app()
