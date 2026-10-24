"""Scores one simulated listing call (E-04) in code. No model grades anything.

A call passes when every `final` check holds: the Caller said it is an AI assistant first, the card ends
with the right status, the booking is the expected one (or there is none) and inside the renter's windows,
red flags appear exactly when the landlord's words deserve them, a question the brief can't answer was
noted, the call ended the right way, and each expected fact was recorded with the right value. Coverage of
the checklist, read-backs of the landlord's numbers, refused (ungrounded) quotes and call length are
reported as rates.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

from evals.sim.calls import CallCase
from evals.sim.score_concierge import Check
from landline.agents.caller import slots
from landline.agents.caller.brief import brief
from landline.contracts import CallPlan, Listing
from landline.orchestrator import SessionState
from landline.voice.sim import SimTrace

GROUPS = {"available": {"available", "available_from"}, "available_from": {"available", "available_from"},
          "parking": {"parking", "parking_cost"}, "parking_cost": {"parking", "parking_cost"}}
UNITS = {w: n for n, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * n for n, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split())
        if w != "_"}


@dataclass
class CallScore:
    case_id: str
    run: int
    passed: bool
    end: str
    status: str
    checks: list[Check] = field(default_factory=list)
    coverage: list[int] = field(default_factory=lambda: [0, 0])    # checklist items done, items
    readbacks: list[int] = field(default_factory=lambda: [0, 0])   # read back, landlord turns with a number
    recorded_readbacks: list[int] = field(default_factory=lambda: [0, 0])   # of numbers the Caller saved
    ungrounded: int = 0
    noted_by_code: int = 0
    caller_turns: int = 0
    repeats: int = 0                                               # Caller questions asked again
    tools: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0                                           # hang-up to the plan's results

    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def to_json(self) -> dict:
        return asdict(self)


def score_call(case: CallCase, run: int, trace: SimTrace, state: SessionState, listing: Listing,
               plan: CallPlan) -> CallScore:
    g, lid = case.gold, case.listing_id
    out = state.outcomes.get(lid)
    checks: list[Check] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(f"final.{name}", "final", ok, "" if ok else detail))

    callers = [t for t in trace.turns if t.speaker == "caller"]
    first = callers[0].text if callers else ""
    check("disclosed", "ai assistant" in first.lower(), f"first words: {first[:80]!r}")
    status = state.status(lid)
    check("status", status == g.status, f"want {g.status}, got {status}")
    viewing = state.viewings.get(lid)
    if g.booked:   # "saturday 11:00" must be that slot; "saturday" is any time that day in the windows
        want = _resolve(g.booked)
        got = _slot(viewing.starts_at) if viewing else None
        ok = got == want if " " in want else bool(got) and got.split()[0] == want
        check("booked", ok, f"want {want}, got {got}")
    else:
        check("booked.none", viewing is None, f"booked {viewing.slot_text if viewing else ''}")
    if viewing is not None:
        check("booked.inside_windows", slots.within(viewing.starts_at, plan.windows),
              f"{viewing.starts_at} outside {plan.windows}")
    flags = list(out.red_flags) if out else []
    check("red_flags", bool(flags) == g.red_flags, f"want {g.red_flags}, got {flags}")
    if g.note_question:   # what matters to the renter: the landlord's question comes back in the results
        asked = list(out.open_questions) if out else []
        check("note_question", bool(asked), "no question came back")
    check("end", trace.end_reason in g.end, f"want {g.end}, got {trace.end_reason}")
    for words in g.results_avoid:
        check(f"results_avoid.{words}", words.lower() not in (state.results or "").lower(),
              f"results said {state.results!r}")
    b = brief(listing, plan)
    asked = {f for item in b.items for f in item.fields}
    facts = list(out.facts) if out else []
    for name, words in g.facts.items():
        if name not in asked:
            continue
        words = [_resolve(w) for w in words]
        group = GROUPS.get(name, {name})
        # The Auditor's value is a fixed form ("laundry in the unit") read from the landlord's quote, so the
        # landlord's own words ("in-suite") count too.
        values = " / ".join(f"{f.value} {f.quote}".lower() for f in facts if f.field in group)
        said = numbers(values)   # an amount counts in digits or words: "100" ≈ "one hundred"
        check(f"fact.{name}", bool(values) and all(w.lower() in values or (w.isdigit() and int(w) in said)
                                                   for w in words),
              f"want {words}, got {values!r}")
    for c in trace.tool_calls:   # replay what the Caller saved onto a fresh checklist
        if c["name"] == "record_fact" and c["output"].startswith("Recorded"):
            args = _args(c["arguments"])
            b.covered(str(args.get("field", "")), str(args.get("value", "")))
        elif c["name"] == "book_viewing" and c["output"].startswith("Booked"):
            for item in b.items:
                if item.key == "viewing":
                    item.done = True
    tools: dict[str, int] = {}
    for c in trace.tool_calls:
        tools[c["name"]] = tools.get(c["name"], 0) + 1
    # Coverage counts only calls meant to cover the checklist: a scam or a voicemail ends early on purpose.
    full = not g.red_flags and g.status in ("booked", "verified")
    coverage = [len(b.items) - len(b.missing()), len(b.items)] if full else [0, 0]
    return CallScore(case_id=case.id, run=run, passed=all(c.passed for c in checks), end=trace.end_reason,
                     status=status, checks=checks, coverage=coverage,
                     readbacks=readbacks(trace), recorded_readbacks=recorded_readbacks(trace),
                     ungrounded=trace.ungrounded,
                     noted_by_code=trace.noted_by_code,
                     caller_turns=len(callers), repeats=repeats(trace),
                     tools=tools)


def repeats(trace: SimTrace) -> int:
    """Caller questions asked again: a question sentence nearly the same as one the Caller already asked.
    The prompt says never to ask the same thing twice; on production a Caller asked "Are pets allowed?" five
    times in one call."""
    asked: list[str] = []
    n = 0
    for t in trace.turns:
        if t.speaker != "caller":
            continue
        for q in re.findall(r"[^.!?]*\?", t.text):
            q = " ".join(q.lower().split())
            if not q:
                continue
            if any(SequenceMatcher(None, q, a).ratio() > 0.85 for a in asked):
                n += 1
            asked.append(q)
    return n


def _resolve(words: str) -> str:
    """`{tomorrow_date}` ("october 25", as the Auditor writes a date) and `{tomorrow_day}` ("sunday") in a
    gold value: the day after today in Toronto."""
    tomorrow = datetime.now(UTC).astimezone(slots.TORONTO).date() + timedelta(days=1)
    return words.replace("{tomorrow_date}", f"{tomorrow:%B} {tomorrow.day}".lower()).replace(
        "{tomorrow_day}", slots.WEEKDAYS[tomorrow.weekday()])


def readbacks(trace: SimTrace) -> list[int]:
    """[read back, landlord turns that said a number of 5 or more]: did the Caller's next turn repeat it?"""
    ok = n = 0
    turns = trace.turns
    for i, t in enumerate(turns):
        if t.speaker != "landlord":
            continue
        said = {x for x in numbers(t.text) if x >= 5}
        reply = next((u for u in turns[i + 1:] if u.speaker == "caller"), None)
        if not said or reply is None:
            continue
        n += 1
        ok += bool(said & numbers(reply.text))
    return [ok, n]


