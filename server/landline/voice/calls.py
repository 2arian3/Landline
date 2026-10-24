"""The renter calls that hold a Higgs line right now. One registry lives on app.state (hard rule 4: no
module-level dicts with session data), until O-02's runtime and the calls table take over."""
from __future__ import annotations

import logging
import secrets
import string
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

ID_CHARS = string.ascii_letters + string.digits + "-_"
CLAIM_WITHIN_S = 60   # TwiML minted the call but Twilio never opened the stream: free the line after this


def mint(prefix: str) -> str:
    """`s_` / `c_` + 10 url-safe characters (docs/CONTRACTS.md). Minted in code, never by a model."""
    return prefix + "".join(secrets.choice(ID_CHARS) for _ in range(10))


@dataclass
class RenterCall:
    call_id: str
    session_id: str
    caller: str | None
    opened_at: float
    called: str | None = None   # the number the renter dialled; texts go out from it
    claimed: bool = False
    end_reason: str | None = None


class RenterCalls:
    def __init__(self, cap: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.cap = cap
        self.clock = clock
        self.live: dict[str, RenterCall] = {}
        self.recent: deque[RenterCall] = deque(maxlen=50)   # ended calls, newest last

    def open(self, caller: str | None, session_id: str | None = None,
             called: str | None = None) -> RenterCall | None:
        """A new call with its ids, or None when every line is taken. Judge mode passes the session it
        already started; a phone call gets a new one."""
        now = self.clock()
        for call in [c for c in self.live.values() if not c.claimed and now - c.opened_at > CLAIM_WITHIN_S]:
            self.finalize(call.call_id, "never_connected")
        if len(self.live) >= self.cap:
            return None
        call = RenterCall(call_id=mint("c_"), session_id=session_id or mint("s_"), caller=caller,
                          opened_at=now, called=called)
        self.live[call.call_id] = call
        return call

    def claim(self, call_id: str) -> RenterCall | None:
        """The call a media stream belongs to. Works once per call; unknown ids get None."""
        call = self.live.get(call_id)
        if call is None or call.claimed:
            return None
        call.claimed = True
        return call

    def finalize(self, call_id: str, reason: str) -> bool:
        """Ends the call and frees its line. Only the first caller for a call_id does anything."""
        call = self.live.pop(call_id, None)
        if call is None:
            return False
        call.end_reason = reason
        self.recent.append(call)
        log.info("call %s (session %s) ended: %s", call.call_id, call.session_id, reason)
        return True
