"""Scores one simulated Concierge conversation against its case, in code. No model grades anything.

A case passes when every `final` check holds on the session state at the end: preferences, reactions,
focus, the call request (listings, viewing windows, may_book, extra questions), that it ended with
end_session, and that booking wasn't allowed before the renter said so. `expect` checks are reported as
"on time" (the agent acted in the turn it should have). Hygiene counts what breaks the prompt's rules
without changing the state: turns over two sentences, claiming a change with no tool call in that turn,
ids read out, list formatting, and tool replies that mean bad arguments.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from evals.sim.cases import Case, Want
from evals.sim.driver import Trace
from landline.agents.concierge import tools
from landline.db.catalogue import Catalogue

# A past-tense claim that something changed. Said in a turn with no tool call, nothing did change.
CLAIM = re.compile(r"\b(i'?ve|i have)\s+(just\s+|now\s+|also\s+)?(noted|updated|added|saved|recorded|set|"
                   r"changed|removed|lined up|booked|shown)\b|\bnoted\b", re.I)
# Dots that don't end a sentence. A lone E/W/N/S is left alone: "Bloor St. W. It's" is two sentences.
ABBREV = re.compile(r"\b(St|Ave|Rd|Dr|Blvd|Cres|Crt|Mr|Mrs|Ms|No)\.", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?。！？؟])\s+")
MARKDOWN = re.compile(r"\*\*|(^|\n)\s*([-•*]|\d+\.)\s")
# A last turn that opens a conversation instead of closing it ("Hello! How can I help you today?").
GREETS = re.compile(r"\bhow can i (help|assist)\b|^(hello|hi)\b", re.I)
SCRIPTS = {"fa": r"[؀-ۿ]", "ar": r"[؀-ۿ]", "ur": r"[؀-ۿ]", "zh": r"[一-鿿]", "yue": r"[一-鿿]",
           "ja": "[぀-ヿ]", "ko": r"[가-힯]", "ru": r"[Ѐ-ӿ]", "uk": r"[Ѐ-ӿ]", "hi": r"[ऀ-ॿ]",
           "pa": "[਀-੿]", "gu": "[઀-૿]", "bn": "[ঀ-৿]",
           "ta": "[஀-௿]", "he": "[֐-׿]", "el": "[Ͱ-Ͽ]", "th": "[฀-๿]",
           # Latin script: Spanish by its marks and common words, none of which English replies use.
           "es": r"(?i)[ñ¿¡áéíóú]|\b(?:que|para|con|una|los|las|está|quiero|busco|tengo|gracias|mes)\b"}
REPLIES = {"not_caught": tools.NOT_CAUGHT, "which": tools.WHICH, "unknown_tool": tools.UNKNOWN,
           "broken": tools.BROKEN}


@dataclass
class Check:
    name: str
    kind: str          # "final" decides the case; "on_time" and "permission" are reported
    passed: bool
    detail: str = ""


@dataclass
class Result:
    case_id: str
    run: int
    passed: bool
    end: str
    checks: list[Check] = field(default_factory=list)
    hygiene: dict[str, int] = field(default_factory=dict)
    lines: int = 0
    agent_turns: int = 0
    ms: list[int] = field(default_factory=list)   # each agent turn after a line, tools included

    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def to_json(self) -> dict:
        return asdict(self)


class BadCase(ValueError):
    """The case names something the board doesn't have: the case is wrong, not the agent."""


def score(case: Case, trace: Trace, catalogue: Catalogue) -> Result:
    checks: list[Check] = []
    for i, line in enumerate(case.lines):
        if line.expect is None:
            continue
        if i >= len(trace.states):
            checks.append(Check(f"line{i}", "on_time", False, f"the conversation ended first ({trace.end})"))
            continue
        checks += _wants(line.expect, trace.states[i], trace, catalogue, kind="on_time", line=i)
    checks += _wants(case.final, trace.final, trace, catalogue, kind="final", line=len(trace.lines) - 1)
    if case.may_book_from is not None:
        early = [t for t in trace.tools if t.name == "call_listings" and t.line < case.may_book_from
                 and _args(t.arguments).get("may_book") is True]
        checks.append(Check("final.may_book.not_before_permission", "final", not early,
                            f"may_book set on line {early[0].line}" if early else ""))
    return Result(case_id=case.id, run=trace.run, passed=all(c.passed for c in checks if c.kind == "final"),
                  end=trace.end, checks=checks, hygiene=hygiene(trace, catalogue), lines=len(trace.lines),
                  agent_turns=len(trace.agent), ms=[a.ms for a in trace.agent if a.line >= 0])


