"""The Dispatcher (A-06): the hand-off from the renter's brief to the calls. Plain code, no model.

When a session ends with a plan (`SessionEnded`, phase `calling`), it freezes the `CallPlan` from the
session (the picked listings in the renter's order, their extra questions, their viewing times as Toronto
datetimes, whether Landline may book, and only what they agreed to share: user_profiles.shareable, set on
/me) and queues one call per listing in the same commit (`CallsScheduled`). Then it runs them:

- at most `cap` at once across every session: the Higgs lines minus one kept for renters calling in;
- a call that dials the phone network goes only to a number on the allowlist (hard rule 6), else it ends
  `blocked`, and only inside Toronto business hours, so a queued call waits for 9:00;
- each call has a deadline, and one that passes it ends `timeout`.

Each call ends once (`CallEnded`, keyed on the call) and its outcome follows (`OutcomeReady`). When every
call in the plan has an outcome, it emits `CallsCompleted`, and the renter is texted the results once.

A listing agent who calls back about a call that reached no one (A-13b, voice/callbacks.py) gets the same:
`callback()` makes it a new call in that session, for that listing and plan, with a deadline and one end.

Placing one call is a `CallRunner`'s job: the Caller over Twilio (A-05), the sim landlord (A-03), or a fake
in tests. With no runner, the plan is still frozen and its calls end `blocked` at once: nothing is made up,
and the session doesn't wait in `calling` for calls nobody will place.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection
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
from landline.voice.callbacks import Missed
from landline.voice.calls import mint
from landline.voice.sms import Texter

log = logging.getLogger(__name__)

TORONTO = ZoneInfo("America/Toronto")
OPENS, CLOSES = time(9, 0), time(20, 0)   # Toronto business hours for calling listing agents
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
SMS_MAX = 1500                            # Twilio joins long texts; keep the results well inside one message
SHARED_BY_DEFAULT = frozenset({"move_in", "occupants", "pets"})   # user_profiles.shareable's default


@dataclass(frozen=True)
class Dial:
    """One listing call, as the runner places it. Ids and the number come from code, never a model."""
    call_id: str
    session_id: str
    listing_id: str
    to: str | None          # an allowlisted number for a phone call; None off the phone network
    plan: CallPlan
    deadline: datetime
    callback: bool = False  # the listing agent called us back (A-13b): the Caller answers, it dials nothing
    twilio_sid: str | None = None   # a callback's own call, already on the line


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
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 translate: Callable[[str, str | None], Awaitable[str]] | None = None):
        self.runtime, self.settings, self.runner, self.texter = runtime, settings, runner, texter
        self.translate = translate   # the results into the renter's language (voice/translate.py)
        self.clock, self.sleep = clock, sleep
        self.cap = settings.call_concurrency or max(1, settings.higgs_max_sessions - 1)
        self._lines = asyncio.Semaphore(self.cap)
        self.allowlist = frozenset(n for n in (as_e164(p) for p in settings.demo_agent_phones) if n)
        # The allowlisted phones free to ring. A phone call takes one before it dials and gives it back when
        # it ends, however it ends, so a teammate already on a listing call is never rung again; with more
        # calls than phones, the rest wait for one to free up.
        self._phones: asyncio.Queue[str] = asyncio.Queue()
        for number in sorted(self.allowlist):
            self._phones.put_nowait(number)
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
        allowed, name = await self._shareable(state)
        plan = freeze(state, now, allowed=allowed, name=name)
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
            # Nothing can place these calls, and nothing picks queued calls up later, so they end now as
            # `blocked`: the plan completes, the session leaves `calling`, and the results say so.
            log.warning("session %s: no caller is configured; its %d calls end blocked", sid, len(call_ids))
            for cid, lid in zip(call_ids, plan.listing_ids, strict=True):
                await self._state(sid, cid, "blocked")
                await self._finish(plan, cid, lid, CallResult("blocked"))
            return
        for cid, lid in zip(call_ids, plan.listing_ids, strict=True):
            self._spawn(self._call(cid, lid, plan, runner))

    def runner_for(self, session_id: str) -> CallRunner | None:
        """The runner for one session's calls. A routing runner (voice/routing.py) picks by the session, so a
        judge's calls never reach the phone network; any other runner takes every session's calls."""
        pick = getattr(self.runner, "for_session", None)
        return pick(session_id) if pick is not None else self.runner

    # A listing agent calling back (A-13b)

    @property
    def takes_callbacks(self) -> bool:
        """Whether any runner here can answer a listing agent's call back: without one, an incoming call never
        waits on the lookup."""
        phone = getattr(self.runner, "phone", self.runner)   # ByChannel's phone runner, or the only one
        return bool(getattr(phone, "answers_callbacks", False))

    def callback_runner(self, session_id: str) -> CallRunner | None:
        """The runner that can answer a callback about this session's calls: one that took its calls over the
        phone and can pick up a call that's already on the line. None: the call goes to the Concierge."""
        runner = self.runner_for(session_id)
        return runner if runner is not None and getattr(runner, "answers_callbacks", False) else None

    async def callback(self, missed: Missed, phone: str | None, twilio_sid: str | None) -> str | None:
        """A listing agent calling back about `missed`, a call that reached no one (voice/callbacks.py).
        It's a new listing call, its id minted here, that joins the missed call's session for the same
        listing and plan (CallStateChanged with its listing_id), so its facts, booking and outcome land like
        any listing call's. The runner answers it with the Caller once Twilio opens the stream; like any
        listing call it has a deadline and ends once (_finish). Returns its call id, or None when it can't be
        taken here (no runner for it, or the session refuses it), and the call goes to the Concierge."""
        sid, lid = missed.session_id, missed.listing_id
        runner = self.callback_runner(sid)
        plan = self.runtime.state(sid).plan if runner is not None else None
        if runner is None or plan is None or lid not in plan.listing_ids:
            return None
        now, cid, to = self.clock(), mint("c_"), as_e164(phone) if phone else None
        dial = Dial(call_id=cid, session_id=sid, listing_id=lid, to=to, plan=plan, callback=True,
                    twilio_sid=twilio_sid or None,
                    deadline=now + timedelta(seconds=self.settings.listing_call_max_s))
        try:
            async with self.runtime.session_tx(sid) as tx:
                joined = await tx.apply(CallStateChanged(session_id=sid, at=now, idem_key=f"{cid}:joined",
                                                         call_id=cid, state="queued", listing_id=lid))
                db = self.runtime.db
                if joined and tx.conn is not None and db is not None:
                    await db.calls.create(cid, session_id=sid, role="listing", transport=runner.transport,
                                          state="queued", listing_id=lid, deadline_at=dial.deadline,
                                          to_e164=to, conn=tx.conn)
        except Refused as e:
            log.warning("callback about %s refused by session %s: %s", missed.call_id, sid, e)
            return None
        log.info("callback %s about missed call %s (%s, session %s)", cid, missed.call_id, lid, sid)
        held = self._hold_phone(to)   # their phone is busy with us: no listing call rings it meanwhile
        self._spawn(self._answer(dial, runner, held))
        await asyncio.sleep(0)   # place() holds the call (OutboundCalls) before Twilio opens its stream
        return cid

    async def _answer(self, dial: Dial, runner: CallRunner, held: str | None) -> None:
        """Runs the callback to its end. One that never got on the line (the stream never came) ends
        without an outcome: the card keeps what the missed call said, and the agent's next try is still a
        callback."""
        live = False

        async def report(state: CallState) -> None:
            nonlocal live
            live = live or state == "live"
            await self._state(dial.session_id, dial.call_id, state)

        try:
            async with asyncio.timeout(self.settings.listing_call_max_s):
                result = await runner.place(dial, report)
        except TimeoutError:
            result = CallResult("timeout")
        except Exception:
            log.exception("callback %s about %s failed", dial.call_id, dial.listing_id)
            result = CallResult("failed")
        finally:
            if held is not None:
                self._phones.put_nowait(held)
        await self._finish(dial.plan, dial.call_id, dial.listing_id, result, settle=live)

    def _hold_phone(self, number: str | None) -> str | None:
        """Takes an allowlisted phone out of the free ones while its owner is on a callback; returns it if it
        was free, so `_answer` gives it back. One already ringing for a listing call stays with that call."""
        if number not in self.allowlist:
            return None
        free = [self._phones.get_nowait() for _ in range(self._phones.qsize())]
        for n in free:
            if n != number:
                self._phones.put_nowait(n)
        return number if number in free else None

    # One call

    async def _call(self, call_id: str, listing_id: str, plan: CallPlan, runner: CallRunner) -> None:
        if runner.uses_phone and not self.allowlist:
            await self._state(plan.session_id, call_id, "blocked")
            await self._finish(plan, call_id, listing_id, CallResult("blocked"))
            return
        to = await self._phones.get() if runner.uses_phone else None   # waits for a free phone
        try:
            result = await self._place(call_id, listing_id, plan, runner, to)
        finally:
            if to is not None:
                self._phones.put_nowait(to)   # free again, whether the call was answered, failed or timed out
        await self._finish(plan, call_id, listing_id, result)

    async def _place(self, call_id: str, listing_id: str, plan: CallPlan, runner: CallRunner,
                     to: str | None) -> CallResult:
        if runner.uses_phone and to not in self.allowlist:   # hard rule 6, right before dialling
            await self._state(plan.session_id, call_id, "blocked")
            return CallResult("blocked")
        async with self._lines:
            if runner.uses_phone:
                await self._wait_for_hours()
            dial = Dial(call_id=call_id, session_id=plan.session_id, listing_id=listing_id, to=to, plan=plan,
                        deadline=self.clock() + timedelta(seconds=self.settings.listing_call_max_s))
            await self._state(plan.session_id, call_id, "dialing", to=to)   # calls.to_e164 (A-13a)

            async def report(state: CallState) -> None:
                await self._state(plan.session_id, call_id, state)

            try:
                async with asyncio.timeout(self.settings.listing_call_max_s):
                    return await runner.place(dial, report)
            except TimeoutError:
                return CallResult("timeout")
            except Exception:
                log.exception("call %s to %s failed", call_id, listing_id)
                return CallResult("failed")

    async def _wait_for_hours(self) -> None:
        """Hold a queued call until Toronto business hours, unless FORCE_BUSINESS_HOURS=1."""
        while not self.settings.force_business_hours:
            wait = until_open(self.clock())
            if wait <= 0:
                return
            await self.sleep(wait)

    async def _state(self, sid: str, call_id: str, state: CallState, to: str | None = None) -> None:
        await self._emit(CallStateChanged(session_id=sid, at=self.clock(), idem_key=f"{call_id}:{state}",
                                          call_id=call_id, state=state))
        if self.runtime.db is not None:
            await self.runtime.db.calls.set_state(call_id, state, to_e164=to, answered=state == "live")

    async def _finish(self, plan: CallPlan, call_id: str, listing_id: str, result: CallResult, *,
                      settle: bool = True) -> None:
        """End the call once, then record its outcome, then complete the plan if this was the last one.
        `settle=False`: end it only (a callback that never got on the line), leaving the outcome as it was."""
        sid, now, reason = plan.session_id, self.clock(), result.end_reason
        await self._emit(CallEnded(session_id=sid, at=now, idem_key=f"{call_id}:ended", call_id=call_id,
                                   reason=reason))
        if self.runtime.db is not None:
            await self.runtime.db.calls.finalize(call_id, reason)   # the first end wins
        if not settle:
            return
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
        results = view.results
        if self.translate is not None and state.plan is not None:
            results = await self.translate(results, state.plan.language)   # English when it can't
        self.texter.send_soon(state.caller_phone, sender, results_text(results, link))

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

    async def _shareable(self, state: SessionState) -> tuple[frozenset[str], str | None]:
        """What the renter allows the Caller to tell listing agents (user_profiles.shareable, ticked on /me),
        and their stored name. A renter with nothing stored, or no database (judge mode, tests), shares the
        default. If the lookup fails, nothing is shared: a privacy choice never falls back to sharing."""
        db = self.runtime.db
        if db is None:
            return SHARED_BY_DEFAULT, None
        try:
            if state.user_id is not None:
                user = await db.users.get(state.user_id)
            elif state.channel == "phone" and state.caller_phone:
                user = await db.users.by_phone(as_e164(state.caller_phone) or state.caller_phone)
            else:
                user = None
            if user is None:
                return SHARED_BY_DEFAULT, None
            profile = await db.profiles.get(user.user_id)
            return (frozenset(profile.shareable) if profile else SHARED_BY_DEFAULT), user.name
        except Exception:
            log.exception("session %s: what the renter shares couldn't be read; sharing nothing",
                          state.session_id)
            return frozenset(), None

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


def freeze(state: SessionState, now: datetime, allowed: Collection[str] = SHARED_BY_DEFAULT,
           name: str | None = None) -> CallPlan:
    """The CallPlan from what the brief recorded. Never edited afterwards. Only what the renter allowed
    (`allowed`, user_profiles.shareable) reaches the Caller: an unticked answer is left out, and their name
    only when they ticked it and it's known."""
    r, p = state.request, state.preferences
    assert r is not None
    windows = [w for a in r.availability if (w := window(a, now))]
    shareable = Shareable(name=name if "name" in allowed and name else None,
                          move_in=p.move_in if "move_in" in allowed else None,
                          occupants=p.occupants if "occupants" in allowed else None,
                          pets=p.pets if "pets" in allowed else [])
    return CallPlan(session_id=state.session_id, user_id=state.user_id, listing_ids=r.listing_ids[:5],
                    questions=r.questions, windows=windows, may_book=r.may_book, shareable=shareable,
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
