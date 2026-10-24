"""The Dispatcher (A-06): the hand-off from the renter's brief to the calls. Plain code, no model.

When a session ends with a plan (`SessionEnded`, phase `calling`), it freezes the `CallPlan` from the
session (the picked listings in the renter's order, their extra questions, their viewing times as Toronto
datetimes, whether Landline may book, what they agreed to share) and queues one call per listing in the
same commit (`CallsScheduled`). Then it runs them:

- at most `cap` at once across every session: the Higgs lines minus one kept for renters calling in;
- a call that dials the phone network goes only to a number on the allowlist (hard rule 6), else it ends
  `blocked`, and only inside Toronto business hours, so a queued call waits for 9:00;
- each call has a deadline, and one that passes it ends `timeout`.

Each call ends once (`CallEnded`, keyed on the call) and its outcome follows (`OutcomeReady`). When every
call in the plan has an outcome, it emits `CallsCompleted`, and the renter is texted the results once.

Placing one call is a `CallRunner`'s job: the Caller over Twilio (A-05), the sim landlord (A-03), or a fake
in tests. With no runner, the plan is still frozen and its calls stay queued: nothing is made up.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from landline.config import Settings
from landline.contracts import (
    CallEnded,
    CallOutcome,
    CallPlan,
    CallsCompleted,
    CallsScheduled,
    CallState,
    CallStateChanged,
    EndReason,
    Event,
    OutcomeReady,
    SessionView,
    Shareable,
    Window,
    as_e164,
)
from landline.contracts.tools import Availability
from landline.orchestrator import Refused, SessionRuntime, SessionState
from landline.voice.calls import mint
from landline.voice.sms import Texter

log = logging.getLogger(__name__)

TORONTO = ZoneInfo("America/Toronto")
OPENS, CLOSES = time(9, 0), time(20, 0)   # Toronto business hours for calling listing agents
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
SMS_MAX = 1500                            # Twilio joins long texts; keep the results well inside one message


@dataclass(frozen=True)
class Dial:
    """One listing call, as the runner places it. Ids and the number come from code, never a model."""
    call_id: str
    session_id: str
    listing_id: str
    to: str | None          # an allowlisted number for a phone call; None off the phone network
    plan: CallPlan
    deadline: datetime


@dataclass(frozen=True)
class CallResult:
    end_reason: EndReason
    outcome: CallOutcome | None = None   # the Auditor's; without one, the outcome says only how it ended


Report = Callable[[CallState], Awaitable[None]]   # a runner says "ringing", "live" or "voicemail"


class CallRunner(Protocol):
    """Places one call and returns once it has ended."""
    transport: str          # calls.transport: voice | sim | stub
    uses_phone: bool        # dials the phone network: needs an allowlisted number and business hours

    async def place(self, dial: Dial, report: Report) -> CallResult: ...


class Dispatcher:
    def __init__(self, runtime: SessionRuntime, settings: Settings, *, runner: CallRunner | None = None,
                 texter: Texter | None = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.runtime, self.settings, self.runner, self.texter = runtime, settings, runner, texter
        self.clock, self.sleep = clock, sleep
        self.cap = settings.call_concurrency or max(1, settings.higgs_max_sessions - 1)
        self._lines = asyncio.Semaphore(self.cap)
        self.allowlist = frozenset(n for n in (as_e164(p) for p in settings.demo_agent_phones) if n)
        self._numbers = itertools.cycle(sorted(self.allowlist)) if self.allowlist else None
        self._tasks: set[asyncio.Task] = set()
        runtime.on("SessionEnded", self.on_session_ended)
        runtime.on("CallsCompleted", self.on_calls_completed)

    # The hand-off

    async def on_session_ended(self, event: Event, view: SessionView) -> None:
        """A brief ended. With a plan, freeze it and queue its calls in one commit; a second end (a retry,
        a duplicate webhook) finds the plan frozen and schedules nothing."""
        if view.phase != "calling":
            return
        sid = event.session_id
        state = self.runtime.state(sid)
        if state.plan is not None or state.request is None:
            return
        now = self.clock()
        plan = freeze(state, now)
        call_ids = [mint("c_") for _ in plan.listing_ids]
        runner = self.runner_for(sid)
        transport = runner.transport if runner else "stub"
        async with self.runtime.session_tx(sid) as tx:
            scheduled = await tx.apply(CallsScheduled(session_id=sid, at=now, idem_key=f"{sid}:scheduled",
                                                      plan=plan, call_ids=call_ids))
            db = self.runtime.db
            if scheduled and tx.conn is not None and db is not None:
                for cid, lid in zip(call_ids, plan.listing_ids, strict=True):
                    await db.calls.create(cid, session_id=sid, role="listing", transport=transport,
                                          state="queued", listing_id=lid, conn=tx.conn)
        if not scheduled:
            return
        if runner is None:
            log.warning("session %s: %d calls queued, but no caller is configured", sid, len(call_ids))
            return
        for cid, lid in zip(call_ids, plan.listing_ids, strict=True):
            self._spawn(self._call(cid, lid, plan, runner))

    def runner_for(self, session_id: str) -> CallRunner | None:
        """The runner for one session's calls. A routing runner (voice/routing.py) picks by the session, so a
        judge's calls never reach the phone network; any other runner takes every session's calls."""
        pick = getattr(self.runner, "for_session", None)
        return pick(session_id) if pick is not None else self.runner

    # One call

    async def _call(self, call_id: str, listing_id: str, plan: CallPlan, runner: CallRunner) -> None:
        to = next(self._numbers) if runner.uses_phone and self._numbers else None
        if runner.uses_phone and (to is None or to not in self.allowlist):
            await self._state(plan.session_id, call_id, "blocked")
            await self._finish(plan, call_id, listing_id, CallResult("blocked"))
            return
        async with self._lines:
            if runner.uses_phone:
                await self._wait_for_hours()
            dial = Dial(call_id=call_id, session_id=plan.session_id, listing_id=listing_id, to=to, plan=plan,
                        deadline=self.clock() + timedelta(seconds=self.settings.listing_call_max_s))
            await self._state(plan.session_id, call_id, "dialing")

            async def report(state: CallState) -> None:
                await self._state(plan.session_id, call_id, state)

            try:
                async with asyncio.timeout(self.settings.listing_call_max_s):
                    result = await runner.place(dial, report)
            except TimeoutError:
                result = CallResult("timeout")
            except Exception:
                log.exception("call %s to %s failed", call_id, listing_id)
                result = CallResult("failed")
        await self._finish(plan, call_id, listing_id, result)

    async def _wait_for_hours(self) -> None:
        """Hold a queued call until Toronto business hours, unless FORCE_BUSINESS_HOURS=1."""
        while not self.settings.force_business_hours:
            wait = until_open(self.clock())
            if wait <= 0:
                return
            await self.sleep(wait)

    async def _state(self, sid: str, call_id: str, state: CallState) -> None:
        await self._emit(CallStateChanged(session_id=sid, at=self.clock(), idem_key=f"{call_id}:{state}",
                                          call_id=call_id, state=state))
        if self.runtime.db is not None:
            await self.runtime.db.calls.set_state(call_id, state, answered=state == "live")

    async def _finish(self, plan: CallPlan, call_id: str, listing_id: str, result: CallResult) -> None:
        """End the call once, then record its outcome, then complete the plan if this was the last one."""
        sid, now, reason = plan.session_id, self.clock(), result.end_reason
        await self._emit(CallEnded(session_id=sid, at=now, idem_key=f"{call_id}:ended", call_id=call_id,
                                   reason=reason))
        if self.runtime.db is not None:
            await self.runtime.db.calls.finalize(call_id, reason)   # the first end wins
        outcome = result.outcome or CallOutcome(call_id=call_id, listing_id=listing_id, end_reason=reason)
        await self._emit(OutcomeReady(session_id=sid, at=now, idem_key=f"{call_id}:outcome", outcome=outcome))
        state = self.runtime.state(sid)
        if state.plan and all(lid in state.outcomes for lid in state.plan.listing_ids):
            await self._emit(CallsCompleted(session_id=sid, at=now, idem_key=f"{sid}:completed",
                                            call_ids=list(state.calls)))

    async def _emit(self, event: Event) -> None:
        try:
            await self.runtime.emit(event)
        except Refused as e:
            log.warning("%s %s refused: %s", event.type, event.idem_key, e)

    # The results

    async def on_calls_completed(self, event: Event, view: SessionView) -> None:
        """Text the renter the results. CallsCompleted commits once per plan, so this runs once."""
        state = self.runtime.state(event.session_id)
        sender = self.settings.twilio_phone_number
        if state.channel != "phone" or not state.caller_phone or not view.results:
            return
        if self.texter is None or not self.texter.ready or not sender:
            log.warning("session %s: results ready but no way to text them", event.session_id)
            return
        link = f"{self.settings.web_url.rstrip('/')}/s/{event.session_id}"
        self.texter.send_soon(state.caller_phone, sender, results_text(view.results, link))

    # Tasks

    def _spawn(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def idle(self) -> None:
        """Wait until the runtime's subscribers and every call they started have finished (tests)."""
        while True:
            await self.runtime.idle()
            if not self._tasks:
                return
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await asyncio.sleep(0)   # let finished calls leave the set (see SessionRuntime.idle)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


