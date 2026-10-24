"""Runs the simulated Concierge calls and rebuilds the report (E-01, `make eval`).

    uv run python -m evals.run_concierge                     # tune and holdout, 3 runs each, live Higgs
    uv run python -m evals.run_concierge --set tune --case farsi-switch --runs 1
    uv run python -m evals.run_concierge --replay            # the recordings only: no key, no credits

Live runs need BOSON_API_KEY and are skipped without it. Every run's trace goes to evals/runs/<stamp>/ (not
committed). A full run of a set (no --case) also replaces that set's numbers in evals/reports/concierge.json,
keeping the run before as "previous", and rebuilds evals/reports/latest.md. Tune prompts on `tune` with
--no-report, so the report compares a change against the run before it, not against the last attempt;
the write-up quotes `holdout`.

Sessions run a few at a time (--concurrency, default 3). If this key is the one judge mode uses, they
share its concurrency limit, so don't run it while someone is on /try.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime

from evals import report
from evals.sim import replay
from evals.sim.cases import FIXTURES, SETS, Case, load_set
from evals.sim.driver import ScriptedRenter, simulate
from evals.sim.score_concierge import Result, score
from landline.config import ROOT, Settings
from landline.db.catalogue import Catalogue, _from_fixture
from landline.voice import higgs

RUNS_DIR = ROOT / "evals" / "runs"
CATALOGUE_PATH = FIXTURES / "listings.json"   # frozen, so a new scrape can't move the boards


async def run(sets: list[str], runs: int, concurrency: int, only: set[str], use_replay: bool,
              keep_report: bool = False) -> int:
    settings = Settings.load()
    if not use_replay and not settings.boson_api_key:
        print("BOSON_API_KEY is not set: skipping the live Concierge runs.")
        report.write()
        return 0
    catalogue = Catalogue(_from_fixture(CATALOGUE_PATH), "eval")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RUNS_DIR / f"{stamp}{'-replay' if use_replay else ''}"
    gate = asyncio.Semaphore(concurrency)

    async def one(name: str, case: Case, n: int) -> Result:
        if use_replay:
            fake = replay.ReplayHiggs(replay.load(FIXTURES / "recordings" / f"{case.id}.jsonl"))
            connect, key = fake.connect, ""
        else:
            connect, key = higgs.connect, settings.boson_api_key or ""
        async with gate:
            trace = await simulate(case.id, n, ScriptedRenter([line.say for line in case.lines]),
                                   catalogue=catalogue, connect_higgs=connect, api_key=key)
        result = score(case, trace, catalogue)
        folder = out / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{case.id}-{n}.json").write_text(json.dumps(
            {"result": result.to_json(), "trace": trace.to_json()}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        replay.save(folder / f"{case.id}-{n}.jsonl", trace.events)
        mark = "pass" if result.passed else "FAIL"
        failed = ", ".join(sorted({c.name for c in result.failed() if c.kind == "final"}))
        print(f"  {name:8} {case.id:34} run {n}  {mark}  {trace.end:12} {failed}", flush=True)
        return result

    recorded = {p.stem for p in (FIXTURES / "recordings").glob("*.jsonl")}
    jobs: dict[str, list] = {}
    for name in sets:
        cases = [c for c in load_set(name) if (not only or c.id in only)
                 and (not use_replay or c.id in recorded)]
        jobs[name] = [(case, n) for case in cases for n in range(1, (1 if use_replay else runs) + 1)]
    total = sum(len(j) for j in jobs.values())
    where = out.relative_to(ROOT).as_posix()
    print(f"{'Replaying' if use_replay else 'Running'} {total} conversations -> {where}")

    results = {name: await asyncio.gather(*(one(name, c, n) for c, n in todo)) for name, todo in jobs.items()}
    meta = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "sha": _sha(), "runs": runs,
            "mode": "replay" if use_replay else "live text", "catalogue": f"{CATALOGUE_PATH.name} "
            f"({len(catalogue.listings)} listings)", "traces": where}
    for name, res in results.items():
        if not res:
            continue
        summary = report.summarize(list(res), meta)
        print(f"\n{name}: {summary['cases_all_runs']}/{summary['cases']} cases pass every run, "
              f"{summary['run_pass_rate']:.0%} of runs pass")
        if not only and not use_replay and not keep_report:
            report.record(name, summary)
    if not use_replay and not keep_report:
        report.write()
        print(f"\nReport: {report.LATEST.relative_to(ROOT).as_posix()}")
    return 0


def _sha() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=ROOT, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True,
                               cwd=ROOT).stdout.strip()
        return f"{sha}{'+dirty' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # Farsi and Mandarin lines on a Windows console
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--set", action="append", choices=sorted(SETS), dest="sets")
    parser.add_argument("--case", action="append", default=[], help="only this case id (repeatable)")
    parser.add_argument("--runs", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument("--concurrency", type=int, default=3, help="Higgs sessions at once (default 3)")
    parser.add_argument("--replay", action="store_true", help="replay evals/fixtures/recordings instead")
    parser.add_argument("--no-report", action="store_true", help="leave evals/reports alone (for tuning)")
    args = parser.parse_args()
    return asyncio.run(run(args.sets or ["tune", "holdout"], args.runs, args.concurrency, set(args.case),
                           args.replay, args.no_report))


if __name__ == "__main__":
    raise SystemExit(main())
