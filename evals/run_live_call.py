"""Rings your phone with a real listing call, skipping the renter's brief. You play the listing agent and the
real Caller talks to you, live.

    uv run python -m evals.run_live_call                          # a sample plan for the first listing
    uv run python -m evals.run_live_call --listing livrent:145662 --text
    uv run python -m evals.run_live_call --case linda-scripted    # a mock call's listing and plan

It starts the API itself on :8000, where ngrok forwards (stop `make dev` first). Then it records a phone
renter's plan for one listing and hangs that session up, as if the renter had. From there it is the real
path: the Dispatcher dials the one phone on DEMO_AGENT_PHONES through Twilio, from TWILIO_PHONE_NUMBER;
Twilio streams the call's audio back through PUBLIC_URL; and the Caller talks to you on Higgs with its
tools. The transcript prints as you talk. The outcome and the results text print when the call ends, then
the API stops. `--text` also texts you the results, as a renter would get them.

Needs in .env: TRANSPORT=voice, PUBLIC_URL (the ngrok address), TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
TWILIO_PHONE_NUMBER, BOSON_API_KEY and your phone alone on DEMO_AGENT_PHONES. And ngrok running. Outside
9 am to 8 pm Toronto time, FORCE_BUSINESS_HOURS=1 too. A mock call's lines are ignored: you are the landlord.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import uvicorn

from evals.run_mock_call import show_outcome
from evals.sim.calls import PlanSpec, ShareSpec
from evals.sim.scripted_call import load_mock_call, load_mock_calls
from landline.config import Settings
from landline.contracts import (
    CallsRequested,
    Listing,
    PreferencesUpdated,
    SessionEnded,
    SessionStarted,
    as_e164,
)
from landline.contracts.tools import Availability, PreferenceDelta
from landline.db.catalogue import Catalogue
from landline.orchestrator import SessionRuntime, SessionState
from landline.voice.dispatch import until_open

PORT = 8000
# "tomorrow" is never a window that has already started, whatever the time of the run.
SAMPLE = PlanSpec(availability=[Availability(day="tomorrow", from_="12:00", to="16:00")], may_book=True,
                  questions=["Is the building quiet at night?"],
                  shareable=ShareSpec(move_in="December 1", occupants=1, pets=["cat"]))
STATES = {"dialing": "Dialing your phone (...{phone}) from ...{sender}.",
          "ringing": "Ringing: answer your phone.",
          "live": "Connected. You're the listing agent: talk to it.",
          "voicemail": "It reached voicemail.",
          "blocked": "Blocked: the phone isn't on the allowlist, or Twilio isn't set up."}


def problems(s: Settings, now: datetime) -> list[str]:
    """What stops a real call before anything starts."""
    out = []
    if s.transport != "voice":
        out.append(f"TRANSPORT is {s.transport!r}, not 'voice': nothing would dial")
    if not s.public_url:
        out.append("PUBLIC_URL is empty: set it to your ngrok address, so Twilio can reach this script")
    if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_phone_number):
        out.append("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER are all needed")
    if not s.boson_api_key:
        out.append("BOSON_API_KEY is empty: the Caller runs on Higgs")
    if len(phones := allowlist(s)) != 1:
        out.append(f"DEMO_AGENT_PHONES has {len(phones)} numbers: leave only your phone, so no one else "
                   "rings")
    if not s.force_business_hours and until_open(now) > 0:
        out.append("it's outside Toronto calling hours (9 am to 8 pm): set FORCE_BUSINESS_HOURS=1 to call "
                   "anyway")
    return out


def allowlist(s: Settings) -> set[str]:
    return {n for n in (as_e164(p) for p in s.demo_agent_phones) if n}


def pick_listing(catalogue: Catalogue, wanted: str | None) -> Listing | None:
    if wanted:
        return catalogue.by_id.get(wanted)
    return next((x for x in catalogue.listings if x.address and x.rent), None)


async def place_call(runtime: SessionRuntime, settings: Settings, listing_id: str, plan: PlanSpec, *,
                     text: bool = False, wait_s: float | None = None, say=print) -> SessionState:
    """A phone renter's session with `plan` for one listing, hung up, so the Dispatcher places its call.
    Returns the session once its calls are done. `text`: the renter's phone is yours, so the results text
    comes to you; otherwise there is no renter phone and no text."""
    phone, sender = next(iter(allowlist(settings))), settings.twilio_phone_number or ""
    sid = f"s_live_{datetime.now(UTC):%Y%m%d%H%M%S}"

    async def on_state(event, _view) -> None:
        if event.session_id == sid:
            say(STATES.get(event.state, f"Call {event.state}.").format(phone=phone[-4:], sender=sender[-4:]))
    runtime.on("CallStateChanged", on_state)

    now = datetime.now(UTC)
    await runtime.start(SessionStarted(session_id=sid, at=now, idem_key=f"{sid}:started", channel="phone",
                                       caller_phone=phone if text else None))
    share = plan.shareable
    delta = PreferenceDelta(move_in=share.move_in, occupants=share.occupants, pets=share.pets or None)
    await runtime.emit(PreferencesUpdated(session_id=sid, at=now, idem_key=f"{sid}:prefs", delta=delta))
    await runtime.emit(CallsRequested(session_id=sid, at=now, idem_key=f"{sid}:plan",
                                      listing_ids=[listing_id], extra_questions=plan.questions,
                                      availability=plan.availability, may_book=plan.may_book))
    await runtime.emit(SessionEnded(session_id=sid, at=now, idem_key=f"{sid}:ended", reason="hangup"))
    limit = time.monotonic() + (wait_s if wait_s is not None else settings.listing_call_max_s + 90)
    while runtime.state(sid).phase != "done" and time.monotonic() < limit:
        await asyncio.sleep(0.5)
    with contextlib.suppress(TimeoutError):   # the results text starts from a subscriber of CallsCompleted
        await asyncio.wait_for(runtime.idle(), 30)
    return runtime.state(sid)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


async def reaches(public_url: str) -> str:
    """'' when PUBLIC_URL reaches the API this script started, else why not."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{public_url}/health", headers={"ngrok-skip-browser-warning": "1"})
    except httpx.HTTPError as e:
        return f"{public_url} didn't answer ({type(e).__name__})"
    if r.status_code != 200:
        return f"{public_url}/health answered {r.status_code}"
    try:
        transport = r.json().get("transport")
    except ValueError:
        transport = None
    if transport != "voice":
        return f"{public_url} doesn't reach the API this script started"
    return ""


