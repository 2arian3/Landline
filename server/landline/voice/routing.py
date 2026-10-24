"""Which runner places a session's listing calls (hard rule 6: judge mode never touches the phone network).

The Dispatcher asks `for_session(session_id)` before each call. A phone renter's calls go to the phone
runner (Twilio, TRANSPORT=voice) when there is one; every other session, a judge on /try or a browser
brief, goes to the sim runner, whatever TRANSPORT says. A phone session with no phone runner gets the sim
runner when TRANSPORT=sim, and otherwise none, so its calls stay queued.
"""
from __future__ import annotations

import logging

from landline.orchestrator import SessionRuntime, UnknownSession
from landline.voice.dispatch import CallRunner

log = logging.getLogger(__name__)


class ByChannel:
    def __init__(self, runtime: SessionRuntime, *, phone: CallRunner | None, sim: CallRunner | None,
                 sim_for_phone: bool = False) -> None:
        self.runtime, self.phone, self.sim, self.sim_for_phone = runtime, phone, sim, sim_for_phone

    def for_session(self, session_id: str) -> CallRunner | None:
        try:
            channel = self.runtime.state(session_id).channel
        except UnknownSession:
            return None
        if channel != "phone":
            return self.sim                      # never the phone runner
        if self.phone is not None:
            return self.phone
        return self.sim if self.sim_for_phone else None
