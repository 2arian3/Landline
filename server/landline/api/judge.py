"""Judge mode's front door (U-06). POST /api/judge/start hands a browser a new session and the socket
for its audio, as long as a line is free. The socket itself belongs to the browser transport (A-03)."""
from __future__ import annotations

import secrets
import string
import time
from collections.abc import Callable, Mapping

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


class StartLimit:
    """At most `per_ip` judge sessions started from one address in any `window_s`."""

    def __init__(self, per_ip: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.per_ip = per_ip
        self.window_s = window_s
        self.clock = clock
        self.starts: dict[str, list[float]] = {}   # address -> its start times inside the window

    def wait_s(self, ip: str) -> float:
        """Seconds until `ip` may start another session; 0 when it may start now."""
        since = self.clock() - self.window_s
        self.starts = {a: kept for a, ts in self.starts.items() if (kept := [t for t in ts if t > since])}
        recent = self.starts.get(ip, [])
        return 0 if len(recent) < self.per_ip else recent[0] - since

    def record(self, ip: str) -> None:
        self.starts.setdefault(ip, []).append(self.clock())


def client_ip(headers: Mapping[str, str], peer: str | None) -> str:
    """The judge's address. The gateway's nginx overwrites X-Real-IP with the address it saw, so a
    browser can't pick its own; the first X-Forwarded-For entry covers a proxy that only sets that."""
    if real := headers.get("x-real-ip", "").strip():
        return real
    if forwarded := headers.get("x-forwarded-for", "").split(",")[0].strip():
        return forwarded
    return peer or "unknown"
