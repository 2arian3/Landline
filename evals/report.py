"""Builds evals/reports/latest.md from evals/reports/concierge.json (E-01).

    uv run python -m evals.report        # rebuild the report without running anything

The JSON keeps, per set, the latest full run's summary and the one before it, so the report shows both
side by side. Extraction (E-02), ranking (E-03) and listing calls (E-04) add their sections when they exist.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evals.sim.score_concierge import Result
from landline.config import ROOT

REPORTS = ROOT / "evals" / "reports"
SUMMARY = REPORTS / "concierge.json"
CALLS = REPORTS / "calls.json"
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
    ("greeting.says", "Welcome back named what was remembered"),
    ("greeting.avoids", "Welcome back said nothing it didn't know"),
    ("ended", "Ended with end_session after the goodbye"),
    ("ended.goodbye", "Said goodbye before hanging up"),
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
    if parts[0] in ("request", "ended", "greeting") and len(parts) > 1:
        return f"{parts[0]}.{parts[1]}"
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


def record(name: str, summary: dict[str, Any], path: Path = SUMMARY) -> None:
    """Make `summary` the current numbers for set `name`, keeping the current ones as previous."""
    data = _load(path)
    data[name] = {"current": summary, "previous": (data.get(name) or {}).get("current")}
    REPORTS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def write() -> str:
    text = render(_load(), _load(CALLS))
    REPORTS.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(text, encoding="utf-8")
    return text


# ── listing calls (E-04) ────────────────────────────────────────────────────

CALL_CHECKS = [
    ("disclosed", "Said it's an AI assistant first"),
    ("status", "Card ends with the right status"),
    ("booked", "Booked the expected slot, or nothing"),
    ("booked.inside_windows", "Bookings inside the renter's windows"),
    ("red_flags", "Red flags exactly when the landlord's words deserve them"),
    ("note_question", "Noted the question it couldn't answer"),
    ("end", "Ended the right way"),
    ("fact", "Facts recorded with the right value"),
]


def call_family(check_name: str) -> str:
    parts = check_name.split(".")[1:]
    if parts[:2] == ["booked", "inside_windows"]:
        return "booked.inside_windows"
    return parts[0] if parts else "?"


def summarize_calls(results: list[Any], meta: dict[str, Any]) -> dict[str, Any]:
    by_case: dict[str, list[Any]] = defaultdict(list)
    for r in results:
        by_case[r.case_id].append(r)
    checks: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        for c in r.checks:
            checks[call_family(c.name)][0] += c.passed
            checks[call_family(c.name)][1] += 1
    cov = [sum(r.coverage[0] for r in results), sum(r.coverage[1] for r in results)]
    reads = [sum(r.readbacks[0] for r in results), sum(r.readbacks[1] for r in results)]
    saved = [sum(r.recorded_readbacks[0] for r in results), sum(r.recorded_readbacks[1] for r in results)]
    secs = sorted(r.seconds for r in results)
    turns = sorted(r.caller_turns for r in results)
    return {
        **meta, "cases": len(by_case), "run_count": len(results),
        "cases_all_runs": sum(all(r.passed for r in rs) for rs in by_case.values()),
        "run_pass_rate": _rate(sum(r.passed for r in results), len(results)),
        "checks": dict(checks), "coverage_items": cov, "coverage": _rate(*cov) or 0.0, "readbacks": reads,
        "recorded_readbacks": saved,
        "ungrounded": sum(r.ungrounded for r in results),
        "noted_by_code": sum(r.noted_by_code for r in results),
        "seconds_p50": secs[len(secs) // 2] if secs else None,
        "turns_p50": turns[len(turns) // 2] if turns else None,
        "ends": dict(Counter(r.end for r in results)),
        "per_case": {cid: {"passed": sum(r.passed for r in rs), "runs": len(rs),
                           "statuses": dict(Counter(r.status for r in rs)),
                           "failing": Counter(c.name for r in rs for c in r.failed()).most_common(3)}
                     for cid, rs in sorted(by_case.items())},
    }


def _calls(title: str, cur: dict, prev: dict | None) -> list[str]:
    out = [f"### {title}", "",
           f"{cur['at']} · `{cur['sha']}` · {cur['cases']} personas × {cur['runs']} runs · {cur['mode']}", "",
           "| | This run | Previous |", "|---|---|---|"]

    def row(label: str, fn) -> None:
        out.append(f"| {label} | {fn(cur)} | {fn(prev) if prev else '—'} |")

    row("**Personas passing every run**", lambda s: f"**{s['cases_all_runs']} / {s['cases']}**")
    row("Runs passing", lambda s: _fmt_rate(s["run_pass_rate"]))
    row("Checklist coverage", lambda s: _ratio(*s["coverage_items"]))
    row("Landlord turns with a number, read back next turn", lambda s: _ratio(*s["readbacks"]))
    row("Numbers the Caller saved, read back in that turn",
        lambda s: _ratio(*s["recorded_readbacks"]) if "recorded_readbacks" in s else "—")
    row("Quotes refused as not said (ungrounded)", lambda s: str(s["ungrounded"]))
    row("Questions code noted after the Caller promised to check", lambda s: str(s.get("noted_by_code", 0)))
    for key, label in CALL_CHECKS:
        if key in cur["checks"] or (prev and key in prev["checks"]):
            row(label, lambda s, k=key: _ratio(*s["checks"][k]) if k in s["checks"] else "—")
    row("Call, median: Caller turns / seconds to results",
        lambda s: f"{s['turns_p50']} / {s['seconds_p50']} s")
    row("How calls ended", lambda s: ", ".join(f"{k} {v}" for k, v in sorted(s["ends"].items())))
    out += ["", "| Persona | Runs passing | Card status | Failing most often |", "|---|---|---|---|"]
    for cid, c in cur["per_case"].items():
        failing = "; ".join(f"`{name}` ×{n}" for name, n in c["failing"]) or "—"
        statuses = ", ".join(f"{k} {v}" for k, v in sorted(c["statuses"].items()))
        out.append(f"| `{cid}` | {c['passed']}/{c['runs']} | {statuses} | {failing} |")
    out += ["", f"Traces: `{cur['traces']}` (not committed).", ""]
    return out


def render(data: dict[str, Any], calls: dict[str, Any] | None = None) -> str:
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
    out += ["## Listing calls: the Caller against landlord personas (text)", "",
            "One renter plan per run through the real Dispatcher: the Caller (its brief, prompt and tools) "
            "phones one listing through the sim runner, where a second Higgs session plays a landlord "
            "persona with hidden facts, viewing times and questions of its own. Scored in code from the "
            "session afterwards: the card's status, the booking and its window, red flags, the questions "
            "noted, and each expected fact's value. Personas in `holdout/` never tune the prompt.", ""]
    calls = calls or {}
    if not any(name in calls for name, _ in SETS):
        out += ["No listing call has been simulated yet. Run `make eval` with `BOSON_API_KEY` set.", ""]
    for name, title in SETS:
        if name in calls:
            out += _calls(title, calls[name]["current"], calls[name].get("previous"))
    out += extraction()
    out += ["## Not measured yet", "",
            "- Ranking (E-03).",
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


EXTRACTION = REPORTS / "extraction.json"
FIELD_NAMES = {"red_flags": "red flags", "viewing": "viewing time", "parking_cost": "parking price",
               "available_from": "move-in date", "utilities_included": "utilities included",
               "lease_months": "lease length"}


def extraction() -> list[str]:
    """The Auditor on call transcripts (E-02), from evals/reports/extraction.json."""
    out = ["## Auditor: facts from call transcripts", "",
           "The real Auditor (`server/landline/agents/auditor/`) on 20 written call transcripts with gold "
           "facts (`evals/run_extraction.py`): tricky fees, pets, a landlord who switches to Mandarin, two "
           "scams, read-backs where the agent's own words must not count, a no-answer and a voicemail. "
           "**Rules** is the path with no model: the Caller's live `record_fact`s, some of them wrong on "
           "purpose, checked against the landlord's turns, plus the red-flag rules and the booked viewing. "
           "**With extractor** adds the text LLM's facts, which go through the same quote check. "
           "**Ungrounded** is facts shown whose quote no landlord said, re-checked outside the Auditor; the "
           "bar is 0. Scored in code.", ""]
    if not EXTRACTION.exists():
        return out + ["Not run yet: `uv run python -m evals.run_extraction`.", ""]
    data = json.loads(EXTRACTION.read_text(encoding="utf-8"))
    for name, title in SETS:
        if name in data:
            out += _extraction_set(title, data[name]["current"], data[name].get("previous"))
    return out


def _extraction_set(title: str, cur: dict, prev: dict | None) -> list[str]:
    modes = [(m, label) for m, label in (("rules", "Rules"), ("extractor", "With extractor")) if cur.get(m)]
    out = [f"### {title}", "",
           f"{cur['at']} · `{cur['sha']}` · {cur['rules']['cases']} transcripts · "
           f"extractor: {cur['mode']}", "",
           "| | " + " | ".join(label for _, label in modes) + " | Previous (best mode) |",
           "|---|" + "---|" * (len(modes) + 1)]
    best = (prev or {}).get("extractor") or (prev or {}).get("rules")

    def row(label: str, fn) -> None:
        cells = [fn(cur[m]) for m, _ in modes]
        out.append(f"| {label} | " + " | ".join(cells) + f" | {fn(best) if best else '—'} |")

    row("**Ungrounded facts shown**", lambda s: f"**{s['ungrounded']} of {s['facts_shown']}**")
    row("Precision", lambda s: _fmt_rate(s["precision"]))
    row("Recall", lambda s: _fmt_rate(s["recall"]))
    row("Transcripts fully right", lambda s: f"{s['cases_exact']} / {s['cases']}")
    row("Claims refused (quote not a landlord's, or the words say otherwise)",
        lambda s: ", ".join(f"{k} {v}" for k, v in sorted(s["dropped"].items())) or "0")
    fields = sorted({f for m, _ in modes for f in cur[m]["fields"]})
    out += ["", "| Field | " + " | ".join(f"{label} P / R" for _, label in modes) + " |",
            "|---|" + "---|" * len(modes)]
    for f in fields:
        cells = []
        for m, _ in modes:
            x = cur[m]["fields"].get(f)
            cells.append("—" if not x else f"{_fmt_rate(x['precision'])} / {_fmt_rate(x['recall'])} "
                         f"({x['correct']}/{x['gold']})")
        out.append(f"| {FIELD_NAMES.get(f, f.replace('_', ' '))} | " + " | ".join(cells) + " |")
    out.append("")
    return out


def _load(path: Path = SUMMARY) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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