def recorded_readbacks(trace: SimTrace) -> list[int]:
    """[read back, Caller turns that saved a number of 5 or more]: did the words of the turn in which the
    Caller recorded a fact or booked a viewing repeat that number? Unlike `readbacks`, numbers in the
    landlord's own questions ("want a locker for $60?") and goodbyes ("see you Saturday at 11") don't count,
    since there is nothing to save in them."""
    ok = n = 0
    for i, t in enumerate(trace.turns):
        if t.speaker != "caller":
            continue
        saved: set[int] = set()
        for c in trace.tool_calls:
            if c.get("turn") != i or not c["output"].startswith(("Recorded", "Booked", "Not booked")):
                continue
            args = _args(c["arguments"])
            saved |= {x for x in numbers(" ".join(str(v) for v in args.values())) if x >= 5}
        if not saved:
            continue
        n += 1
        ok += bool(saved & numbers(t.text))
    return [ok, n]


def numbers(text: str) -> set[int]:
    """The numbers in a turn, in digits ("$1,850", "11 am") or words ("one hundred seventy-five")."""
    t = text.lower().replace("-", " ")
    found = {int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", t)}
    total = current = 0
    seen = False
    for word in re.findall(r"[a-z]+", t) + ["."]:
        if word in UNITS:
            current += UNITS[word]
            seen = True
        elif word in TENS:
            current += TENS[word]
            seen = True
        elif word == "hundred" and seen:
            current = (current or 1) * 100
        elif word == "thousand" and seen:
            total, current = total + (current or 1) * 1000, 0
        elif word == "and" and seen:
            continue
        elif seen:
            found.add(total + current)
            total = current = 0
            seen = False
    return found


def _slot(at) -> str:
    local = at.astimezone(slots.TORONTO)
    return f"{slots.WEEKDAYS[local.weekday()]} {local:%H:%M}"


def _args(arguments: str) -> dict:
    try:
        value = json.loads(arguments or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
