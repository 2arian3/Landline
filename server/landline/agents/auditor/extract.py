"""The transcript extractor: a text LLM proposes facts, each with the landlord's exact words.

Everything it returns is a claim. The Auditor drops any quote that isn't in a landlord turn, reads every
amount from the quote itself, and refuses a value its quote contradicts, so a wrong or made-up answer here
costs recall, never a wrong fact on the page. It never sees ids or the clock, and it is told not to add
anything up: totals are computed in code.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from landline.agents.auditor.values import CATEGORIES
from landline.contracts.tools import FactField

FIELDS = list(FactField.__args__)   # type: ignore[attr-defined]


@dataclass(frozen=True)
class Candidate:
    field: str
    value: str
    quote: str
    turn: int | None = None
    amount: float | None = None
    count: int | None = None
    each: bool | None = None
    period: str | None = None
    mandatory: bool | None = None
    name: str | None = None


Complete = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]   # (system, user, schema) → JSON

SYSTEM = f"""You read the transcript of a phone call between an AI assistant calling for a renter (AGENT) \
and a landlord or listing agent (LANDLORD) about one rental listing. List every fact the LANDLORD stated \
about the unit or the lease.

Rules:
- Only facts the LANDLORD said in their own words. If the AGENT says something and the LANDLORD only agrees \
("yes", "right", "that's correct"), skip it: it is the agent's words, not the landlord's. If the LANDLORD \
corrects the AGENT, use the correction.
- `quote` is copied character for character from one LANDLORD turn, in the language spoken, 3 to 25 words, \
and `turn` is that turn's number. Never translate, paraphrase or join two turns.
- `value` is short English words. For these fields use exactly one of the listed words: \
{"; ".join(f"{f}: {', '.join(v)}" for f, v in CATEGORIES.items())}.
- Money: `amount` is one number exactly as said for one unit (one parking spot, one month, one fee), never a \
sum or a product you worked out. `count` is how many units were mentioned (2 spots), else null. `each` is \
true when the amount is per unit. `period` is monthly or one_time. `mandatory` is true if it must be paid, \
false if optional, null if not said. `name` names a fee (parking, locker, deposit, utilities, internet, \
pet_fee, key_deposit, amenity).
- Use `rent` for the monthly rent, `parking_cost` for the price of a parking spot, `fees` for any other \
charge or deposit, `available` for whether the unit is still for rent, `available_from` for the move-in \
date.
- If the landlord changes an answer later, list both; the later turn wins.
- Nothing the landlord didn't say. An empty list is a fine answer."""

SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["facts"],
    "properties": {"facts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["field", "turn", "quote", "value", "amount", "count", "each", "period", "mandatory",
                     "name"],
        "properties": {
            "field": {"type": "string", "enum": FIELDS},
            "turn": {"type": "integer"},
            "quote": {"type": "string"},
            "value": {"type": "string"},
            "amount": {"type": ["number", "null"]},
            "count": {"type": ["integer", "null"]},
            "each": {"type": ["boolean", "null"]},
            "period": {"type": ["string", "null"], "enum": ["monthly", "one_time", None]},
            "mandatory": {"type": ["boolean", "null"]},
            "name": {"type": ["string", "null"]},
        },
    }}},
}


def prompt(turns: Sequence[Any], listing_summary: str) -> str:
    lines = [f"Listing: {listing_summary}", "", "Transcript:"]
    for t in turns:
        who = "LANDLORD" if t.speaker == "landlord" else "AGENT"
        lines.append(f"[{t.idx}] {who}: {t.text}")
    return "\n".join(lines)


async def extract(complete: Complete, turns: Sequence[Any], listing_summary: str) -> list[Candidate]:
    raw = await complete(SYSTEM, prompt(turns, listing_summary), SCHEMA)
    return parse(raw)


def parse(raw: dict[str, Any]) -> list[Candidate]:
    out = []
    for f in raw.get("facts") or []:
        if not isinstance(f, dict) or f.get("field") not in FIELDS or not f.get("quote"):
            continue
        out.append(Candidate(field=f["field"], value=str(f.get("value") or ""), quote=str(f["quote"]),
                             turn=f.get("turn") if isinstance(f.get("turn"), int) else None,
                             amount=f.get("amount") if isinstance(f.get("amount"), int | float) else None,
                             count=f.get("count") if isinstance(f.get("count"), int) else None,
                             each=f.get("each") if isinstance(f.get("each"), bool) else None,
                             period=f.get("period"), mandatory=f.get("mandatory")
                             if isinstance(f.get("mandatory"), bool) else None, name=f.get("name")))
    return out
