"""Sends a scripted (mock) conversation through a listing call and prints what came of it.

    uv run python -m evals.run_mock_call                 # every case in evals/fixtures/mock_calls/
    uv run python -m evals.run_mock_call --case linda-scripted
    uv run python -m evals.run_mock_call --file my-call.json --runs 3

The landlord's side is the case's lines (format in evals/sim/scripted_call.py). Everything else is the real
outbound path, minus the phone: a judge session records the case's one-listing plan and hangs up, the
Dispatcher places the call through the sim runner (voice/sim.py), the real Caller answers on Higgs in text
with its tools, and the Auditor writes the outcome. It prints the conversation with each tool call and its
reply, the outcome, the card's status and the results text. When the case has `gold`, it also runs the call
eval's checks and exits 1 if any fail.

Needs BOSON_API_KEY, because the Caller is a Higgs session. Nothing dials, and evals/reports is left alone.
Traces go to evals/runs/<stamp>-mock/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from evals.run_concierge import CATALOGUE_PATH, RUNS_DIR
from evals.sim.score_calls import CallScore, score_call
from evals.sim.scripted_call import MockCall, load_mock_call, load_mock_calls, with_script
from landline.agents.caller import slots
from landline.config import ROOT, Settings
from landline.contracts import CallsRequested, PreferencesUpdated, SessionEnded, SessionStarted
from landline.contracts.tools import PreferenceDelta
from landline.db.catalogue import Catalogue, _from_fixture
from landline.orchestrator import SessionRuntime, SessionState
from landline.voice import higgs
from landline.voice.dispatch import Dispatcher
from landline.voice.higgs import ConnectHiggs
from landline.voice.sim import SimRunner, SimTrace

CALL_WAIT_S = 240


@dataclass
class MockRun:
    case: MockCall
    n: int
    state: SessionState
    trace: SimTrace
    seconds: float
    score: CallScore | None = None


async def run_mock(case: MockCall, n: int, catalogue: Catalogue, settings: Settings,
                   connect: ConnectHiggs = higgs.connect, wait_s: float = CALL_WAIT_S) -> MockRun:
    """One call of `case`, from the renter's hang-up to the plan's results. `connect` is the Caller's
    Higgs (tests pass a fake one); the landlord is always the script."""
    runtime = SessionRuntime(catalogue)
    persona = case.persona()
    runner = SimRunner(runtime, with_script(case.script(), connect), settings.boson_api_key or "",
                       persona_for=lambda dial, listing: persona)
    Dispatcher(runtime, replace(settings, call_concurrency=1), runner=runner)
    sid, now, p = f"s_mock_{case.id}_{n}", datetime.now(UTC), case.plan
    await runtime.start(SessionStarted(session_id=sid, at=now, idem_key=f"{sid}:started", channel="judge"))
    share = p.shareable
    delta = PreferenceDelta(move_in=share.move_in, occupants=share.occupants, pets=share.pets or None)
    await runtime.emit(PreferencesUpdated(session_id=sid, at=now, idem_key=f"{sid}:prefs", delta=delta))
    await runtime.emit(CallsRequested(session_id=sid, at=now, idem_key=f"{sid}:plan",
                                      listing_ids=[case.listing_id], extra_questions=p.questions,
                                      availability=p.availability, may_book=p.may_book))
    started = time.monotonic()
    await runtime.emit(SessionEnded(session_id=sid, at=now, idem_key=f"{sid}:ended", reason="hangup"))
    while runtime.state(sid).phase != "done" and time.monotonic() - started < wait_s:
        await asyncio.sleep(0.2)
    seconds = round(time.monotonic() - started, 1)
    state = runtime.state(sid)
    trace = next(iter(runner.traces.values()), None)
    await runtime.close()
    if trace is None or state.plan is None:
        raise RuntimeError(f"{case.id} run {n}: no call was placed (phase {state.phase})")
    result = MockRun(case, n, state, trace, seconds)
    if case.gold is not None:
        result.score = score_call(case.as_case(), n, trace, state, catalogue.by_id[case.listing_id],
                                  state.plan)
    return result


def show(r: MockRun) -> None:
    lid, name = r.case.listing_id, r.case.name
    print(f"\n{r.case.id} run {r.n}: ended {r.trace.end_reason} after {r.seconds} s, "
          f"card {r.state.status(lid)}")
    for i, turn in enumerate(r.trace.turns):
        who = name if turn.speaker == "landlord" else "Caller"
        print(f"  {who:>8}  {turn.text}")
        for c in (c for c in r.trace.tool_calls if c["turn"] == i):
            print(f"  {'':>8}    -> {c['name']} {c['arguments']}")
            print(f"  {'':>8}       {c['output'][:200]}")
    show_outcome(r.state, lid)
    if r.score is not None:
        failed = r.score.failed()
        print(f"  Checks: {'pass' if r.score.passed else 'FAIL'}")
        for c in failed:
            print(f"    {c.name}: {c.detail}")


def show_outcome(state: SessionState, lid: str) -> None:
    """What the call left on the renter's session: the Auditor's outcome, the viewing, the results text."""
    out = state.outcomes.get(lid)
    print("  Outcome")
    if out is None:
        print("    none")
    else:
        for f in out.facts:
            print(f"    fact      {f.field} = {f.value}   (\"{f.quote}\")")
        for c in out.conflicts:
            print(f"    conflict  {c}")
        print(f"    red flags {', '.join(out.red_flags) or 'none'}")
        for q in out.open_questions:
            print(f"    question  {q}")
    viewing = state.viewings.get(lid)
    when = f"{viewing.starts_at.astimezone(slots.TORONTO):%a %d %b %H:%M} Toronto" if viewing else ""
    print(f"    viewing   {f'{when} ({viewing.slot_text})' if viewing else 'none'}")
    print(f"  Results text\n    {state.results or 'none'}")


def save(r: MockRun, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    lid = r.case.listing_id
    (out / f"{r.case.id}-{r.n}.json").write_text(json.dumps({
        "seconds": r.seconds, "status": r.state.status(lid),
        "score": r.score.to_json() if r.score else None, "trace": asdict(r.trace),
        "outcome": r.state.outcomes[lid].model_dump(mode="json") if lid in r.state.outcomes else None,
        "results_text": r.state.results}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


async def run(cases: list[MockCall], runs: int) -> int:
    settings = Settings.load()
    if not settings.boson_api_key:
        print("BOSON_API_KEY is not set: the Caller needs Higgs. Add it to .env.", file=sys.stderr)
        return 1
    catalogue = Catalogue(_from_fixture(CATALOGUE_PATH), "eval")
    if missing := [c.id for c in cases if c.listing_id not in catalogue.by_id]:
        print(f"listing not in {CATALOGUE_PATH.name}: {', '.join(missing)}", file=sys.stderr)
        return 1
    out = RUNS_DIR / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-mock"
    failed = 0
    for case in cases:
        for n in range(1, runs + 1):
            r = await run_mock(case, n, catalogue, settings)
            show(r)
            save(r, out)
            failed += bool(r.score and not r.score.passed)
    print(f"\nTraces: {out.relative_to(ROOT).as_posix()}")
    return 1 if failed else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--case", action="append", default=[], help="a case in evals/fixtures/mock_calls/")
    parser.add_argument("--file", type=Path, action="append", default=[], help="a mock call JSON anywhere")
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    cases = [load_mock_call(p) for p in args.file]
    if args.case or not args.file:
        known = {c.id: c for c in load_mock_calls()}
        if unknown := [c for c in args.case if c not in known]:
            parser.error(f"no mock call {', '.join(unknown)}; have {', '.join(sorted(known))}")
        cases += [known[c] for c in args.case] if args.case else list(known.values())
    return asyncio.run(run(cases, args.runs))


if __name__ == "__main__":
    raise SystemExit(main())
