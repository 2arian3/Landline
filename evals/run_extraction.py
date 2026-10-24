"""The extraction eval (E-02): the Auditor on call transcripts with gold facts. Part of `make eval`.

    uv run python -m evals.run_extraction             # the extractor's recorded answers: no key, no network
    uv run python -m evals.run_extraction --live      # call the LLM and re-record its answers
    uv run python -m evals.run_extraction --no-report # tuning: print, leave the report alone

Each case runs the real Auditor twice:
- **rules**: no model. The Caller's live `record_fact`s (some deliberately wrong: quotes from the agent's
  own turns, values their quotes contradict), checked and reconciled against the transcript, plus the
  red-flag rules and the viewing. This is what the renter gets when the LLM is down or slow.
- **with extractor**: the same, plus the text LLM's facts. With --live (LLM_PROVIDER and its key set) its
  answers are recorded to evals/fixtures/recordings/auditor/; otherwise they are replayed from there, so
  `make eval` and `make test` give the same numbers with no key and no network.

Scored in code against each case's gold: precision and recall per field, red flags and the viewing
included. **Ungrounded** counts facts in the outcome whose quote isn't in a landlord turn, re-checked here
independently of the Auditor; the bar is 0. Facts outside the gold vocabulary (`other`, `light`,
`outdoor_space`) aren't scored. Tune sets live in fixtures/transcripts/, holdout in
fixtures/holdout/transcripts/; quote holdout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from evals import report
from landline.agents.auditor import Audit, CallRecord, ListingContext, ToolFact, Turn, audit, grounding, llm
from landline.agents.auditor.numbers import amounts
from landline.agents.auditor.values import bare
from landline.config import ROOT

FIXTURES = ROOT / "evals" / "fixtures"
SETS = {"tune": FIXTURES / "transcripts", "holdout": FIXTURES / "holdout" / "transcripts"}
RECORDINGS = FIXTURES / "recordings" / "auditor"
SUMMARY = report.REPORTS / "extraction.json"
ENDED_AT = datetime.fromisoformat("2026-10-24T15:00:00-04:00")   # a Saturday afternoon, fixed for viewings
UNSCORED = {"other", "light", "outdoor_space"}
PRECISION_BAR = 0.9


@dataclass
class Score:
    case_id: str
    predicted: set[tuple[str, str]]
    gold: set[tuple[str, str]]
    facts_shown: int
    ungrounded: list[str] = field(default_factory=list)     # quotes shown that no landlord said
    dropped: dict[str, int] = field(default_factory=dict)   # claims the Auditor refused, by why
    extractor: str = "none"

    def wrong(self) -> list[str]:
        return [f"+{f}={v}" for f, v in sorted(self.predicted - self.gold)] + \
               [f"-{f}={v}" for f, v in sorted(self.gold - self.predicted)]


def load(set_name: str) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SETS[set_name].glob("*.json"))]


def record(case: dict[str, Any]) -> CallRecord:
    return CallRecord(
        call_id=f"c_{case['id']}", listing_id=case["listing"]["listing_id"],
        end_reason=case.get("end_reason", "done"),
        turns=[Turn(i, t["speaker"], t["text"], t.get("lang")) for i, t in enumerate(case["turns"])],
        listing=ListingContext.of(case["listing"]), ended_at=ENDED_AT,
        tool_facts=[ToolFact(f["field"], f["value"], f["quote"], f.get("turn_idx"))
                    for f in case["tool_facts"]],
        slot_text=case.get("slot_text"))


def gold_pairs(gold: dict[str, Any]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for f, v in gold.items():
        if f == "fees" and v == "none":
            out.add(("fees", "none"))   # "no other monthly fees" (#187, #216)
        elif f == "fees":
            out |= {("fees", f"{x['name']}:{x['amount']}:{x['period']}") for x in v}
        elif f == "red_flags":
            out |= {("red_flags", code) for code in v}
        else:
            out.add((f, str(v)))
    return out


def predicted_pairs(a: Audit) -> set[tuple[str, str]]:
    out = {(h.value.field, h.value.value) for h in a.heard if h.value.field not in UNSCORED}
    out |= {("red_flags", f.code) for f in a.flags}
    if a.outcome.viewing is not None:
        out.add(("viewing", a.outcome.viewing.starts_at.strftime("%Y-%m-%dT%H:%M")))
    return out


def _grounded(quote: str, turns: list[dict[str, Any]]) -> bool:
    """The quote is in a landlord turn, or it is the read-back a landlord confirmed and every amount in it
    is one the landlord also said (#216). A yes to a number nobody said is still ungrounded."""
    landlord = [t["text"] for t in turns if t["speaker"] == "landlord"]
    if grounding.verify(quote, landlord):
        return True
    need = set(amounts(quote))
    said = {n for text in landlord for n in amounts(text)}
    if not need or not need <= said:
        return False
    for i, t in enumerate(turns):
        if t["speaker"] == "landlord" or not grounding.verify(quote, [t["text"]]):
            continue
        nxt = next((u for u in turns[i + 1:] if u["speaker"] == "landlord"), None)
        if nxt is not None and bare(nxt["text"]) == "yes":
            return True
    return False


def score(case: dict[str, Any], a: Audit) -> Score:
    turns = case["turns"]
    ungrounded = [f.quote for f in a.outcome.facts if not _grounded(f.quote, turns)]
    ungrounded += [c.quote for c in a.outcome.conflicts if not _grounded(c.quote, turns)]
    dropped: dict[str, int] = defaultdict(int)
    for d in a.dropped:
        dropped[d.why.split(":")[0]] += 1
    return Score(case["id"], predicted_pairs(a), gold_pairs(case["gold"]), len(a.outcome.facts), ungrounded,
                 dict(dropped), a.extractor)


def summarize(scores: list[Score]) -> dict[str, Any]:
    per: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])   # field → correct, predicted, gold
    for s in scores:
        for f, _ in s.predicted:
            per[f][1] += 1
        for f, _ in s.gold:
            per[f][2] += 1
        for f, _ in s.predicted & s.gold:
            per[f][0] += 1
    correct, pred, gold = (sum(v[i] for v in per.values()) for i in range(3))
    shown = sum(s.facts_shown for s in scores)
    ungrounded = sum(len(s.ungrounded) for s in scores)
    dropped: dict[str, int] = defaultdict(int)
    for s in scores:
        for k, v in s.dropped.items():
            dropped[k] += v
    return {
        "cases": len(scores), "precision": _div(correct, pred), "recall": _div(correct, gold),
        "correct": correct, "predicted": pred, "gold": gold,
        "facts_shown": shown, "ungrounded": ungrounded, "ungrounded_rate": _div(ungrounded, shown) or 0.0,
        "dropped": dict(dropped),
        "fields": {f: {"precision": _div(c, p), "recall": _div(c, g), "correct": c, "predicted": p, "gold": g}
                   for f, (c, p, g) in sorted(per.items())},
        "cases_exact": sum(s.predicted == s.gold for s in scores),
        "extractor": sorted({s.extractor for s in scores}),
        "misses": {s.case_id: s.wrong() for s in scores if s.wrong()},
    }


