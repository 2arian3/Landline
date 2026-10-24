"""A restart that doesn't cut live calls (#167). On SIGTERM, uvicorn closes every websocket at once (the
renter's audio and the listing agent's) and, after its graceful timeout, cancels the page streams with a
traceback each. So the app takes SIGTERM first: it stops taking calls and judge sessions (both hear "lines
busy"), lets the live calls finish for up to DRAIN_S, ends any still going `failed`, ends the page streams,
and only then hands the signal to the handler that was there before, uvicorn's, which stops the server.

systemd gives a stopping service 90 s by default (TimeoutStopSec) before it kills it: room for DRAIN_S
and uvicorn's own --timeout-graceful-shutdown after it. A second SIGTERM goes straight to uvicorn."""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

log = logging.getLogger(__name__)

DRAIN_S = 20.0   # how long live calls get to finish before the rest are ended
EVERY_S = 0.5    # how often they're counted meanwhile


class Drain:
    """On app.state. While `draining`, new calls and judges are turned away; `closing` ends the page
    streams (api/routes.py)."""

    def __init__(self, state: Any, limit_s: float = DRAIN_S, every_s: float = EVERY_S) -> None:
        self.state, self.limit_s, self.every_s = state, limit_s, every_s
        self.draining = False
        self.closing = asyncio.Event()
        self.task: asyncio.Task | None = None

    def live(self) -> int:
        """Calls on the line: renters' (phone and judge) and listing calls."""
        return len(self.state.calls.live) + len(self.state.outbound.live)

    async def run(self) -> None:
        """Waits up to `limit_s` for the live calls to end on their own, ends any still going `failed` (each
        once: the listing call's row is written before it is logged), then ends the page streams. A second
        run does nothing."""
        if self.draining:
            return
        self.draining = True
        loop = asyncio.get_running_loop()
        until = loop.time() + self.limit_s
        log.warning("restarting: taking no new calls; %d live, waiting up to %.0f s", self.live(),
                    self.limit_s)
        while self.live() and loop.time() < until:
            await asyncio.sleep(self.every_s)
        db = getattr(getattr(self.state, "runtime", None), "db", None)
        for call_id in list(self.state.calls.live):
            self.state.calls.finalize(call_id, "failed")
        for call_id in list(self.state.outbound.live):
            # The row before the log (#217). The in-memory end alone is what a restart used to keep.
            await self.state.outbound.end(call_id, "failed", db)
        self.closing.set()
        for store in (self.state.runtime, self.state.sessions):   # wake the streams waiting for a change
            async with store.changed:
                store.changed.notify_all()
        log.warning("restarting: calls drained, page streams ended")


def install(state: Any) -> Drain:
    """The app's Drain. In the main thread (under uvicorn; not the test client's), SIGTERM runs it first and
    then the handler that was there before."""
    drain = Drain(state)
    loop = asyncio.get_running_loop()
    try:
        before = signal.getsignal(signal.SIGTERM)

        def on_term(signum: int, frame: Any) -> None:
            def start() -> None:
                drain.task = asyncio.ensure_future(_stop(drain, before, signum, frame))
            loop.call_soon_threadsafe(start)

        signal.signal(signal.SIGTERM, on_term)
    except ValueError:   # signals only work in the main thread
        pass
    return drain


async def _stop(drain: Drain, before: Any, signum: int, frame: Any) -> None:
    try:
        await drain.run()
    except Exception:
        log.exception("restarting: the drain failed")
    finally:
        if callable(before):
            before(signum, frame)   # uvicorn's: it stops the server now
        else:
            signal.signal(signal.SIGTERM, before if before is not None else signal.SIG_DFL)
            signal.raise_signal(signal.SIGTERM)
