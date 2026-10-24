"""The Caller's brief for one listing (A-05): its checklist and its instructions, built in code at dial time.

The checklist follows the renter, not the holes in our data: whether it's still available, the monthly
fees, pets only when the renter has them, parking, utilities or laundry only when the renter asked for
them and the listing doesn't say (at most MAX_GAPS of these, so a call stays short), the renter's own
questions, and a viewing when the renter allowed booking. The instructions carry what
the listing says, the checklist, the renter's viewing windows, and only the answers the renter agreed to
share (`CallPlan.shareable`). Nothing about the renter reaches the Caller any other way: nothing is
injected mid-call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from landline.agents.caller import slots
from landline.contracts import CallPlan, Listing, Preferences
from landline.scoring.weights import feature_key

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


def brief(listing: Listing, plan: CallPlan, wants: frozenset[str] = frozenset()) -> Brief:
    """`wants`: what the renter asked for that a call can check (`wants()`); the plan doesn't carry it."""
    items = [Item("available", "whether it's still available, and from when",
                  ["available", "available_from"])]
    gaps: list[Item] = []
    if pets := plan.shareable.pets:   # even when the listing says, so the landlord hears what they have
        has = _join([f"a {p}" for p in pets])
        gaps.append(Item("pets", f"whether pets are allowed (the renter has {has})", ["pets"]))
    if "parking" in wants and (listing.parking == "unknown"
                               or (listing.parking == "extra" and listing.parking_cost is None)):
        gaps.append(Item("parking", "whether parking comes with it, and what it costs a month",
                         ["parking", "parking_cost"]))
    if "utilities" in wants and not listing.utilities_included:
        gaps.append(Item("utilities", "which utilities are included in the rent", ["utilities_included"]))
    if "laundry" in wants and listing.laundry == "unknown":
        gaps.append(Item("laundry", "where the laundry is", ["laundry"]))
    # Fees change what the place costs, which every renter cares about, so they always stay.
    items += [*gaps[:MAX_GAPS - 1], Item("fees", "any monthly fees on top of the rent", ["fees"])]
    for n, question in enumerate(plan.questions, start=1):
        items.append(Item(f"q{n}", f"the renter's question: {question} (record their answer as other)",
                          ["other"]))
    if plan.may_book and plan.windows:
        items.append(Item("viewing", "a viewing inside the renter's times, booked with book_viewing", []))
    return Brief(listing, plan, items)


def wants(prefs: Preferences | None) -> frozenset[str]:
    """What the renter named as a must-have or a priority that a call can check: parking, utilities,
    laundry."""
    out: set[str] = set()
    for said in [*prefs.must_have, *prefs.priorities] if prefs else []:
        word, key = said.lower(), feature_key(said)
        if key == "parking" or "parking" in word:
            out.add("parking")
        if key == "laundry" or "laundry" in word or "washer" in word:
            out.add("laundry")
        if re.search(r"utilit|heat|hydro|water|electric", word):
            out.add("utilities")
    return frozenset(out)


def instructions(b: Brief) -> str:
    x, plan = b.listing, b.plan
    checklist = "\n".join(f"{n}. Find out {i.ask}." for n, i in enumerate(b.items, start=1))
    windows = _join([_window(w) for w in plan.windows]) or "none given"
    share = _shareable(plan)
    booking = ("You may book one viewing: call book_viewing with the time they offer, in their words. "
               "Book only a time inside the renter's windows; the reply tells you if it's booked.") \
        if plan.may_book and plan.windows else \
        "You may not book anything, so don't ask about viewings."
    return CALLER.format(address=_place(x), listing=_listing(x), checklist=checklist, windows=windows,
                         share=share, booking=booking)


def disclosure(b: Brief) -> str:
    """The Caller's first words (hard rule 6), as CALLER gives them."""
    return f"Hi, I'm an AI assistant calling for a renter about {_place(b.listing)}."


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
- They say when it's free → record_available_from, not record_fact: fill in the parts of what they \
said (kind, and the weekday, weeks, month or day), never a date you work out. Read back the day its \
reply gives.
- They answer "any monthly fees?" with just yes or no → record_fact fees with their words anyway; the reply \
says what to ask next.
- They answer one of the renter's questions → record_fact as other, with their answer. An answer you \
didn't record doesn't count, and the call can't end until it's recorded.
- They offer a viewing time inside the renter's times → book_viewing with their words, then confirm it.
- Say a viewing is booked, or "let me book that", only after book_viewing's reply says it's booked.
- book_viewing says a time can't be booked → offer one of the times its reply gives, in one short \
sentence. Never repeat an apology or a question you've already said.
- They ask about the renter → never skip it, even to ask your own next question. Answer only from what \
you may say about the renter (below); for anything else, call note_question with their question, then \
say "I'll check with my client" before you carry on.
- They ask something else you can't answer → note_question, then say you'll check with your client.
- They want money, a deposit or an e-transfer before anyone has seen the unit, or can't show it in \
person → record_fact as other, then say you'll pass it on to your client, thank them, and call end_call \
with done, all in that same turn. Don't argue and don't book.
- Never ask the same thing twice. If they won't answer, move on.
- The call is over once the checklist is covered, a viewing is booked or can't be, it sounds like a scam, \
or they say goodbye. Then say one short thank-you and goodbye and call end_call in that same turn. Ask \
nothing more, and never leave the line open: the call only ends when you call end_call.

For example, they say "Parking is one spot, $150 a month, and there's in-suite laundry." You call \
record_fact (field parking_cost, value "$150 a month", quote "Parking is one spot, $150 a month"), then \
record_fact (field laundry, value "in-suite", quote "there's in-suite laundry"), then you say: "So parking \
is $150 a month, and laundry is in the unit. Are there any other monthly fees?"

What you may say about the renter: {share} Nothing else. If they ask anything else (income, work, a \
guarantor, credit, references, a deposit, documents, or whether they'd want a paid extra like a locker), \
call note_question with their question and say "I'll check with my client and get back to you." Never \
agree to send money or anything personal, and never promise \
anything for the renter. Never say the renter confirmed, agreed or chose anything during the call: you \
can't reach them.

What the listing says: {listing}

Your checklist, in order (skip what they have already told you). Ask nothing that isn't on it:
{checklist}

The renter can view: {windows}. {booking}

Your tools:
- record_fact, as soon as they say something on the checklist: field, value in a few words, and quote: \
their exact words, copied from what they said.
- note_question, for a question you can't answer.
- book_viewing, with the time they offered, in their words, like "Saturday at 2 pm".
- end_call. Say goodbye, then call it in the same turn: done once the checklist is covered (or they \
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
