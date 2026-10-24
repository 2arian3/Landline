from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
LOCAL_WEB = "http://localhost:3000"


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    web_url: str
    transport: str
    # Browser origins allowed to call the API directly, besides web_url and LOCAL_WEB.
    cors_origins: tuple[str, ...] = ()
    cors_origin_regex: str | None = None
    # Judge sessions talking at once (U-06), capped by the account's Higgs concurrency (A-01).
    higgs_max_sessions: int = 5
    # Where a judge's browser sends its audio, relative to the API. A placeholder until the browser
    # transport (A-03) settles it; nothing else spells it out.
    judge_ws_path: str = "/api/judge/{session_id}/audio"

    @classmethod
    def load(cls) -> Settings:
        load_dotenv(ROOT / ".env")
        return cls(
            database_url=os.getenv("DATABASE_URL") or None,
            web_url=os.getenv("WEB_URL") or LOCAL_WEB,
            transport=os.getenv("TRANSPORT") or "stub",
            cors_origins=tuple(o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()),
            cors_origin_regex=os.getenv("CORS_ORIGIN_REGEX") or None,
            higgs_max_sessions=int(os.getenv("HIGGS_MAX_SESSIONS") or 5),
        )

    def allowed_origins(self) -> list[str]:
        # An Origin header never ends in "/", so a pasted URL with one would never match.
        return sorted({o.rstrip("/") for o in (self.web_url, LOCAL_WEB, *self.cors_origins)})