def _wants(want: Want, state: dict, trace: Trace, catalogue: Catalogue, *, kind: str,
           line: int) -> list[Check]:
    at = "final" if kind == "final" else f"line{line}"
    out: list[Check] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        out.append(Check(f"{at}.{name}", kind, ok, "" if ok else detail))

    try:
        for key, value in (want.prefs or {}).items():
            got = state["preferences"].get(key)
            check(f"prefs.{key}", pref_ok(key, value, got), f"want {value!r}, got {got!r}")
        own = None if kind == "final" else line   # inside `expect`, refs default to that line's board
        for r in want.reactions or []:
            lid = _one(r.ref, _board(trace, r.at if r.at is not None else own), catalogue)
            said = state["reactions"].get(lid)
            fair = [r.reaction] if isinstance(r.reaction, str) else r.reaction
            ok = said is not None and (fair is None or said["reaction"] in fair) \
                and (r.attribute is None or said.get("attribute") == r.attribute)
            check(f"reaction.{r.ref}", ok, f"want {r.reaction}/{r.attribute} on {_addr(catalogue, lid)}, "
                                           f"got {said}")
        for r in want.no_reactions or []:
            lid = _one(r.ref, _board(trace, r.at if r.at is not None else own), catalogue)
            said = state["reactions"].get(lid)
            check(f"no_reaction.{r.ref}", said is None, f"got {said} on {_addr(catalogue, lid)}")
        if want.focus is not None:
            lid = _one(want.focus.ref, _board(trace, want.focus.at if want.focus.at is not None else own),
                       catalogue)
            got = state.get("focus_listing_id")
            check(f"focus.{want.focus.ref}", got == lid,
                  f"want {_addr(catalogue, lid)}, got {_addr(catalogue, got)}")
        if want.area is not None:
            label = ((state.get("focus_area") or {}).get("label") or "")
            check("area", _close(want.area, label), f"want {want.area!r}, got {label!r}")
        if want.no_request:
            check("no_request", state.get("request") is None, f"got {state.get('request')}")
        if want.request is not None:
            _request(want, state, trace, catalogue, line, check)
        if want.tools is not None:
            called = [n for a in trace.agent if a.line == line for n in a.tools]
            for name in want.tools:
                check(f"tool.{name}", name in called, f"called {called}")
        if want.ended is not None:
            check("ended", (trace.end == "end_session") == want.ended, f"ended by {trace.end}")
        if want.ended:
            # After end_session nothing more is said, so a goodbye has to come in that same turn. Without
            # one the renter hears the line drop.
            bye = [a for a in trace.agent if "end_session" in a.tools]
            said = bye[-1].text.strip() if bye else ""
            check("ended.goodbye", bool(said) and not GREETS.search(said),
                  f"ended with {said!r}" if said else "hung up without a word" if bye
                  else "never called end_session")
        if want.replies_in is not None:
            ok, detail = _replies_in(want.replies_in, trace)
            check(f"replies_in.{want.replies_in}", ok, detail)
        for word in want.replies_avoid or []:
            hits = [a.line for a in trace.agent if word.lower() in a.text.lower()]
            check(f"replies_avoid.{word}", not hits, f"said on lines {hits}")
        greeting = trace.agent[0].text if trace.agent else ""
        for word in want.greeting_says or []:
            check(f"greeting.says.{word}", word.lower() in greeting.lower(), f"greeting: {greeting!r}")
        for word in want.greeting_avoids or []:
            check(f"greeting.avoids.{word}", word.lower() not in greeting.lower(), f"greeting: {greeting!r}")
    except BadCase as exc:
        check("case", False, f"bad case: {exc}")
    return out


def _request(want: Want, state: dict, trace: Trace, catalogue: Catalogue, line: int, check) -> None:
    req, w = state.get("request"), want.request
    assert w is not None
    check("request", req is not None, "call_listings recorded nothing")
    if req is None:
        return
    if w.listings is not None:
        calls = [t for t in trace.tools if t.name == "call_listings" and t.line <= line]
        board = calls[-1].board if calls else []
        refs = [w.listings] if isinstance(w.listings, str) else w.listings
        wanted = [lid for ref in refs for lid in _resolve(ref, board, catalogue)]
        got = req["listing_ids"]
        check("request.listings", set(got) == set(wanted),
              f"want {[_addr(catalogue, x) for x in wanted]}, got {[_addr(catalogue, x) for x in got]}")
    if w.availability is not None:
        wanted = {(s.day.lower(), s.from_, s.to) for s in w.availability}
        got = {(a["day"].lower(), a.get("from_") or a.get("from"), a["to"]) for a in req["availability"]}
        check("request.availability", got == wanted, f"want {sorted(wanted)}, got {sorted(got)}")
    if w.may_book is not None:
        check("request.may_book", req["may_book"] == w.may_book, f"want {w.may_book}, got {req['may_book']}")
    for word in w.questions or []:
        check(f"request.question.{word}", any(word.lower() in q.lower() for q in req["questions"]),
              f"questions {req['questions']}")


