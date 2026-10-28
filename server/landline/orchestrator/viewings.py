"""Viewings the listing agent confirms (O-08). Plain code, no model.

O-08b: when a listing call books a viewing (ViewingBooked), code makes one viewing_confirmations row for that
call, with a token from `secrets.token_urlsafe`, and texts the number the call dialled a link to confirm it:

    Landline: please confirm the viewing at 412 Markham St on Saturday, Oct 31 at 2 pm: <WEB_URL>/v/<token>

One row and one text per call. The text goes only to `calls.to_e164` (A-13a fills it when the call is
dialled), and only when that number is on the allowlist (hard rule 6). A sim or judge call, a call with no
dialled number yet, or a number off the allowlist gets its row and its card, and no text. The card's
viewing comes from the session's state (orchestrator/board.py), committed with the booking itself through
session_tx (hard rule 4). O-08c (confirm or decline, then the invites) builds on these rows.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from landline.config import Settings
from landline.contracts import Event, SessionView, ViewingBooked, as_e164
from landline.orchestrator.runtime import SessionRuntime

log = logging.getLogger(__name__)

TORONTO = ZoneInfo("America/Toronto")
CONFIRM_TEXT = "Landline: please confirm the viewing at {address} on {when}: {link}"


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


def when(at: datetime) -> str:
    """"Saturday, Oct 31 at 2 pm", in Toronto time."""
    local = at.astimezone(TORONTO)
    hour = local.strftime("%I").lstrip("0")
    minutes = f":{local:%M}" if local.minute else ""
    return f"{local:%A}, {local:%b} {local.day} at {hour}{minutes} {local:%p}".replace("AM", "am").replace(
        "PM", "pm")
