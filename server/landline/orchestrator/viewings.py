"""Viewings the listing agent confirms (O-08). Plain code, no model.

O-08b: when a listing call books a viewing (ViewingBooked), code makes one viewing_confirmations row for that
call, with a token from `secrets.token_urlsafe`, and texts the number the call dialled a link to confirm it:

    Landline: please confirm the viewing at 412 Markham St on Saturday, Oct 31 at 2 pm: <WEB_URL>/v/<token>

One row and one text per call. The text goes only to `calls.to_e164` (A-13a fills it when the call is
dialled), and only when that number is on the allowlist (hard rule 6). A sim or judge call, a call with no
dialled number yet, or a number off the allowlist gets its row and its card, and no text. The card's
viewing comes from the session's state (orchestrator/board.py), committed with the booking itself through
session_tx (hard rule 4).

O-08c: `Decisions.confirm(token, agent_email)` and `Decisions.decline(token)`, which the /v/<token> page's API
calls (O-08d). A token decides once (one conditional update, so two answers can't both win). On a confirm,
Google Calendar sends a 30-minute invite to the agent, and to the renter when their profile has an email;
the renter is texted "Confirmed: …" with "Invite sent to <email>", or an add-to-calendar link when there's
no email, no Google keys, or Calendar fails (the confirm stands either way). On a decline, the renter is
texted that the agent can't do that time. Times, addresses and emails come only from the database and the
catalogue (hard rule 2), and the event's two lines on what the call heard are the call's grounded facts.
Renter texts follow the results text's rule: a phone session's caller, who called us; the allowlist is for
listing agents' numbers.
"""
from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from landline.config import Settings
from landline.contracts import CallOutcome, Event, SessionView, ViewingBooked, ViewingDecided, as_e164
from landline.db.rows import ViewingConfirmationRow
from landline.orchestrator.calendar import CalendarError, add_to_calendar_link
from landline.orchestrator.handlers import Refused
from landline.orchestrator.runtime import SessionRuntime, UnknownSession
from landline.orchestrator.state import SessionState

log = logging.getLogger(__name__)

TORONTO = ZoneInfo("America/Toronto")
CONFIRM_TEXT = "Landline: please confirm the viewing at {address} on {when}: {link}"
CONFIRMED_INVITE = "Landline: Confirmed: {address}, {when}. Invite sent to {email}."
CONFIRMED_LINK = "Landline: Confirmed: {address}, {when}. Add it to your calendar: {link}"
DECLINED = "Landline: {address} can't do {when}. Want me to find another time?"
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class ConfirmLinks:
    """Subscribes to ViewingBooked once, at boot."""

    def __init__(self, runtime: SessionRuntime, settings: Settings, texter: Any | None):
        self.runtime, self.settings, self.texter = runtime, settings, texter
        self.allowlist = frozenset(n for n in (as_e164(p) for p in settings.demo_agent_phones) if n)
        runtime.on("ViewingBooked", self.on_booked)

    async def on_booked(self, event: Event, view: SessionView) -> None:
        db = self.runtime.db
        if db is None or not isinstance(event, ViewingBooked):
            return
        call = await db.calls.get(event.call_id)
        if call is None:
            log.warning("call %s: booked a viewing but has no calls row; no confirm link", event.call_id)
            return
        token = secrets.token_urlsafe(16)
        row = await db.confirmations.create(token, call_id=event.call_id, session_id=event.session_id,
                                            listing_id=event.listing_id, starts_at=event.starts_at)
        if row is None:
            return   # this call's viewing already has its row, and its text if it had one
        to = as_e164(call.to_e164) if call.to_e164 else None
        why = (f"a {call.transport} call" if call.transport != "voice"
               else "no dialled number yet (A-13a)" if to is None
               else "its number is off the allowlist" if to not in self.allowlist
               else "no way to text" if not (self.texter is not None and self.texter.ready
                                             and self.settings.twilio_phone_number)
               else None)
        if why is not None:
            log.info("call %s: viewing confirmation %s… made; no confirm link texted: %s", event.call_id,
                     token[:6], why)
            return
        address = self._address(event.listing_id)
        link = f"{self.settings.web_url.rstrip('/')}/v/{token}"
        text = CONFIRM_TEXT.format(address=address, when=when(event.starts_at), link=link)
        log.info("call %s: texting the listing agent the confirm link", event.call_id)
        self.texter.send_soon(to, self.settings.twilio_phone_number, text)

    def _address(self, listing_id: str) -> str:
        listing = self.runtime.catalogue.by_id.get(listing_id)
        return listing.address if listing else "the listing"


@dataclass(frozen=True)
class Answer:
    """What a confirm or decline came to, for the /v page (O-08d). `outcome` is "confirmed" or "declined"
    when this answer decided it; "already_decided" (with `status`, how it was decided) for a second answer;
    "not_found" for an unknown token; "bad_email" when the agent's email isn't one."""
    outcome: Literal["confirmed", "declined", "already_decided", "not_found", "bad_email"]
    status: str | None = None
    invited: tuple[str, ...] = ()
    event_id: str | None = None


