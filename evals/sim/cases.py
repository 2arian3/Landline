"""The fixture format for simulated Concierge calls (evals/fixtures/concierge/*.json, holdout/concierge/).

    {"id": "dog-top3-book",
     "about": "what the case tests, in one line",
     "lines": [{"say": "Hi, I need a one bedroom under 2,800.",
                "expect": {"prefs": {"max_monthly": 2800, "min_beds": 1}}},   # optional, on time
               {"say": "The second one is too far from everything.",
                "expect": {"reactions": [{"ref": "rank:2", "reaction": "dislike",
                                          "attribute": "location"}]}}],
     "may_book_from": 4,                                     # optional: the line that allows booking
     "final": {"prefs": {...}, "reactions": [{"ref": "rank:2", "at": 1, ...}],
               "request": {"listings": "top:3", "availability": [{"day": "saturday", "from": "10:00",
                           "to": "14:00"}], "may_book": true, "questions": ["parking"]},
               "ended": true}}

`expect` is checked on the state right after its line (did the agent act on time). `final` is checked on
the state at the end and decides whether the case passes. Listing refs are symbolic so the case tests the
Concierge, not the ranker: `rank:N` and `top:N` resolve against the board the renter saw before line `at`
(or before the line the `expect` sits on), `addr:words` against the street words on that board. A
request's listings resolve against the board `call_listings` saw.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SETS = {"tune": FIXTURES / "concierge", "holdout": FIXTURES / "holdout" / "concierge"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Reaction = Literal["like", "dislike", "reject"]


class ReactionWant(_Strict):
    ref: str
    at: int | None = None          # the line whose board `ref` refers to; implied inside `expect`
    reaction: Reaction | list[Reaction] | None = None   # a list when either reading is fair
    attribute: Literal["light", "location", "size", "cost", "parking"] | None = None


class SlotWant(_Strict):
    day: str
    from_: str = Field(alias="from")
    to: str


class RequestWant(_Strict):
    listings: str | list[str] | None = None    # "top:3", or ["rank:1", "addr:Don Mills"]
    availability: list[SlotWant] | None = None   # the exact set of windows
    may_book: bool | None = None
    questions: list[str] | None = None          # words each of which must appear in some extra question


class FocusWant(_Strict):
    ref: str
    at: int | None = None


class Want(_Strict):
    prefs: dict[str, object] | None = None
    reactions: list[ReactionWant] | None = None
    focus: FocusWant | None = None
    area: str | None = None          # words the focused area's label must contain
    request: RequestWant | None = None
    no_request: bool | None = None   # nothing may be lined up to call
    tools: list[str] | None = None   # `expect` only: tools the agent must call in that turn
    ended: bool | None = None        # `final` only: the Concierge called end_session
    replies_in: str | None = None    # `final` only: once the renter writes in this script, so does the agent


class Line(_Strict):
    say: str
    expect: Want | None = None


class Case(_Strict):
    id: str
    about: str
    lines: list[Line]
    final: Want
    may_book_from: int | None = None   # call_listings may set may_book only from this line on


def load_set(name: str) -> list[Case]:
    folder = SETS[name]
    return [load(p) for p in sorted(folder.glob("*.json"))]


def load(path: Path) -> Case:
    case = Case.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if case.id != path.stem:
        raise ValueError(f"{path.name}: id {case.id!r} must match the file name")
    return case
