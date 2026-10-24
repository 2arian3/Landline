"""The gaps code fills for the Concierge (A-04): a renter line in another writing system that the
Concierge answered without a tool; an English line whose reply claimed a save with no tool ("I've updated
your budget…", #96, `claims_save`); and a renter asking for calls or a viewing that the Concierge answered
without call_listings (#127, at the end of this file).

Seen in the eval (docs/EVAL_FINDINGS.md): after a renter switches to Korean or Farsi, the Concierge answers
in their language ("a two-bedroom up to $3,000, got it") but calls no tool, so nothing is saved and the map
doesn't move. Prompt changes didn't fix it. So code catches exactly that case: a renter line mostly in a
non-Latin script, after which the agent's turn ended with no tool call. A text model reads that line
alone for what update_preferences takes, and the tool's own handler saves it, with the same validation and
events as when the Concierge calls it. English (and other Latin-script) lines never get here: an earlier
catch-up that read every line made false updates on English ones ("it'll just be me" became studios).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
import unicodedata
from typing import Any

from landline.agents.concierge import tools
from landline.agents.concierge.prompt import primary

log = logging.getLogger(__name__)

TIMEOUT_S = 20   # in the background: a late save still moves the map
MIN_LETTERS = 4
SYSTEM = (
    "A renter said the line below to Landline, an assistant that helps people rent in Toronto. Return only "
    "what this line itself says the renter wants, in English with digits. max_monthly: the most they will "
    "pay per month, in dollars. min_beds: bedrooms they need (0 for a studio). pets: their pets as English "
    "words, like dog or cat. areas: parts of Toronto they name. must_have: things they say they require. "
    "move_in: when they want to move in, in English. occupants: how many people will live there. Use null "
    "or an empty list for anything the line doesn't say; a price of one listing is not a budget. "
    "language: the two-letter code of the language the line is in."
)
_NUM = {"type": ["integer", "null"]}
_WORDS = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"max_monthly": _NUM, "min_beds": _NUM, "pets": _WORDS, "areas": _WORDS,
                   "must_have": _WORDS, "move_in": {"type": ["string", "null"]}, "occupants": _NUM,
                   "language": {"type": "string"}},
    "required": ["max_monthly", "min_beds", "pets", "areas", "must_have", "move_in", "occupants", "language"],
}
_ids = itertools.count(1)


def foreign(line: str) -> bool:
    """Mostly letters outside the Latin script: Korean, Persian, Punjabi, Chinese, Arabic, Russian…"""
    letters = [c for c in line if c.isalpha()]
    if len(letters) < MIN_LETTERS:
        return False
    latin = sum(1 for c in letters if unicodedata.name(c, "").startswith("LATIN"))
    return latin * 2 < len(letters)


# A save the Concierge claimed without a tool (#96, #129): "I've updated your budget to three thousand
# dollars", "Perfect, I've saved that", "Got it, two bedrooms", and nothing was saved. Only a reply that
# starts with such a claim lets an English line be read: reading every line made false updates ("it'll
# just be me" became studios).
_CLAIM = re.compile(r"^\W*(?:(?:ok(?:ay)?|great|perfect|sure|alright|all\s+right|thanks|thank\s+you)\W+)?"
                    r"(?:i've|i’ve|i\s+have)\s+(?:updated|noted|saved|set|added|changed)\b"
                    r"|^\W*got\s+it\s*[,:–—-]\s*\w", re.I)


def claims_save(reply: str) -> bool:
    """Whether the Concierge's reply starts by saying it saved something."""
    return bool(_CLAIM.search(reply or ""))