class Decisions:
    """The listing agent's answer to a booked viewing (O-08c). Made once, at boot."""

    def __init__(self, runtime: SessionRuntime, settings: Settings, texter: Any | None,
                 calendar: Any | None):
        self.runtime, self.settings, self.texter, self.calendar = runtime, settings, texter, calendar

    async def confirm(self, token: str, agent_email: str) -> Answer:
        email = valid_email(agent_email)
        if email is None:
            return Answer("bad_email")
        db = self.runtime.db
        if db is None:
            return Answer("not_found")
        row = await db.confirmations.decide(token, status="confirmed", agent_email=email)
        if row is None:
            return await self._undecided(token)
        await self._flip(row)
        state = await self._state(row.session_id)
        address = self._address(row.listing_id)
        description = heard_lines(_outcome(state, row.call_id))
        renter_email = await self._renter_email(state)
        invited = tuple(e for e in (email, renter_email) if e)
        event_id = None
        if self.calendar is not None:
            try:
                event_id = await self.calendar.create_event(f"Viewing: {address}", address, description,
                                                            row.starts_at, invited)
                await db.confirmations.set_event(token, event_id)
            except CalendarError as e:   # the confirm stands; the renter gets the link instead
                log.warning("viewing %s…: the agent confirmed, but no invite went out: %s", token[:6], e)
        if event_id is not None and renter_email:
            text = CONFIRMED_INVITE.format(address=address, when=when(row.starts_at), email=renter_email)
        else:
            link = add_to_calendar_link(f"Viewing: {address}", address, description, row.starts_at)
            text = CONFIRMED_LINK.format(address=address, when=when(row.starts_at), link=link)
        self._text_renter(state, text, token)
        log.info("viewing %s…: confirmed by the agent; event %s", token[:6], event_id or "none")
        return Answer("confirmed", "confirmed", invited if event_id else (), event_id)

    async def decline(self, token: str) -> Answer:
        db = self.runtime.db
        if db is None:
            return Answer("not_found")
        row = await db.confirmations.decide(token, status="declined")
        if row is None:
            return await self._undecided(token)
        await self._flip(row)
        state = await self._state(row.session_id)
        text = DECLINED.format(address=self._address(row.listing_id), when=when(row.starts_at))
        self._text_renter(state, text, token)
        log.info("viewing %s…: declined by the agent", token[:6])
        return Answer("declined", "declined")

    async def _flip(self, row: ViewingConfirmationRow) -> None:
        """The card's viewing takes the answer through session_tx (ViewingDecided) while the session is live.
        After a restart the answer is in viewing_confirmations alone, which is what the /v page reads."""
        if not self.runtime.has(row.session_id):
            return
        event = ViewingDecided(session_id=row.session_id, at=datetime.now(UTC),
                               idem_key=f"{row.call_id}:decided", call_id=row.call_id,
                               listing_id=row.listing_id, starts_at=row.starts_at, status=row.status)
        try:
            await self.runtime.emit(event)
        except (Refused, UnknownSession) as e:   # the decision stands; only the card didn't flip
            log.warning("call %s: viewing %s, but the card didn't flip: %s", row.call_id, row.status, e)

    async def _undecided(self, token: str) -> Answer:
        row = await self.runtime.db.confirmations.get(token)
        return Answer("not_found") if row is None else Answer("already_decided", row.status)

    async def _state(self, session_id: str) -> SessionState | None:
        """The session as it stands: live in the runtime, or its last snapshot after a restart."""
        if self.runtime.has(session_id):
            return self.runtime.state(session_id)
        row = await self.runtime.db.sessions.get(session_id)
        snapshot = (row.snapshot or {}).get("state") if row else None
        return SessionState.model_validate(snapshot) if snapshot else None

    async def _renter_email(self, state: SessionState | None) -> str | None:
        if state is None or not state.caller_phone:
            return None
        user = await self.runtime.db.users.by_phone(as_e164(state.caller_phone) or state.caller_phone)
        return valid_email(user.email) if user is not None and user.email else None

    def _text_renter(self, state: SessionState | None, text: str, token: str) -> None:
        """The results text's rule: a phone session's caller, from our number."""
        sender = self.settings.twilio_phone_number
        if state is None or state.channel != "phone" or not state.caller_phone:
            log.info("viewing %s…: no renter's phone to text", token[:6])
            return
        if self.texter is None or not self.texter.ready or not sender:
            log.warning("viewing %s…: no way to text the renter", token[:6])
            return
        self.texter.send_soon(state.caller_phone, sender, text)

    def _address(self, listing_id: str) -> str:
        listing = self.runtime.catalogue.by_id.get(listing_id)
        return listing.address if listing else "the listing"


def valid_email(text: str | None) -> str | None:
    """An email address as the agent typed it, trimmed, or None when it isn't one."""
    email = (text or "").strip()
    return email if len(email) <= 254 and _EMAIL.fullmatch(email) else None


def heard_lines(outcome: CallOutcome | None) -> str:
    """The event's two lines on what the call learned: its first two grounded facts, each with the agent's
    words, or a plain line when it heard none."""
    facts = [f for f in (outcome.facts if outcome else []) if f.quote.strip() and f.value.strip()][:2]
    if not facts:
        return "Booked by Landline on a call with the listing agent."
    return "\n".join(f'{f.field.replace("_", " ")}: {f.value} ("{f.quote}")' for f in facts)


def _outcome(state: SessionState | None, call_id: str) -> CallOutcome | None:
    if state is None:
        return None
    return next((o for o in state.outcomes.values() if o.call_id == call_id), None)


def when(at: datetime) -> str:
    """"Saturday, Oct 31 at 2 pm", in Toronto time."""
    local = at.astimezone(TORONTO)
    hour = local.strftime("%I").lstrip("0")
    minutes = f":{local:%M}" if local.minute else ""
    return f"{local:%A}, {local:%b} {local.day} at {hour}{minutes} {local:%p}".replace("AM", "am").replace(
        "PM", "pm")
