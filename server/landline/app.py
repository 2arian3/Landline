"""FastAPI app. Run with `make dev` (uvicorn landline.app:app --reload)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from landline.api import admin, browser_audio, twilio
from landline.api.demo import DemoSessions
from landline.api.judge import JudgeLines, StartLimit
from landline.api.routes import router
from landline.config import Settings
from landline.db.catalogue import Catalogue
from landline.db.pool import Database
from landline.orchestrator import SessionRuntime
from landline.voice import higgs
from landline.voice.calls import RenterCalls
from landline.voice.dispatch import Dispatcher
from landline.voice.sms import Texter


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
        # One pool for the runtime and for call turns (/api/calls/{id}/turns). Without DATABASE_URL only the
        # demo store's calls have turns.
        app.state.db = db = await Database.open(settings.database_url) if settings.database_url else None
        app.state.sessions = DemoSessions(app.state.catalogue)
        # The session runtime (O-02): sessions that voice or judge mode start. It persists to Postgres when
        # DATABASE_URL is set, else keeps them in memory. Demo sessions stay in `sessions` until A-06.
        # Its own matrix (with neighbourhoods), and the contact the place lookup identifies itself with.
        app.state.runtime = SessionRuntime(app.state.catalogue, db, contact=settings.contact_email)
        app.state.judges = JudgeLines(settings.higgs_max_sessions)
        app.state.judge_limit = StartLimit(settings.judge_starts_per_ip, settings.judge_start_window_s)
        # For /health. The renter's call bridge sets "ok" or "error: …" each time it opens a Higgs session.
        app.state.higgs = "not configured"
        # The renter's phone calls (A-02, A-04). Tests swap connect_higgs for a fake.
        app.state.calls = RenterCalls(settings.higgs_max_sessions)
        app.state.connect_higgs = higgs.connect
        # Texts the phone renter their board link. Tests swap it for a fake.
        app.state.texter = Texter(settings.twilio_account_sid, settings.twilio_auth_token)
        # The hand-off (A-06): a brief that ends with a plan has its calls queued and run. The Caller (A-05)
        # sets `runner`; until then the plan is frozen and its calls stay queued.
        app.state.dispatcher = Dispatcher(app.state.runtime, settings, texter=app.state.texter)
        yield
        await app.state.dispatcher.close()
        await app.state.texter.close()
        app.state.sessions.close()
        await app.state.runtime.close()
        if db is not None:
            await db.close()

    app = FastAPI(title="Landline", version="0.1.0", lifespan=lifespan)
    # The web app normally reaches the API through its own /api proxy (same origin, no CORS).
    # Browsers that call the API directly (NEXT_PUBLIC_API_URL) need their origin listed here.
    # No cookies or auth headers, so credentials stay off.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins(),
        allow_origin_regex=settings.cors_origin_regex,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],   # Authorization: the admin panel's token
        max_age=600,
    )
    app.include_router(router)
    app.include_router(admin.router)
    app.include_router(twilio.router)
    app.include_router(browser_audio.router)
    return app


app = create_app()
