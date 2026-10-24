"""FastAPI app. Run with `make dev` (uvicorn landline.app:app --reload)."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import logfire
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from landline import drain
from landline.agents.auditor import llm as auditor_llm
from landline.api import admin, browser_audio, me, twilio
from landline.api.demo import DemoSessions
from landline.api.judge import JudgeLines, StartLimit
from landline.api.routes import router
from landline.config import Settings
from landline.db.catalogue import Catalogue
from landline.db.pool import Database
from landline.orchestrator import SessionRuntime
from landline.voice import higgs, outbound
from landline.voice.avatar_clip import Clips
from landline.voice.calls import RenterCalls
from landline.voice.dispatch import Dispatcher
from landline.voice.line import Lines
from landline.voice.routing import ByChannel
from landline.voice.sim import SimRunner
from landline.voice.sms import Texter
from landline.voice.translate import Translator

log = logging.getLogger(__name__)


def _log_landline_info() -> None:
    """uvicorn sets up only its own loggers, so landline.* INFO lines (calls, transcripts, tool calls)
    would be dropped. Print them to stderr next to uvicorn's, which journalctl also collects on the VM."""
    logger = logging.getLogger("landline")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s  %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


def _keep_session(match: logfire.ScrubMatch) -> object:
    """Logfire hides any value that contains "session". Here that's a session id, the end_session tool or a
    line about the renter's session, none of them secret. Its other patterns (passwords, keys, tokens) still
    hide what they match."""
    return match.value if match.pattern_match.group(0).lower() == "session" else None


def _trace_to_logfire() -> None:
    """With LOGFIRE_TOKEN set (.env), each tool call is a span (voice/bridge.py run_tool) and landline.* log
    lines go along with it. Logfire exports from a background thread, so a call never waits on it. Without
    the token nothing is configured or sent, as in tests and CI."""
    if not os.getenv("LOGFIRE_TOKEN"):
        return
    logfire.configure(token=os.environ["LOGFIRE_TOKEN"], service_name="landline-api", console=False,
                      inspect_arguments=False, scrubbing=logfire.ScrubbingOptions(callback=_keep_session))
    logger = logging.getLogger("landline")
    if not any(isinstance(h, logfire.LogfireLoggingHandler) for h in logger.handlers):
        logger.addHandler(logfire.LogfireLoggingHandler())


def create_app(settings: Settings | None = None, catalogue: Catalogue | None = None) -> FastAPI:
    settings = settings or Settings.load()
    _log_landline_info()
    _trace_to_logfire()

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
        # Map taps and background re-ranks, to the live Concierge between turns (A-02, voice/line.py).
        app.state.lines = Lines(app.state.runtime)
        app.state.judges = JudgeLines(settings.higgs_max_sessions)
        app.state.judge_limit = StartLimit(settings.judge_starts_per_ip, settings.judge_start_window_s)
        # For /health. The renter's call bridge sets "ok" or "error: …" each time it opens a Higgs session;
        # until then, a key means ready, not broken.
        app.state.higgs = "ready (no call since restart)" if settings.boson_api_key else "not configured"
        # The renter's phone calls (A-02, A-04). Tests swap connect_higgs for a fake.
        app.state.calls = RenterCalls(settings.higgs_max_sessions)
        app.state.connect_higgs = higgs.connect
        # Texts the phone renter their board link. Tests swap it for a fake.
        app.state.texter = Texter(settings.twilio_account_sid, settings.twilio_auth_token)
        # The hand-off (A-06): a brief that ends with a plan has its calls queued and run. A judge's calls
        # always go to the Caller talking to landlord personas in text (A-03), with no phone. A phone renter's
        # go over Twilio with TRANSPORT=voice (A-02), to the personas with TRANSPORT=sim, and otherwise stay
        # queued.
        chat = auditor_llm.from_env()   # the Auditor's text LLM (A-07); None runs it on rules alone
        extractor = chat.json if chat else None
        # Clips of Landline's voice, rendered after each call. Before the runners, so they can schedule one.
        app.state.clips = Clips.open(settings, db)
        app.state.outbound = outbound.install(app.state, settings, extractor=extractor)
        # Hard rule 6: a judge's (or any browser brief's) calls always go to the sim landlords; only a phone
        # renter's go over Twilio, and only with TRANSPORT=voice (voice/routing.py).
        sim = SimRunner(app.state.runtime, lambda key, cfg: app.state.connect_higgs(key, cfg),
                        settings.boson_api_key or "", extractor=extractor, clips=app.state.clips)
        runner = ByChannel(app.state.runtime, phone=app.state.outbound.runner, sim=sim,
                           sim_for_phone=settings.transport == "sim")
        app.state.dispatcher = Dispatcher(app.state.runtime, settings, runner=runner, texter=app.state.texter,
                                          translate=Translator(chat))
        # A restart lets live calls finish and page streams end before uvicorn stops (#167).
        app.state.drain = drain.install(app.state)
        # Calls a previous process left open: past their deadline they end timeout, and ones that
        # never dialled are placed again (#217). A failure here must not stop the API coming up.
        try:
            await app.state.dispatcher.recover()
        except Exception:
            log.exception("restart sweep failed")
        yield
        await app.state.dispatcher.close()
        await outbound.uninstall(app.state.outbound)
        await app.state.texter.close()
        app.state.sessions.close()
        await app.state.runtime.close()
        await app.state.clips.aclose()
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
    app.include_router(me.router)
    app.include_router(admin.router)
    app.include_router(twilio.router)
    app.include_router(browser_audio.router)
    return app


app = create_app()