async def run(listing_id: str | None, plan: PlanSpec, text: bool) -> int:
    settings = Settings.load()
    if issues := problems(settings, datetime.now(UTC)):
        print("Not calling:\n" + "\n".join(f"  - {p}" for p in issues), file=sys.stderr)
        return 1
    if not port_free(PORT):
        print(f"Port {PORT} is in use: stop `make dev` (Ctrl+C) first. ngrok forwards there.",
              file=sys.stderr)
        return 1
    from landline.app import create_app  # here, so --help and the checks above don't build an app
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info"))
    serving = asyncio.create_task(server.serve())
    try:
        while not server.started:
            if serving.done():
                return 1   # uvicorn said why
            await asyncio.sleep(0.05)
        if why := await reaches(settings.public_url or ""):
            print(f"Not calling: {why}. Is ngrok running?\n"
                  f"  ngrok http {PORT} --url {settings.public_url}", file=sys.stderr)
            return 1
        listing = pick_listing(app.state.catalogue, listing_id)
        if listing is None:
            print(f"No listing {listing_id!r} in the API's catalogue ({app.state.catalogue.origin}). "
                  "Pass --listing with one from /api/catalogue.", file=sys.stderr)
            return 1
        beds = "studio" if listing.beds == 0 else f"{listing.beds:g} bed"
        print(f"\nCalling about {listing.address} ({listing.listing_id}): {beds}, ${listing.rent:,} a month.")
        windows = ", ".join(f"{a.day} {a.from_}-{a.to}" for a in plan.availability) or "none"
        print(f"Renter: viewings {windows}, may book: {plan.may_book}, "
              f"questions: {plan.questions or 'none'}, pets: {plan.shareable.pets or 'none'}. "
              f"Results text: {'to you' if text else 'off'}.\n")
        call = asyncio.create_task(place_call(app.state.runtime, settings, listing.listing_id, plan,
                                              text=text))
        await asyncio.wait({call, serving}, return_when=asyncio.FIRST_COMPLETED)
        if not call.done():   # Ctrl+C, or the API died: its shutdown hangs the call up
            call.cancel()
            print("The API stopped before the call finished.", file=sys.stderr)
            return 1
        state = call.result()
        print()
        show_outcome(state, listing.listing_id)
        if text and state.results:
            print("  (texted to your phone)")
        return 0
    finally:
        server.should_exit = True
        await serving


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.platform == "win32":   # psycopg's async pool, with DATABASE_URL set, can't use the Proactor loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--listing", help="a listing id from the API's catalogue (default: the first one)")
    parser.add_argument("--case", help="take the listing and plan from this mock call "
                                       "(evals/fixtures/mock_calls/)")
    parser.add_argument("--file", type=Path, help="take the listing and plan from a mock call JSON anywhere")
    parser.add_argument("--text", action="store_true", help="text you the results afterwards")
    args = parser.parse_args()
    plan, listing_id = SAMPLE, args.listing
    if args.case or args.file:
        if args.file:
            case = load_mock_call(args.file)
        else:
            known = {c.id: c for c in load_mock_calls()}
            if args.case not in known:
                parser.error(f"no mock call {args.case}; have {', '.join(sorted(known))}")
            case = known[args.case]
        plan, listing_id = case.plan, args.listing or case.listing_id
    try:
        return asyncio.run(run(listing_id, plan, args.text))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