def _replies_in(script: str, trace: Trace) -> tuple[bool, str]:
    pattern = re.compile(SCRIPTS[script])
    switched = [i for i, said in enumerate(trace.lines) if pattern.search(said)]
    if not switched:
        return False, "bad case: no line is in that script"
    after = [a for a in trace.agent if a.line >= switched[0] and a.text.strip()]   # silence: ended.goodbye
    wrong = [a.line for a in after if not pattern.search(a.text)]
    return (bool(after) and not wrong), f"replies after line {switched[0]} not in {script}: lines {wrong}"


# ── hygiene ─────────────────────────────────────────────────────────────────

def hygiene(trace: Trace, catalogue: Catalogue) -> dict[str, int]:
    ids = {lid for board in trace.boards for lid in board} | {lid for t in trace.tools for lid in t.board}
    numbers = {lid.split(":", 1)[-1] for lid in ids if len(lid.split(":", 1)[-1]) >= 5}
    counts = {"long_turns": 0, "claims_without_tool": 0, "ids_read": 0, "markdown": 0}
    for turn in trace.agent:
        counts["long_turns"] += len(sentences(turn.text)) > 2
        counts["claims_without_tool"] += turn.line >= 0 and not turn.tools and bool(CLAIM.search(turn.text))
        counts["ids_read"] += any(x in turn.text for x in ids | numbers)
        counts["markdown"] += bool(MARKDOWN.search(turn.text))
    for name, reply in REPLIES.items():
        # "which" also counts the replies that name a shared address's cards, and a feedback with no ref.
        counts[name] = sum(t.output == reply or (name == "which" and (t.output.startswith("Which one?")
                                                                      or t.output == tools.NO_REF))
                           for t in trace.tools)
    counts["higgs_errors"] = len(trace.errors)
    return counts


def sentences(text: str) -> list[str]:
    plain = ABBREV.sub(lambda m: m.group(1), text.strip())
    return [s for s in SENTENCE_END.split(plain) if s.strip()]


# ── matching ────────────────────────────────────────────────────────────────

def pref_ok(key: str, want: object, got: object) -> bool:
    if want is None:
        return got in (None, [], "")
    if isinstance(want, bool):
        return got is want
    if isinstance(want, int | float):
        return got is not None and float(got) == float(want)
    if isinstance(want, list):
        items = [g["label"] if isinstance(g, dict) else str(g) for g in (got or [])]
        return all(any(_close(str(w), item) for item in items) for w in want)
    if key == "language":
        return isinstance(got, str) and got.lower().startswith(str(want).lower())
    return got is not None and _close(str(want), str(got))


def _close(want: str, got: str) -> bool:
    """`want`'s words appear in `got`, ignoring case, punctuation and plural s: "dog" ≈ "small dogs"."""
    def norm(s: str) -> str:
        words = re.findall(r"\w+", s.lower())
        return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)
    w, g = norm(want), norm(got)
    return bool(w) and (w in g or g in w) if g else False


def _resolve(ref: str, board: list[str], catalogue: Catalogue) -> list[str]:
    kind, _, arg = ref.partition(":")
    if kind in ("rank", "top"):
        n = int(arg)
        if n > len(board):
            raise BadCase(f"{ref} on a board of {len(board)}")
        return [board[n - 1]] if kind == "rank" else board[:n]
    if kind == "addr":
        hits = [lid for lid in board if _close(arg, catalogue.by_id[lid].address)]
        if len(hits) != 1:
            raise BadCase(f"{ref} matches {len(hits)} cards on the board")
        return hits
    raise BadCase(f"unknown ref {ref!r}")


def _one(ref: str, board: list[str], catalogue: Catalogue) -> str:
    ids = _resolve(ref, board, catalogue)
    if len(ids) != 1:
        raise BadCase(f"{ref} names {len(ids)} listings; use rank: or addr:")
    return ids[0]


def _board(trace: Trace, at: int | None) -> list[str]:
    if at is None:
        raise BadCase("a final reaction or focus needs `at`, the line whose board it refers to")
    if at >= len(trace.boards):
        return []
    return trace.boards[at]


def _addr(catalogue: Catalogue, lid: str | None) -> str | None:
    return catalogue.by_id[lid].address if lid in catalogue.by_id else lid


def _args(arguments: str) -> dict:
    try:
        value = json.loads(arguments or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
