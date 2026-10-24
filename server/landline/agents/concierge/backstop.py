"""The one gap code fills for the Concierge (A-04): a renter line in another writing system that the
Concierge answered without a tool.

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


async def catch_up(reader: Any | None, line: str, ctx: tools.ToolContext,
                   timeout_s: float = TIMEOUT_S) -> str | None:
    """Saves what `line` says through update_preferences, and returns the tool's reply; None when the line
    isn't one for this, there's no text model, it couldn't be read, or it says nothing to save.
    `reader` is an agents.auditor.llm.Chat (its `json` method)."""
    if reader is None or not foreign(line):
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
        self.replied = self.tooled = self.answered = self.taken = False

    def speech_started(self) -> None:
        self._reset()

    def response_created(self) -> None:
        self.replied = True

    def tool_called(self) -> None:
        self.tooled = True

    def response_done(self, event: dict[str, Any]) -> None:
        response = event.get("response") or {}
        if not self.replied or response.get("status") not in (None, "completed"):
            return   # a reply from before the renter spoke, or one they talked over
        if not any(o.get("type") == "function_call" for o in response.get("output") or []):
            self.answered = True

    def transcript(self, text: str) -> None:
        self.heard = text

    def line(self) -> str | None:
        if self.heard and self.answered and not self.tooled and not self.taken and foreign(self.heard):
            self.taken = True
            return self.heard
        return None
