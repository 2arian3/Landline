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
    # The renter's phone call (A-02, A-04). Without the voice provider's key the number answers
    # "not taking calls"; without the token every Twilio webhook is refused.
    boson_api_key: str | None = None
    # higgs (Boson Realtime) or gemini (Gemini Live). Higgs stays the default until a spike accepts Gemini.
    voice_provider: str = "higgs"
    gemini_api_key: str | None = None
    gemini_live_model: str = "gemini-3.8-live"
    gemini_live_voice: str = "Aoede"
    twilio_auth_token: str | None = None
    # Texting the renter the board link needs the account SID too; it goes out from the number they
    # dialled, or TWILIO_PHONE_NUMBER when that isn't known.
    twilio_account_sid: str | None = None
    twilio_phone_number: str | None = None
    # The API's public origin, e.g. https://landline.134.87.8.230.sslip.io. Twilio signs this URL, and the
    # media stream's wss:// URL is built from it. Unset: taken from the request (local runs only).
    public_url: str | None = None
    higgs_voice_renter: str = "default"
    renter_call_max_s: float = 900
    # Store both sides of each call's audio next to its transcript (A-10). On unless RECORD_AUDIO=0.
    # The transcript and where each line sits in the call are stored either way.
    record_audio: bool = True
    # Calls to listing agents (A-06). Only numbers on the allowlist are ever dialled (hard rule 6); calls
    # wait for Toronto business hours unless FORCE_BUSINESS_HOURS=1; at most CALL_CONCURRENCY run at once
    # (default: the Higgs lines minus one kept for renters calling in); each has a deadline.
    demo_agent_phones: tuple[str, ...] = ()
    force_business_hours: bool = False
    call_concurrency: int | None = None
    listing_call_max_s: float = 300
    # Who to reach about Landline's requests to other sites (the scraper's SCRAPER_CONTACT_EMAIL). The place
    # lookup (D-04) puts it in its User-Agent; without it, only neighbourhoods and stations resolve places.
    contact_email: str | None = None
    # Google Maps Platform (D-10c): TTC minutes from the top listings to the place a renter names. Unset, the
    # location score stays on km.
    google_maps_api_key: str | None = None
    # The admin panel (/admin) and its API need this token as a bearer header. Unset, they answer 404.
    admin_token: str | None = None
    # Still face for the clip made after a call (voice/avatar_clip.py). Landline's voice lip-syncs it.
    higgs_avatar_image_url: str = "https://docs.boson.ai/public/avatar/sample.jpg"
    # Clerk phone sign-in on /api/me (U-11a, api/me.py). With the secret key set, GET /api/me needs a Clerk
    # session token and POST /api/me with a bare phone number is refused. CLERK_JWT_KEY (the instance's PEM
    # public key) verifies tokens with no network call; without it the SDK fetches Clerk's JWKS.
    clerk_secret_key: str | None = None
    clerk_jwt_key: str | None = None
    # Viewing invites from Landline's Google account (O-08a, orchestrator/calendar.py). The refresh token
    # comes from scripts/google_oauth.py. Without all three, no invite is sent.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_oauth_refresh_token: str | None = None
    # Shared listing memory on SurrealDB Agent Memory (O-09a, orchestrator/memory.py): a context's host, its
    # id and an sp-… API key, from SurrealDB Studio. Without all three, nothing is written or read.
    spectron_url: str | None = None
    spectron_context_id: str | None = None
    spectron_api_key: str | None = None

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
            voice_provider=(os.getenv("VOICE_PROVIDER") or "higgs").strip().lower() or "higgs",
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            gemini_live_model=(os.getenv("GEMINI_LIVE_MODEL") or "gemini-3.8-live").strip(),
            gemini_live_voice=(os.getenv("GEMINI_LIVE_VOICE") or "Aoede").strip(),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN") or None,
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID") or None,
            twilio_phone_number=os.getenv("TWILIO_PHONE_NUMBER") or None,
            public_url=(os.getenv("PUBLIC_URL") or "").rstrip("/") or None,
            higgs_voice_renter=os.getenv("HIGGS_VOICE_RENTER") or "default",
            record_audio=os.getenv("RECORD_AUDIO", "1").strip().lower() not in ("0", "false", "no", "off"),
            demo_agent_phones=_list("DEMO_AGENT_PHONES"),
            force_business_hours=_on("FORCE_BUSINESS_HOURS"),
            call_concurrency=int(os.getenv("CALL_CONCURRENCY") or 0) or None,
            admin_token=os.getenv("ADMIN_TOKEN") or None,
            contact_email=os.getenv("SCRAPER_CONTACT_EMAIL") or None,
            google_maps_api_key=(os.getenv("GOOGLE_MAPS_API_KEY") or "").strip() or None,
            higgs_avatar_image_url=os.getenv("HIGGS_AVATAR_IMAGE_URL")
            or "https://docs.boson.ai/public/avatar/sample.jpg",
            clerk_secret_key=os.getenv("CLERK_SECRET_KEY") or None,
            clerk_jwt_key=os.getenv("CLERK_JWT_KEY") or None,
            google_oauth_client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID") or None,
            google_oauth_client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or None,
            google_oauth_refresh_token=os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN") or None,
            spectron_url=(os.getenv("SPECTRON_URL") or "").strip().rstrip("/") or None,
            spectron_context_id=(os.getenv("SPECTRON_CONTEXT_ID") or "").strip() or None,
            spectron_api_key=(os.getenv("SPECTRON_API_KEY") or "").strip() or None,
        )

    def voice_api_key(self) -> str | None:
        """The key the Concierge and Caller dial with. Avatar clips still use `boson_api_key`."""
        if self.voice_provider == "gemini":
            return self.gemini_api_key
        if self.voice_provider == "higgs":
            return self.boson_api_key
        return None

    def live_voice(self) -> str:
        """The preset the live session is opened with. Gemini rejects Higgs's `default`."""
        if self.voice_provider == "gemini":
            return self.gemini_live_voice
        return self.higgs_voice_renter

    def allowed_origins(self) -> list[str]:
        # An Origin header never ends in "/", so a pasted URL with one would never match.
        return sorted({o.rstrip("/") for o in (self.web_url, LOCAL_WEB, *self.cors_origins)})


def _list(name: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in os.getenv(name, "").split(",") if x.strip())


def _on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")