class Recorder:
    """The extractor's answers per case: recorded on a live run, replayed otherwise."""

    def __init__(self, live: llm.Chat | None, record: bool):
        self.live, self.record = live, record
        self.gate = asyncio.Semaphore(2)   # Higgs answers 429 to many calls at once

    async def _ask(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        assert self.live is not None
        async with self.gate:
            for attempt in range(4):
                try:
                    return await self.live.json(system, user, schema)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 429 or attempt == 3:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))
        raise AssertionError("unreachable")

    def complete_for(self, case_id: str) -> Callable | None:
        path = RECORDINGS / f"{case_id}.json"
        if self.live is not None:
            async def live(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
                raw = await self._ask(system, user, schema)
                if self.record:
                    RECORDINGS.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({"model": self.live.model, "user": user, "response": raw},  # type: ignore[union-attr]
                                               ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                return raw
            return live
        if not path.exists():
            return None
        saved = json.loads(path.read_text(encoding="utf-8"))

        async def replay(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            if user != saved["user"]:
                raise RuntimeError(f"{case_id}: the transcript changed since it was recorded; run --live")
            return saved["response"]
        return replay


async def run_set(cases: list[dict[str, Any]], recorder: Recorder | None) -> list[Score]:
    async def one(case: dict[str, Any]) -> Score:
        complete = recorder.complete_for(case["id"]) if recorder else None
        return score(case, await audit(record(case), complete=complete, timeout_s=60))
    return list(await asyncio.gather(*(one(c) for c in cases)))


async def run(sets: list[str], live: bool, keep_report: bool) -> dict[str, Any]:
    chat = llm.from_env() if live else None
    if live and chat is None:
        raise SystemExit("--live needs LLM_PROVIDER and its API key (see .env.example)")
    recorder = Recorder(chat, record=chat is not None)
    recorded = any(RECORDINGS.glob("*.json"))
    mode = f"live {chat.model}" if chat else ("replayed recordings" if recorded else "none")
    results: dict[str, Any] = {}
    for name in sets:
        cases = load(name)
        rules = summarize(await run_set(cases, None))
        with_llm = summarize(await run_set(cases, recorder)) if mode != "none" else None
        results[name] = {"rules": rules, "extractor": with_llm}
    meta = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "sha": _sha(), "mode": mode}
    if not keep_report:
        data = json.loads(SUMMARY.read_text(encoding="utf-8")) if SUMMARY.exists() else {}
        for name, r in results.items():
            data[name] = {"current": {**meta, **r}, "previous": (data.get(name) or {}).get("current")}
        SUMMARY.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        report.write()
    return {"meta": meta, **results}


def passes(results: dict[str, Any]) -> bool:
    ok = True
    for name, r in results.items():
        if name == "meta":
            continue
        for mode, s in r.items():
            if s is None:
                continue
            if s["ungrounded"] or (s["precision"] or 0) < PRECISION_BAR:
                print(f"FAIL {name}/{mode}: ungrounded {s['ungrounded']}, precision {s['precision']}")
                ok = False
    return ok


def _div(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def _sha() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=ROOT, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True,
                               cwd=ROOT, check=True).stdout.strip()
        return f"{sha}{'+dirty' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--set", choices=[*SETS, "all"], default="all")
    p.add_argument("--live", action="store_true", help="call the LLM and re-record its answers")
    p.add_argument("--no-report", action="store_true")
    args = p.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    sets = list(SETS) if args.set == "all" else [args.set]
    results = asyncio.run(run(sets, args.live, args.no_report))
    for name in sets:
        for mode, s in results[name].items():
            if s is None:
                continue
            print(f"{name:8} {mode:9} precision {s['precision']}  recall {s['recall']}  "
                  f"ungrounded {s['ungrounded']}/{s['facts_shown']}  "
                  f"exact cases {s['cases_exact']}/{s['cases']}")
            for cid, wrong in s["misses"].items():
                print(f"    {cid}: {' '.join(wrong)}")
    return 0 if passes(results) else 1


if __name__ == "__main__":
    sys.exit(main())

