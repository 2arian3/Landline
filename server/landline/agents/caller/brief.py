"""The Caller's brief for one listing (A-05): its checklist and its instructions, built in code at dial time.

The checklist is what the listing doesn't say (its unknown fields, the most useful first, at most
MAX_GAPS so a call stays short), the renter's extra questions, and a viewing. The instructions carry what
the listing says, the checklist, the renter's viewing windows, and only the answers the renter agreed to
share (`CallPlan.shareable`). Nothing about the renter reaches the Caller any other way: nothing is
injected mid-call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from landline.agents.caller import slots
from landline.contracts import CallPlan, Listing

MAX_GAPS = 3   # besides availability, the renter's questions and the viewing


@dataclass
class Item:
    key: str                  # "available", "parking", "q1", "viewing", ...
    ask: str                  # what to find out, in a few words
    fields: list[str]         # the record_fact fields that cover it
    done: bool = False


@dataclass
class Brief:
    listing: Listing
    plan: CallPlan
    items: list[Item] = field(default_factory=list)

    def missing(self) -> list[Item]:
        return [i for i in self.items if not i.done]

    def covered(self, fact_field: str, value: str = "") -> None:
        """Mark the items a recorded fact answers. An `other` fact answers one thing: the viewing when it
        is about viewing times, else the first of the renter's questions still open."""
        if fact_field != "other":
            for item in self.items:
                if fact_field in item.fields:
                    item.done = True
            return
        viewing = _about_viewing(value)
        for item in self.items:
            if item.done or "other" not in item.fields or (item.key == "viewing") != viewing:
                continue
            item.done = True
            return


def brief(listing: Listing, plan: CallPlan) -> Brief:
    items = [Item("available", "whether it's still available, and from when",
                  ["available", "available_from"])]
    gaps: list[Item] = []
    pets = plan.shareable.pets
    if pets or listing.pets == "unknown":
        ask = f"whether pets are allowed (the renter has {_join([f'a {p}' for p in pets])})" if pets \
            else "whether pets are allowed"
        gaps.append(Item("pets", ask, ["pets"]))
    if listing.parking == "unknown" or (listing.parking == "extra" and listing.parking_cost is None):
        gaps.append(Item("parking", "whether parking comes with it, and what it costs a month",
                         ["parking", "parking_cost"]))
    if not listing.utilities_included:
        gaps.append(Item("utilities", "which utilities are included in the rent", ["utilities_included"]))
    if listing.laundry == "unknown":
        gaps.append(Item("laundry", "where the laundry is", ["laundry"]))
    gaps.append(Item("fees", "any monthly fees on top of the rent", ["fees"]))
    items += gaps[:MAX_GAPS]
    for n, question in enumerate(plan.questions, start=1):
        items.append(Item(f"q{n}", f"the renter's question: {question} (record their answer as other)",
                          ["other"]))
    if plan.may_book and plan.windows:
        items.append(Item("viewing", "a viewing inside the renter's times, booked with book_viewing", []))
    else:
        items.append(Item("viewing", "which viewing times they could offer (record them as other)",
                          ["other"]))
    return Brief(listing, plan, items)


def instructions(b: Brief) -> str:
    x, plan = b.listing, b.plan
    checklist = "\n".join(f"{n}. Find out {i.ask}." for n, i in enumerate(b.items, start=1))
    windows = _join([_window(w) for w in plan.windows]) or "none given"
    share = _shareable(plan)
    booking = ("You may book one viewing: call book_viewing with the time they offer, in their words. "
               "Book only a time inside the renter's windows; the reply tells you if it's booked.") \
        if plan.may_book and plan.windows else \
        "You may not book anything. Ask which viewing times they could offer and record them as other."
    return CALLER.format(address=_place(x), listing=_listing(x), checklist=checklist, windows=windows,
                         share=share, booking=booking)