async def catch_up(reader: Any | None, line: str, ctx: tools.ToolContext,
                   timeout_s: float = TIMEOUT_S, *, claimed: bool = False) -> str | None:
    """Saves what `line` says through update_preferences, and returns the tool's reply; None when the line
    isn't one for this, there's no text model, it couldn't be read, or it says nothing to save.
    `reader` is an agents.auditor.llm.Chat (its `json` method). `claimed`: the Concierge's reply to this
    line claimed a save with no tool (see `claims_save`), so an English line is read too."""
    if reader is None or not (claimed or foreign(line)):
        return None
    try:
        out = await asyncio.wait_for(reader.json(SYSTEM, line, SCHEMA, name="preferences"), timeout_s)
    except Exception as exc:   # a timeout, a 429: the turn already went on without it
        log.warning("call %s: catch-up couldn't read the line: %r", ctx.call_id, exc)
        return None
    if not isinstance(out, dict):
        return None
    delta = {k: v for k, v in out.items() if k in SCHEMA["properties"] and v not in (None, [], "")}
    lang = primary(delta.pop("language", None))
    known = ctx.runtime.state(ctx.session_id).preferences.language
    if len(lang) == 2 and lang != "en" and primary(known) != lang:
        delta["language"] = lang
    if not delta:
        return None
    log.info("call %s: catch-up after a turn with no tool: %s", ctx.call_id, delta)
    return await tools.handle("update_preferences", json.dumps(delta), f"catch_up_{next(_ids)}", ctx)


class Turn:
    """Watches one call's events for the case above. Audio events don't come in a fixed order: the
    renter's transcript can arrive before or after the reply to it is done. `line()` gives the renter's
    line once both are in and the reply asked for no tool, at most once a turn."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.heard: str | None = None
        self.said: str | None = None   # the start of the Concierge's reply to it
        self.replied = self.tooled = self.answered = self.taken = self.call_taken = False
        self.claim_taken = False
        self.tools: set[str] = set()

    def speech_started(self) -> None:
        self._reset()

    def response_created(self) -> None:
        self.replied = True

    def tool_called(self, name: str = "") -> None:
        self.tooled = True
        self.tools.add(name)

    def response_done(self, event: dict[str, Any]) -> None:
        response = event.get("response") or {}
        if not self.replied or response.get("status") not in (None, "completed"):
            return   # a reply from before the renter spoke, or one they talked over
        if not any(o.get("type") == "function_call" for o in response.get("output") or []):
            self.answered = True

    def transcript(self, text: str) -> None:
        self.heard = text

    def agent_said(self, text: str) -> None:
        """What the Concierge said in reply. Only its first line counts, and only once it replied to this
        turn: a talked-over reply's transcript can land after the renter started speaking again."""
        if self.replied and self.said is None:
            self.said = text

    def line(self) -> str | None:
        if self.heard and self.answered and not self.tooled and not self.taken and foreign(self.heard):
            self.taken = True
            return self.heard
        return None

    def claimed_line(self) -> str | None:
        """The renter's line (Latin script) when the reply claimed a save and no tool ran, once."""
        if (self.heard and self.answered and not self.tooled and not self.claim_taken and self.said
                and claims_save(self.said) and not foreign(self.heard)):
            self.claim_taken = True
            return self.heard
        return None

    def call_line(self) -> str | None:
        """The renter's line when it asked for calls or a viewing and the reply ran no call_listings, once."""
        if (self.heard and self.answered and "call_listings" not in self.tools and not self.call_taken
                and asks_for_calls(self.heard)):
            self.call_taken = True
            return self.heard
        return None


# ── Calls the renter asked for (#127) ───────────────────────────────────────
#
# On a real call the renter said "Can you call the first one? I really like it." and the Concierge answered
# "I can't call the landlord directly", with no call_listings. The prompt now says Landline makes the calls;
# this is the catch for when the model still doesn't. Only English, and only lines that plainly ask for a
# call, a viewing or availability about listings: no model, so nothing is guessed. call_listings' own
# handler records the plan (with its validation and events), and the Concierge reads its reply back.

PLANNED = "[calls recorded]"   # the note the Concierge gets, through the Line, with call_listings' reply
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
             "fifth": 5, "5th": 5, "sixth": 6, "6th": 6}
