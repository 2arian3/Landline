"""FastAPI app. Run with `make dev` (uvicorn landline.app:app --reload)."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from landline.api.demo import DemoSessions
from landline.api.routes import router
from landline.config import Settings
from landline.db.catalogue import Catalogue


def create_app(settings: Settings | None = None, catalogue: Catalogue | None = None) -> FastAPI:
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.catalogue = catalogue or Catalogue.load(settings.database_url)
        app.state.sessions = DemoSessions(app.state.catalogue)
        # For /health. The voice layer (A-02) replaces it once it has reached Higgs.
        app.state.higgs = "not configured"
        yield

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
    return app


app = create_app()
