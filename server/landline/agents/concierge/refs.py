"""Turning the renter's words into a card on the board, or a point on the map.

The Concierge passes words ("the second one", "Markham", "Bloor"); these functions find what they mean.
Code does this, never the model, so an id that reaches the runtime is always one the board or the
catalogue holds (hard rule 2). Pure functions: no I/O, nothing to await.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import fmean

from landline.contracts import Card
from landline.db.catalogue import Catalogue

ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
}
# Words that say nothing about which listing: left out before matching addresses.
FILLER = {
    "the", "a", "an", "one", "on", "at", "in", "of", "that", "this", "number", "no", "listing", "place",
    "apartment", "unit", "condo", "house", "st", "street", "ave", "avenue", "rd", "road", "dr", "drive",
    "blvd", "e", "w", "east", "west", "and", "near", "around", "show", "me",
}


@dataclass
class Match:
    """What a ref names: one card, or the cards it can't choose between (`options`, in board order)."""
    card: Card | None = None
    options: list[Card] = field(default_factory=list)


def resolve(ref: str | None, board: list[Card], focus_id: str | None = None) -> Card | None:
    return match(ref, board, focus_id).card


def match(ref: str | None, board: list[Card], focus_id: str | None = None) -> Match:
    """The card `ref` names: address words first (the best unique match), else a rank ("2", "second",
    "number two", "last"). When the address words fit several cards equally ("30 Carabob Court" on two
    cards with no unit), a rank, a price or the focused card in `ref`'s context picks one of them; else
    the Match carries them as options, so the Concierge asks which. Never the first of them by default:
    a wrong card here gets a stranger phoned."""
    words = _words(ref or "")
    if not words or not board:
        return Match()
    meaningful = [w for w in words if w not in FILLER and w not in ORDINALS]
    bare_rank = len(meaningful) == 1 and meaningful[0].isdigit() and int(meaningful[0]) <= len(board)
    if meaningful and not bare_rank:   # "2" alone is a rank; "2 Erskine" is an address
        scores = [(len(set(meaningful) & _card_words(c)), c) for c in board]
        best = max(s for s, _ in scores)
        hits = [c for s, c in scores if s == best]
        if best > 0 and len(hits) == 1:
            return Match(hits[0])
        if best > 0:
            return _between(hits, words, board, focus_id)
    rank = _rank(words, len(board))
    return Match(board[rank - 1]) if rank else Match()


def _between(hits: list[Card], words: list[str], board: list[Card], focus_id: str | None) -> Match:
    """Picks among cards the address words fit equally, from what else `words` say: a rank on the board
    that is one of them ("30 Carabob Court, number 5"), or a price only one of them has ("the $1,950
    one"). Then the focused card, if it is one of them: the renter is talking about it."""
    address = set().union(*(_card_words(c) for c in hits))
    numbers = [int(w) for w in words if w.isdigit() and w not in address]
    ranks = [ORDINALS[w] for w in words if w in ORDINALS] + numbers
    if "last" in words:
        ranks.append(len(board))
    by_rank = [board[r - 1] for r in ranks if 1 <= r <= len(board) and board[r - 1] in hits]
    if by_rank:
        return Match(by_rank[0])
    by_price = [c for c in hits if {c.monthly_cost, c.rent} & set(numbers)]
    if len(by_price) == 1:
        return Match(by_price[0])
    focused = [c for c in hits if c.listing_id == focus_id]
    if focused:
        return Match(focused[0])
    return Match(options=by_price or hits)


def top(board: list[Card], n: int) -> list[Card]:
    return board[:n]


def place(label: str, catalogue: Catalogue) -> tuple[float, float] | None:
    """Where `label` is on the map: the centre of the catalogue listings whose neighbourhood, building or
    street name holds every word of it ("Bloor", "Thorncliffe Park"). None when nothing matches; the
    geocoder (D-04) will place intersections and landmarks."""
    wanted = {w for w in _words(label) if w not in FILLER and not w.isdigit()}
    if not wanted:
        return None
    points = [(x.lat, x.lng) for x in catalogue.listings
              if x.lat is not None and x.lng is not None
              and wanted <= _place_words(x.neighbourhood, x.building_name, x.address)]
    if not points:
        return None
    return fmean(p[0] for p in points), fmean(p[1] for p in points)


def _rank(words: list[str], size: int) -> int | None:
    picks = [ORDINALS[w] for w in words if w in ORDINALS]
    picks += [int(w) for w in words if w.isdigit()]
    if "last" in words:
        picks.append(size)
    return picks[0] if picks and 1 <= picks[0] <= size else None


def _card_words(card: Card) -> set[str]:
    return set(_words(" ".join(filter(None, (card.address, card.unit_label, card.neighbourhood)))))


def _place_words(*parts: str | None) -> set[str]:
    return {w for w in _words(" ".join(filter(None, parts))) if not w.isdigit()}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("#", " "))