_ORD = r"(?:first|second|third|fourth|fifth|sixth|last|1st|2nd|3rd|4th|5th|6th)"
_TARGET = (rf"(?:them|those|these|both|all\s+of\s+them|all\s+three|number\s+\w+|#\s?\d"
           rf"|(?:this|that)\s+(?:one|place|listing|landlord|building)"
           rf"|the\s+(?:{_ORD}|top\s+\w+|landlords?|agents?|owners?|listings?|places?|buildings?|ones?)"
           rf"|{_ORD}\s+one)")
_CALL = re.compile(rf"\b(?:call|phone|ring|contact|reach(?:\s+out\s+to)?)\s+(?:up\s+)?{_TARGET}", re.I)
_VISIT = re.compile(r"\b(?:set(?:\s+up)?|book|arrange|schedule|organi[sz]e|get)\b[^.?!]{0,30}?"
                    r"\b(?:visit|viewing|showing|tour)s?\b", re.I)
_AVAILABLE = re.compile(r"\bcheck\s+(?:if|whether)\s+(?:it|they|this|that|the\s+\w+\s+one)(?:'s|’s|\s+is"
                        r"|\s+are)\s+(?:still\s+)?available\b", re.I)
_NOT = re.compile(r"\b(?:don't|don’t|dont|do\s+not|no\s+need\s+to|won't|never)\b[^.?!]{0,12}"
                  r"\b(?:call|phone|contact|book|set)\b|\bnot\s+yet\b|\bcall\b[^.?!]{0,20}\byet\b", re.I)


def asks_for_calls(line: str) -> bool:
    """Whether an English line plainly asks Landline to call about listings or set up a viewing:
    "can you call the first one", "call the top three", "set a house visit for the first one", "book a
    viewing for number two", "contact the landlord". Not "I'll call you back", "call me Sara", "what do you
    call that area" or "don't call yet"."""
    if foreign(line) or _NOT.search(line):
        return False
    return bool(_CALL.search(line) or _VISIT.search(line) or _AVAILABLE.search(line))


def refs_in(line: str, ctx: tools.ToolContext) -> list[str]:
    """The listings the line names, as call_listings' refs: "the first one" is "1", "the top three" is
    "1", "2", "3", "number two" is "2". "This one" or "the landlord" with a card in focus is that card.
    Nothing named gives no refs, so call_listings keeps a plan already made or takes the top three."""
    text = line.lower()
    top = re.search(r"\b(?:top|first)\s+(two|three|four|five|2|3|4|5)\b", text)
    if top:
        n = _WORD_NUMBERS.get(top.group(1)) or int(top.group(1))
        return [str(i) for i in range(1, n + 1)]
    found: list[str] = []
    for m in re.finditer(r"\b(?:number\s+(\w+)|#\s?(\d)|(" + _ORD + r"))\b", text):
        word = m.group(1) or m.group(2) or m.group(3)
        if word == "last":
            ref = "last"
        elif word in _ORDINALS:
            ref = str(_ORDINALS[word])
        elif word.isdigit():
            ref = word
        elif word in _WORD_NUMBERS:
            ref = str(_WORD_NUMBERS[word])
        else:
            continue
        if ref not in found:
            found.append(ref)
    if found:
        return found
    view = ctx.runtime.get(ctx.session_id)
    if view.focus_listing_id and re.search(r"\b(?:this|that|it|the\s+(?:landlord|agent|owner))\b", text):
        card = next((c for c in view.board if c.listing_id == view.focus_listing_id), None)
        if card is not None:
            return [str(card.rank)]
    return []


async def catch_up_calls(line: str, ctx: tools.ToolContext) -> str | None:
    """Records the calls `line` asked for through call_listings' own handler, and returns its reply; None
    when the line doesn't ask for any."""
    if not asks_for_calls(line):
        return None
    refs = refs_in(line, ctx)
    log.info("call %s: catch-up for calls after a turn without call_listings: refs %s", ctx.call_id, refs)
    arguments = json.dumps({"listing_refs": refs} if refs else {})
    return await tools.handle("call_listings", arguments, f"call_catch_up_{next(_ids)}", ctx)
