"""Judge mode's front door (U-06). POST /api/judge/start hands a browser a new session and the socket
for its audio, as long as a line is free. The socket itself belongs to the browser transport (A-03)."""
from __future__ import annotations

import secrets
import string
import time
from collections.abc import Callable

ID_CHARS = string.ascii_letters + string.digits + "-_"
DEADLINE_S = 600   # a judge session holds its line at most this long, even if nobody hangs up


def new_session_id() -> str:
    """`s_` + 10 url-safe characters (docs/CONTRACTS.md). Minted in code, never by a model."""
    return "s_" + "".join(secrets.choice(ID_CHARS) for _ in range(10))


class JudgeLines:
    """Judge sessions holding a line now. A line frees when the browser transport calls `hang_up`, or
    at its deadline if nothing does."""

    def __init__(self, cap: int, deadline_s: float = DEADLINE_S, clock: Callable[[], float] = time.monotonic):
        self.cap = cap
        self.deadline_s = deadline_s
        self.clock = clock
        self.open: dict[str, float] = {}   # session_id -> when its line frees at the latest

    def take(self) -> str | None:
        """A new session id with a line held for it, or None when every line is taken."""
        now = self.clock()
        self.open = {sid: until for sid, until in self.open.items() if until > now}
        if len(self.open) >= self.cap:
            return None
        session_id = new_session_id()
        self.open[session_id] = now + self.deadline_s
        return session_id

    def hang_up(self, session_id: str) -> None:
        self.open.pop(session_id, None)
