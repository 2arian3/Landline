"""Builds evals/reports/latest.md from evals/reports/concierge.json (E-01).

    uv run python -m evals.report        # rebuild the report without running anything

The JSON keeps, per set, the latest full run's summary and the one before it, so the report shows both
side by side. Extraction (E-02), ranking (E-03) and listing calls (E-04) add their sections when they exist.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from typing import Any

from evals.sim.score_concierge import Result
from landline.config import ROOT

REPORTS = ROOT / "evals" / "reports"
SUMMARY = REPORTS / "concierge.json"
LATEST = REPORTS / "latest.md"

CHECKS = [   # check family → what the report calls it
    ("prefs", "Preferences recorded"),
    ("reaction", "Reaction on the right listing"),
    ("focus", "Focused listing"),
    ("area", "Map moved to the area"),
    ("request", "A call request recorded"),
    ("request.listings", "Listings to call"),
    ("request.availability", "Viewing windows"),
    ("request.may_book", "Permission to book"),
    ("request.question", "Renter's extra questions"),
    ("may_book", "No booking allowed before the renter said so"),
    ("no_request", "No calls when none were asked for"),
    ("replies_in", "Replies in the renter's language"),
    ("ended", "Ended with end_session after the goodbye"),
    ("case", "Case errors (the fixture is wrong)"),
]
HYGIENE = [
    ("long_turns", "Turns over two sentences"),
    ("claims_without_tool", "Said it changed something, with no tool call"),
    ("ids_read", "Listing ids read out"),
    ("markdown", "List or markdown formatting"),
    ("not_caught", "Tool replies \"didn't catch that\" (bad arguments)"),
    ("which", "Tool replies \"which one?\" (ref didn't resolve)"),
    ("unknown_tool", "Unknown tool called"),
    ("broken", "Tool failed"),
    ("higgs_errors", "Higgs error events"),
]
SETS = [("holdout", "Holdout (quote these)"), ("tune", "Tune (prompts change against these)")]


def family(check_name: str) -> str:
    parts = check_name.split(".")[1:]   # drop "final" or "lineN"
    if not parts:
        return "?"
    if parts[0] == "request" and len(parts) > 1:
        return f"request.{parts[1]}"
    return parts[0]


def summarize(results: list[Result], meta: dict[str, Any]) -> dict[str, Any]:
    by_case: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        by_case[r.case_id].append(r)
    final = [c for r in results for c in r.checks if c.kind == "final"]
    on_time = [c for r in results for c in r.checks if c.kind == "on_time"]
    checks: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in final:
        checks[family(c.name)][0] += c.passed
        checks[family(c.name)][1] += 1
    hygiene: Counter[str] = Counter()
    for r in results:
        hygiene.update(r.hygiene)
    ms = sorted(m for r in results for m in r.ms)
    return {
        **meta,
        "cases": len(by_case), "run_count": len(results),
        "cases_all_runs": sum(all(r.passed for r in rs) for rs in by_case.values()),
        "run_pass_rate": _rate(sum(r.passed for r in results), len(results)),
        "on_time": [sum(c.passed for c in on_time), len(on_time)],
        "checks": dict(checks),
        "hygiene": dict(hygiene),
        "agent_turns": sum(r.agent_turns for r in results),
        "ms_p50": _pct(ms, 0.5), "ms_p90": _pct(ms, 0.9),
        "ends": dict(Counter(r.end for r in results)),
        "per_case": {cid: {"passed": sum(r.passed for r in rs), "runs": len(rs),
                           "failing": Counter(c.name for r in rs for c in r.failed()
                                              if c.kind == "final").most_common(3)}
                     for cid, rs in sorted(by_case.items())},
    }


def record(name: str, summary: dict[str, Any]) -> None:
    """Make `summary` the current numbers for set `name`, keeping the current ones as previous."""
    data = _load()
    data[name] = {"current": summary, "previous": (data.get(name) or {}).get("current")}
    REPORTS.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def write() -> str:
    text = render(_load())
    REPORTS.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(text, encoding="utf-8")
    return text


def render(data: dict[str, Any]) -> str:
    out = ["# Landline eval report", "",
           "Built by `make eval` (`evals/run_concierge.py`, then `evals/report.py`) from "
           "`evals/reports/concierge.json`. Every number in the submission comes from this file. Quote the "
           "holdout set: prompts are changed against the tune set only.", "",
           "## Concierge: simulated calls in text", "",
           "The real Concierge (its prompt and tools) on Higgs Realtime in text mode, talking to a scripted "
           "renter whose lines are fixed, so a failure is the Concierge's. Its tools run through the phone "
           "bridge's own `run_tool` on an in-memory session runtime over a frozen catalogue. A case passes "
           "when the session at the end is right: preferences, reactions, focus, which listings to call, the "
           "viewing windows, permission to book, and that it ended with `end_session`. Each case runs "
           "several times; a model never grades anything.", ""]
    if not any(name in data for name, _ in SETS):
        out += ["No eval has run yet. Run `make eval` with `BOSON_API_KEY` set.", ""]
    for name, title in SETS:
        if name in data:
            out += _set(title, data[name]["current"], data[name].get("previous"))
    out += ["## Not measured yet", "",
            "- Extraction (E-02) and ranking (E-03).",
            "- Listing calls against landlord personas (E-04): needs the Caller (A-05). It will use the same "
            "driver, with a second Higgs session playing the landlord.",
            "- Anything audio: time to first audio, barge-in, speech-to-text errors. Text mode can't see "
            "them; they need the audio sim transport (A-03).",
            "- Claims in languages other than English: the \"said it changed something\" check reads English "
            "only.", ""]
    return "\n".join(out)


def _set(title: str, cur: dict, prev: dict | None) -> list[str]:
    out = [f"### {title}", "",
           f"{cur['at']} · `{cur['sha']}` · {cur['cases']} cases × {cur['runs']} runs · {cur['mode']} · "
           f"{cur['catalogue']}", "",
           "| | This run | Previous |", "|---|---|---|"]

    def row(label: str, fn) -> None:
        out.append(f"| {label} | {fn(cur)} | {fn(prev) if prev else '—'} |")

    row("**Cases passing every run**", lambda s: f"**{s['cases_all_runs']} / {s['cases']}**")
    row("Runs passing", lambda s: _fmt_rate(s["run_pass_rate"]))
    row("Checks on time (in the turn they belong to)", lambda s: _ratio(*s["on_time"]))
    for key, label in CHECKS:
        if key in cur["checks"] or (prev and key in prev["checks"]):
            row(label, lambda s, k=key: _ratio(*s["checks"][k]) if k in s["checks"] else "—")
    for key, label in HYGIENE:
        if cur["hygiene"].get(key) or (prev and prev["hygiene"].get(key)):
            row(label, lambda s, k=key: f"{s['hygiene'].get(k, 0)} of {s['agent_turns']} turns"
                if k == "long_turns" else str(s["hygiene"].get(k, 0)))
    row("Agent turn, median / p90 (text, tools included)",
        lambda s: f"{_secs(s['ms_p50'])} / {_secs(s['ms_p90'])}")
    row("How runs ended", lambda s: ", ".join(f"{k} {v}" for k, v in sorted(s["ends"].items())))
    out += ["", "| Case | Runs passing | Failing most often |", "|---|---|---|"]
    for cid, c in cur["per_case"].items():
        failing = "; ".join(f"`{name}` ×{n}" for name, n in c["failing"]) or "—"
        out.append(f"| `{cid}` | {c['passed']}/{c['runs']} | {failing} |")
    out += ["", f"Traces: `{cur['traces']}` (not committed).", ""]
    return out


def _load() -> dict[str, Any]:
    if not SUMMARY.exists():
        return {}
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


def _rate(ok: int, n: int) -> float | None:
    return ok / n if n else None


def _ratio(ok: int, n: int) -> str:
    return f"{ok}/{n} ({ok / n:.0%})" if n else "—"


def _fmt_rate(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def _pct(values: list[int], q: float) -> int | None:
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def _secs(ms: int | None) -> str:
    return "—" if ms is None else f"{ms / 1000:.1f} s"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(write())
