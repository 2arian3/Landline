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
    out += ranking()
    out += voice()
    out += ["## Not measured yet", "",
            "- Speech-to-text errors on real voices: the audio sim speaks synthesized clips.",
            "- Claims in languages other than English: the \"said it changed something\" check reads English "
            "only.", ""]
    return "\n".join(out)


AUDIO = REPORTS / "audio.json"


def voice() -> list[str]:
    """The audio sim (E-04): the real voice paths on Higgs audio, from evals/reports/audio.json."""
    out = ["## Voice: real audio on Higgs Realtime", "",
           "The real voice paths, unchanged (`evals/run_audio.py`): the Concierge through the bridge on "
           "24 kHz PCM (the browser), the Caller through the phone runner on μ-law (Twilio's format). "
           "The other side streams speech in real time with silence between lines, as a phone line does. "
           "Its lines are clips synthesized with Higgs TTS (`higgs-tts-v3`), not recordings of people. "
           "Time to first audio runs from the end of the other side's voice to the agent's first audio "
           "chunk (what a caller waits through, including the VAD's end-of-speech wait), "
           "and from Higgs' `speech_stopped` to it. "
           "A barge-in talks over a reply 1.2 s in. It counts when Higgs hears it within 2 s, the audio is "
           "cleared, and the next reply picks up what was said (its words or its tool arguments).", ""]
    if not AUDIO.exists():
        return out + ["Not run yet: `uv run python -m evals.run_audio` with `BOSON_API_KEY` set.", ""]
    data = json.loads(AUDIO.read_text(encoding="utf-8"))
    cur, prev = data["current"], data.get("previous")

    def ms(s: dict) -> str:
        return "—" if not s or s.get("p50") is None else f"{s['p50']:,} / {s['p90']:,} ms (n={s['n']})"

    out += [f"{cur['at']} · `{cur['sha']}` · {cur['runs']} sessions · clips {cur['clips']}", "",
            "| | p50 / p90 | Previous p50 / p90 |", "|---|---|---|"]

    def row(label: str, fn) -> None:
        out.append(f"| {label} | {fn(cur)} | {fn(prev) if prev else '—'} |")

    row("Concierge greeting: `response.create` → first audio", lambda s: ms(s["greeting_ms"]))
    row("**Concierge (browser): end of the renter's voice → first audio**",
        lambda s: ms(s["concierge"]["from_speech_end"]))
    row("Concierge: `speech_stopped` → first audio", lambda s: ms(s["concierge"]["from_speech_stopped"]))
    row("**Caller (phone): end of the landlord's voice → first audio**",
        lambda s: ms(s["caller"]["from_speech_end"]))
    row("Caller: `speech_stopped` → first audio", lambda s: ms(s["caller"]["from_speech_stopped"]))
    b = cur["barge_in"]
    out += ["", "| Barge-in | This run |", "|---|---|",
            f"| **Recovered** (heard, cleared, next reply answers it) | **{b['ok']} / {b['tries']}** |",
            f"| Voice onset → `speech_started` | {ms(b['detected'])} |",
            f"| Voice onset → audio cleared at the caller | {ms(b['cleared'])} |",
            f"| Next reply picked up the interruption | {b['answered']} / {b['tries']} |",
            f"| Interrupted reply's response cancelled by the server | {b['cancelled']} / {b['tries']} |",
            f"| `conversation.item.truncate` sent by our code | {b['truncated_by_us']} / {b['tries']} |",
            f"| Agent audio played before the cut | {ms(b['played_ms'])} |", ""]
    t = cur.get("barge_in_with_truncate")
    if t and t["tries"]:
        out += ["With `conversation.item.truncate` sent on `speech_started` (tried in the harness only; the "
                "bridge doesn't send it yet):", "",
                "| | This run |", "|---|---|",
                f"| Recovered | {t['ok']} / {t['tries']} |",
                f"| Truncates sent / acknowledged by Higgs (`conversation.item.truncated`) | {t['sent']} / "
                f"{t['acked']} |",
                f"| Higgs' `audio_end_ms` equal to what had played | {t['matched']} / {t['sent']} |",
                f"| Errors | {', '.join(t['errors']) or 'none'} |", ""]
    if cur["calls"]:
        out += ["| Caller call on audio | Length | Caller turns | Ended | Facts | Booked | Red flags |",
                "|---|---|---|---|---|---|---|"]
        for c in cur["calls"]:
            out.append(f"| `{c['persona']}` | {c['seconds']:.0f} s | {c['caller_turns']} | {c['end']} | "
                       f"{c['facts']} | {'yes' if c['booked'] else 'no'} | {c['red_flags']} |")
        out.append("")
    if b["failures"]:
        out += ["Barge-ins that didn't recover: " + "; ".join(
            f"`{f['clip']}` ({', '.join(f['why'])}: {f['answer']!r})" for f in b["failures"]), ""]
    out += [f"Timelines: `{cur['traces']}` (not committed).", ""]
    return out


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


