"""Turning the renter's words into a card on the board, or a point on the map.

The Concierge passes words ("the second one", "Markham", "Bloor"); these functions find what they mean.
Code does this, never the model, so an id that reaches the runtime is always one the board or the
catalogue holds (hard rule 2). Pure functions: no I/O, nothing to await.
"""
from __future__ import annotations

import re
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


def resolve(ref: str | None, board: list[Card]) -> Card | None:
    """The card `ref` names: address words first (the best unique match), else a rank ("2", "second",
    "number two", "last"). None when nothing or more than one card fits, so the Concierge asks which."""
    words = _words(ref or "")
    if not words or not board:
        return None
    meaningful = [w for w in words if w not in FILLER and w not in ORDINALS]
    bare_rank = len(meaningful) == 1 and meaningful[0].isdigit() and int(meaningful[0]) <= len(board)
    if meaningful and not bare_rank:   # "2" alone is a rank; "2 Erskine" is an address
        scores = [(len(set(meaningful) & _card_words(c)), c) for c in board]
        best = max(s for s, _ in scores)
        hits = [c for s, c in scores if s == best]
        if best > 0 and len(hits) == 1:
            return hits[0]
    rank = _rank(words, len(board))
    return board[rank - 1] if rank else None


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
