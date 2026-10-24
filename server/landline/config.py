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
    # Judge mode stays shut ("opens soon") until the browser audio (A-03) is live: JUDGE_AUDIO_READY=1.
    judge_audio_ready: bool = False
    # Judge sessions one address may start per window. A venue's Wi-Fi puts every judge on one address.
    judge_starts_per_ip: int = 3
    judge_start_window_s: int = 600
    # The renter's phone call (A-02, A-04). Without the key the number answers "not taking calls";
    # without the token every Twilio webhook is refused.
    boson_api_key: str | None = None
    twilio_auth_token: str | None = None
    # The API's public origin, e.g. https://landline.134.87.8.230.sslip.io. Twilio signs this URL, and the
    # media stream's wss:// URL is built from it. Unset: taken from the request (local runs only).
    public_url: str | None = None
    higgs_voice_renter: str = "default"
    renter_call_max_s: float = 900

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
            judge_audio_ready=_on("JUDGE_AUDIO_READY"),
            judge_starts_per_ip=int(os.getenv("JUDGE_STARTS_PER_IP") or 3),
            judge_start_window_s=int(os.getenv("JUDGE_START_WINDOW_S") or 600),
            boson_api_key=os.getenv("BOSON_API_KEY") or None,
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN") or None,
            public_url=(os.getenv("PUBLIC_URL") or "").rstrip("/") or None,
            higgs_voice_renter=os.getenv("HIGGS_VOICE_RENTER") or "default",
        )

    def allowed_origins(self) -> list[str]:
        # An Origin header never ends in "/", so a pasted URL with one would never match.
        return sorted({o.rstrip("/") for o in (self.web_url, LOCAL_WEB, *self.cors_origins)})


def _on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")