CALLER = """\
You are Landline, an AI assistant phoning the landlord or listing agent of a rental in Toronto for a \
renter. You work for the renter. The other person speaks first when they pick up.

How you speak:
- Your first words, after they answer, are: "Hi, I'm an AI assistant calling for a renter about \
{address}." Then ask your first question.
- One short question at a time, at most two short sentences per turn. This is a phone call: no lists, \
no markdown.
- Speak the language they speak, and switch when they switch. Tool arguments are always in English, with \
digits.
- Never narrate your tools. Call the tool, then speak.

Your tools are the only way anything is saved. Talking saves nothing: whatever they tell you is lost \
unless you record it, and a viewing isn't booked until book_viewing says so. In every turn, call your \
tools first, then speak:
- They answer something on your checklist → record_fact. If they tell you several things at once, call \
record_fact once for each. Then start your reply by repeating every number, price, date or time they just \
gave, as a statement ("So parking is $150 a month."), even when you also answer a question of theirs. The \
tool's reply gives you the words.
- They answer one of the renter's questions → record_fact as other, with their answer. An answer you \
didn't record doesn't count, and the call can't end until it's recorded.
- They offer a viewing time inside the renter's times → book_viewing with their words, then confirm it.
- They ask something you can't answer → note_question, then say you'll check with your client.
- They want money, a deposit or an e-transfer before anyone has seen the unit, or can't show it in \
person → record_fact as other, then say you'll pass it on to your client, thank them, and call end_call \
with done, all in that same turn. Don't argue and don't book.
- Never ask the same thing twice. If they won't answer, move on.

For example, they say "Parking is one spot, $150 a month, and there's in-suite laundry." You call \
record_fact (field parking_cost, value "$150 a month", quote "Parking is one spot, $150 a month"), then \
record_fact (field laundry, value "in-suite", quote "there's in-suite laundry"), then you say: "So parking \
is $150 a month, and laundry is in the unit. Are pets allowed?"

What you may say about the renter: {share} Nothing else. If they ask anything else (income, credit, \
references, a deposit, documents), say "I'll check with my client and get back to you," and call \
note_question with their question. Never agree to send money or anything personal, and never promise \
anything for the renter.

What the listing says: {listing}

Your checklist, in order (skip what they have already told you):
{checklist}

The renter can view: {windows}. {booking}

Your tools:
- record_fact, as soon as they say something on the checklist: field, value in a few words, and quote: \
their exact words, copied from what they said.
- note_question, for a question you can't answer.
- book_viewing, with the time they offered, in their words, like "Saturday at 2 pm".
- end_call. First thank them and say goodbye, then call it: done once the checklist is covered (or they \
don't know the rest), refused if they won't talk, wrong_number, or voicemail. On a voicemail, leave one \
short message ("Hi, this is an AI assistant calling for a renter about {address}. I'll try again \
later.") and end with voicemail.
"""


def _place(x: Listing) -> str:
    return f"{x.address}, {x.unit_label}" if x.unit_label else x.address


def _listing(x: Listing) -> str:
    beds = "a studio" if x.beds == 0 else f"{x.beds:g} bedroom{'s' if x.beds != 1 else ''}"
    said = [f"{_place(x)}, {beds} for ${x.rent:,} a month"]
    if x.available_from:
        said.append(f"available from {x.available_from:%B} {x.available_from.day}")
    if x.parking not in ("unknown",):
        said.append(f"parking: {x.parking}" + (f" at ${x.parking_cost:,} a month" if x.parking_cost else ""))
    if x.pets != "unknown":
        said.append(f"pets: {x.pets.replace('_', ' ')}")
    if x.laundry != "unknown":
        said.append(f"laundry: {x.laundry.replace('_', ' ')}")
    if x.utilities_included:
        said.append(f"includes {_join(x.utilities_included)}")
    return "; ".join(said) + "."


def _shareable(plan: CallPlan) -> str:
    s = plan.shareable
    said = [f"they want to move in {s.move_in}" if s.move_in else None,
            (f"{s.occupants} {'person' if s.occupants == 1 else 'people'} would live there"
             if s.occupants else None),
            f"they have {_join([f'a {p}' for p in s.pets])}" if s.pets else None]
    said = [x for x in said if x]
    if not said:
        return "nothing yet."
    text = _join(said)
    return text[0].upper() + text[1:] + "."


def _window(w) -> str:
    start, end = w.start.astimezone(slots.TORONTO), w.end.astimezone(slots.TORONTO)
    return f"{slots.spoken(start)} to {slots.spoken(end).split(' at ')[1]}"


_DAY = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b|\btoday\b|\btomorrow\b|\bweekend\b", re.I)
_CLOCK = re.compile(r"\b\d{1,2}(:\d{2})?\s*(am|pm|a\.m\.|p\.m\.)|\bnoon\b|\b\d{1,2}:\d{2}\b", re.I)


def _about_viewing(value: str) -> bool:
    """A viewing time: viewing words, or a day and a time ("Thursday at 6:30 pm"). A time alone ("quiet
    after 10 pm") answers something else."""
    v = value.lower()
    words = any(w in v for w in ("view", "visit", "show", "tour", "see it", "open house"))
    return words or bool(_DAY.search(v) and _CLOCK.search(v))


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
