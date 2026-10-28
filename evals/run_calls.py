"""Runs the simulated listing calls (E-04) and adds them to the report.

    uv run python -m evals.run_calls                          # tune and holdout personas, 3 runs each
    uv run python -m evals.run_calls --set tune --case raj-upsell --runs 1 --no-report

Each run is one renter plan through the real Dispatcher: a judge session records a one-listing plan with
the case's viewing windows, extra questions, booking permission and shareable answers, hangs up, and the
Dispatcher places the call through the sim runner, where the Caller talks to the case's landlord persona on
Higgs in text. The session's state afterwards (outcome, viewing, card status) and the call's trace are
scored in code. Live runs need BOSON_API_KEY and are skipped without it. Traces go to evals/runs/<stamp>/.
A full run of a set (no --case, no --no-report) replaces its numbers in evals/reports/calls.json, keeping
the run before as "previous", and rebuilds evals/reports/latest.md.

Each call uses two Higgs sessions; --concurrency (default 2) calls run at once.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime

from evals import report
from evals.run_concierge import CATALOGUE_PATH, RUNS_DIR, _sha
from evals.sim.calls import CALL_SETS, CallCase, load_calls
from evals.sim.score_calls import CallScore, score_call
from landline.config import ROOT, Settings
from landline.contracts import CallsRequested, PreferencesUpdated, SessionEnded, SessionStarted
from landline.contracts.tools import PreferenceDelta
from landline.db.catalogue import Catalogue, _from_fixture
from landline.orchestrator import SessionRuntime
from landline.voice.dispatch import Dispatcher
from landline.voice.gemini_live import session_connect
from landline.voice.sim import SimRunner

CALL_WAIT_S = 240


async def one_call(case: CallCase, n: int, catalogue: Catalogue, settings: Settings, out) -> CallScore:
    runtime = SessionRuntime(catalogue)
    persona = case.as_persona()
    runner = SimRunner(runtime, session_connect(settings), settings.voice_api_key() or "",
                       persona_for=lambda d, x: persona)
    Dispatcher(runtime, replace(settings, call_concurrency=1), runner=runner)
    sid, now, p = f"s_call_{case.id}_{n}", datetime.now(UTC), case.plan
    await runtime.start(SessionStarted(session_id=sid, at=now, idem_key=f"{sid}:started", channel="judge"))
    share = p.shareable
    delta = PreferenceDelta(move_in=share.move_in, occupants=share.occupants, pets=share.pets or None)
    await runtime.emit(PreferencesUpdated(session_id=sid, at=now, idem_key=f"{sid}:prefs", delta=delta))
    await runtime.emit(CallsRequested(session_id=sid, at=now, idem_key=f"{sid}:plan",
                                      listing_ids=[case.listing_id],
                                      extra_questions=p.questions, availability=p.availability,
                                      may_book=p.may_book))
    started = time.monotonic()
    await runtime.emit(SessionEnded(session_id=sid, at=now, idem_key=f"{sid}:ended", reason="hangup"))
    while runtime.state(sid).phase != "done" and time.monotonic() - started < CALL_WAIT_S:
        await asyncio.sleep(0.5)
    seconds = round(time.monotonic() - started, 1)
    state = runtime.state(sid)
    trace = next(iter(runner.traces.values()), None)
    await runtime.close()
    if trace is None or state.plan is None:
        raise RuntimeError(f"{case.id} run {n}: no call was placed (phase {state.phase})")
    result = score_call(case, n, trace, state, catalogue.by_id[case.listing_id], state.plan)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{case.id}-{n}.json").write_text(json.dumps({
        "result": result.to_json(), "seconds": seconds, "trace": asdict(trace),
        "outcome": (state.outcomes[case.listing_id].model_dump(mode="json")
                    if case.listing_id in state.outcomes else None),
        "results_text": state.results}, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    failed = ", ".join(c.name for c in result.failed())
    mark = "pass" if result.passed else "FAIL"
    print(f"  {case.id:20} run {n}  {mark}  {trace.end_reason:9} {result.status:9} "
          f"{seconds:5.1f}s  turns {result.caller_turns:2} repeats {result.repeats}  {failed}", flush=True)
    result.seconds = seconds
    return result


async def run(sets: list[str], runs: int, concurrency: int, only: set[str], keep_report: bool) -> int:
    settings = Settings.load()
    if not settings.voice_api_key():
        which = "GEMINI_API_KEY" if settings.voice_provider == "gemini" else "BOSON_API_KEY"
        print(f"{which} is not set: skipping the live listing calls.")
        report.write()
        return 0
    catalogue = Catalogue(_from_fixture(CATALOGUE_PATH), "eval")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_root = RUNS_DIR / f"{stamp}-calls"
    gate = asyncio.Semaphore(concurrency)

    async def gated(name: str, case: CallCase, n: int) -> CallScore:
        async with gate:
            return await one_call(case, n, catalogue, settings, out_root / name)

    results: dict[str, list[CallScore]] = {}
    for name in sets:
        cases = [c for c in load_calls(name) if not only or c.id in only]
        where = out_root.relative_to(ROOT).as_posix()
        print(f"{name}: {len(cases)} personas x {runs} runs -> {where}/{name}")
        results[name] = list(await asyncio.gather(*(gated(name, c, n) for c in cases
                                                    for n in range(1, runs + 1))))
    meta = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "sha": _sha(), "runs": runs,
            "mode": "live text", "traces": out_root.relative_to(ROOT).as_posix()}
    for name, res in results.items():
        if not res:
            continue
        summary = report.summarize_calls(res, meta)
        print(f"\n{name}: {summary['cases_all_runs']}/{summary['cases']} personas pass every run, "
              f"{summary['run_pass_rate']:.0%} of runs pass, coverage {summary['coverage']:.0%}")
        if not only and not keep_report:
            report.record(name, summary, path=report.CALLS)
    if not keep_report:
        report.write()
        print(f"\nReport: {report.LATEST.relative_to(ROOT).as_posix()}")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--set", action="append", choices=sorted(CALL_SETS), dest="sets")
    parser.add_argument("--case", action="append", default=[], help="only this persona case (repeatable)")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2, help="calls at once (two Higgs sessions each)")
    parser.add_argument("--no-report", action="store_true", help="leave evals/reports alone (for tuning)")
    args = parser.parse_args()
    return asyncio.run(run(args.sets or ["tune", "holdout"], args.runs, args.concurrency, set(args.case),
                           args.no_report))


if __name__ == "__main__":
    raise SystemExit(main())