# ── ranking (E-03) ──────────────────────────────────────────────────────────

RANKING = REPORTS / "ranking.json"


def ranking() -> list[str]:
    """The Matcher on renter briefs (E-03), from evals/reports/ranking.json."""
    out = ["## Matcher: ranking renter briefs", "",
           "The real ranking (`server/landline/scoring/`, with feedback from `orchestrator/feedback.py`) "
           "on 10 renter briefs over the frozen catalogue (`evals/run_ranking.py`), through the session "
           "runtime. Each brief's acceptable listings are labelled from criteria written in its fixture, not "
           "from the ranker. **hit@3**: one of them is in the top 3. **Turns to converge**: the renter keeps "
           "disliking the top card for a reason the brief didn't give (\"too small\") until a listing with "
           "what they want reaches the top 3, at most 5 turns. Pure code: the same numbers every run, "
           "no key.",
           ""]
    if not RANKING.exists():
        return out + ["Not run yet: `uv run python -m evals.run_ranking`.", ""]
    data = json.loads(RANKING.read_text(encoding="utf-8"))
    for name, title in SETS:
        if name in data:
            out += _ranking_set(title, data[name]["current"], data[name].get("previous"))
    checks = (data.get("checks") or {}).get("current")
    if checks:
        lat = checks["latency"]
        out += ["### Checks", "",
                f"- Light twins (a brighter copy never loses): {_fails(checks['light_twins'])}",
                f"- \"Too dark\" on a dark top card (a small catalogue where light varies): "
                f"{_fails(checks['too_dark'])}",
                f"- Ranking time, warm: p50 {lat['p50_ms']} ms, p95 {lat['p95_ms']} ms on {lat['listings']} "
                f"listings (cold {lat['cold_ms']} ms); p50 {lat['big_p50_ms']} ms, "
                f"p95 {lat['big_p95_ms']} ms on {lat['big_listings']}. Target < 5 ms.", ""]
    return out


def _ranking_set(title: str, cur: dict, prev: dict | None) -> list[str]:
    def turns(s: dict) -> str:
        mean = "—" if s["turns_mean"] is None else f"{s['turns_mean']} mean, {s['turns_max']} max"
        return f"{s['converged']} / {s['cases']} ({mean})"

    rows = [("**hit@3**", lambda s: _fmt_rate(s["hit_at_3"])),
            ("Precision@3", lambda s: _fmt_rate(s["precision_at_3"])),
            ("Converged after feedback", turns),
            ("Monotonicity violations", lambda s: str(len(s["monotonicity"]))),
            ("Stability violations", lambda s: str(len(s["stability"])))]
    out = [f"### {title}", "",
           f"{cur['at']} · `{cur['sha']}` · {cur['cases']} briefs · {cur['catalogue']}", "",
           "| | Now | Previous |", "|---|---|---|"]
    out += [f"| {label} | {fn(cur)} | {fn(prev) if prev else '—'} |" for label, fn in rows]
    out += ["", "| Brief | hit@3 | Precision@3 | Turns (reason) |", "|---|---|---|---|"]
    for c in cur["per_case"]:
        t = "not converged" if c["turns"] is None else str(c["turns"])
        hit = "yes" if c["hit"] else "no"
        out.append(f"| {c['id']} | {hit} | {c['precision']:.2f} | {t} ({c['reason']}) |")
    if cur["monotonicity"] or cur["stability"]:
        out += ["", "Violations:", ""] + [f"- {v}" for v in cur["monotonicity"] + cur["stability"]]
    out.append("")
    return out


def _fails(violations: list[str]) -> str:
    return "pass" if not violations else "**fail**: " + "; ".join(violations)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(write())