def freeze(state: SessionState, now: datetime) -> CallPlan:
    """The CallPlan from what the brief recorded. Never edited afterwards."""
    r, p = state.request, state.preferences
    assert r is not None
    windows = [w for a in r.availability if (w := window(a, now))]
    return CallPlan(session_id=state.session_id, user_id=state.user_id, listing_ids=r.listing_ids[:5],
                    questions=r.questions, windows=windows, may_book=r.may_book,
                    shareable=Shareable(move_in=p.move_in, occupants=p.occupants, pets=p.pets),
                    language=p.language or "en", created_at=now)


def window(a: Availability, now: datetime) -> Window | None:
    """`saturday 13:00–17:00` as the next such window in Toronto time; None if it makes no sense or, for
    today, has already passed. A weekday whose window passed this week means next week."""
    local = now.astimezone(TORONTO)
    if a.day == "today":
        day = local.date()
    elif a.day == "tomorrow":
        day = local.date() + timedelta(days=1)
    else:
        day = local.date() + timedelta(days=(WEEKDAYS.index(a.day) - local.weekday()) % 7)
    start, end = _at(day, a.from_), _at(day, a.to)
    if end <= start:
        return None
    if end <= local:
        if a.day in ("today", "tomorrow"):
            return None
        start, end = start + timedelta(days=7), end + timedelta(days=7)
    return Window(start=start, end=end)


def until_open(now: datetime) -> float:
    """Seconds until Toronto business hours; 0 inside them."""
    local = now.astimezone(TORONTO)
    if OPENS <= local.time() < CLOSES:
        return 0.0
    day = local.date() if local.time() < OPENS else local.date() + timedelta(days=1)
    return (datetime.combine(day, OPENS, TORONTO) - local).total_seconds()


def results_text(results: str, link: str) -> str:
    body = f"Landline: {results}"
    if len(body) > SMS_MAX - len(link) - 6:
        body = body[: SMS_MAX - len(link) - 7].rsplit(" ", 1)[0] + "…"
    return f"{body}\nMap: {link}"


def _at(day: date, clock: str) -> datetime:
    h, m = (int(x) for x in clock.split(":"))
    return datetime.combine(day, time(h, m), TORONTO)
