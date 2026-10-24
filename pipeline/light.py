"""The light estimate (D-07, docs/SCORING.md §6): how much natural light a unit gets, from three clues.

    light = 0.35 · floor_term + 0.35 · text_term + 0.30 · photo_term, at most 0.25 for a basement

- floor_term from the floor the description states ("14th floor", a basement suite, a penthouse):
  basement 0.1, ground 0.35, 2–4 0.5, 5–9 0.7, 10+ 0.85;
- text_term starts at 0.5 and moves ±0.15 per light word ("south-facing", "floor-to-ceiling windows",
  "north-facing"), clamped to 0–1;
- photo_term is where the listing's photos rank by brightness among every listing's photos (0–1).

A clue that isn't there counts 0.5: no evidence either way. Floor and words are found by rules, each with
the exact words they came from, so every value is grounded by construction and no model is involved. For
the photos, liv.rent's image service shrinks each one to a single pixel, its mean colour; only that
brightness is kept. The card always says the light score is estimated (live-map-ui).

    python -m pipeline light --photos 2 --load     # rules + 2 photos a listing, then into $DATABASE_URL
"""
from __future__ import annotations

import json
import re
import struct
import zlib
from bisect import bisect_left
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

VERSION = "light-v1"
PIXEL = "https://cdn.liv.rent/1x1/filters:format(png)/{key}"
_KEY = re.compile(r"^https://cdn\.liv\.rent/[^/]+/(?:filters:[^/]+/)?(.+)$")
QUOTE_MAX = 140

FLOOR_TERMS = ((-1, 0.10), (0, 0.35), (1, 0.35), (4, 0.50), (9, 0.70))   # (up to floor, term); above: 0.85
HIGH = 0.85
STEP = 0.15
BASEMENT_CAP = 0.25

# A floor the text states. A basement only when it is the unit ("basement suite"), not a locker or laundry.
_FLOOR_N = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)[- ]floor\b", re.I)
_BASEMENT = re.compile(r"\b(semi[- ]basement|basement (?:unit|apartment|suite|level unit)|"
                       r"lower[- ]level (?:unit|apartment|suite))\b", re.I)
_GROUND = re.compile(r"\b(ground[- ]floor (?:unit|apartment|suite)|main[- ]floor (?:unit|apartment|suite))\b",
                     re.I)
_PENTHOUSE = re.compile(r"\bpenthouse\b", re.I)

# Words about light. Each kind counts once, however often the text repeats it.
CUES: tuple[tuple[str, int, re.Pattern], ...] = (
    ("south- or west-facing", +1,
     re.compile(r"\b(?:south|west|south[- ]?west|south[- ]?east)[- ]facing\b", re.I)),
    ("floor-to-ceiling windows", +1, re.compile(r"\bfloor[- ]to[- ]ceiling windows\b", re.I)),
    ("large windows", +1, re.compile(r"\b(?:large|big|oversized|huge|expansive) windows\b", re.I)),
    ("corner unit", +1, re.compile(r"\bcorner (?:unit|suite|apartment)\b", re.I)),
    ("skylight", +1, re.compile(r"\bskylights?\b", re.I)),
    ("bright", +1, re.compile(r"\b(?:bright|sun[- ]?(?:filled|lit|drenched)|light[- ]filled|"
                              r"(?:abundant|plenty of|lots of|ample) (?:natural )?(?:sun)?light|"
                              r"natural light)\b", re.I)),
    ("north-facing", -1, re.compile(r"\bnorth[- ]facing\b", re.I)),
    ("little light", -1, re.compile(r"\b(?:limited|little|no) (?:natural )?light\b|\bno windows\b", re.I)),
)


@dataclass
class Clues:
    listing_id: str
    floor: int | None = None
    floor_quote: str | None = None
    cues: list[dict] = field(default_factory=list)       # {cue, sign, quote}
    luminance: float | None = None                         # mean of the photos' relative luminance, 0–1
    photos: int = 0


def read_clues(listing_id: str, text: str | None, unit_type: str | None) -> Clues:
    """The floor and light words in a description, each with the sentence it came from."""
    c = Clues(listing_id)
    text = text or ""
    if unit_type == "basement":
        c.floor, c.floor_quote = -1, None   # the listing's own unit type: scraped, needs no quote
    elif m := _BASEMENT.search(text):
        c.floor, c.floor_quote = -1, _sentence(text, m)
    elif m := _FLOOR_N.search(text):
        n = int(m.group(1))
        if 1 <= n <= 80:
            c.floor, c.floor_quote = n, _sentence(text, m)
    elif m := _GROUND.search(text):
        c.floor, c.floor_quote = 0, _sentence(text, m)
    elif m := _PENTHOUSE.search(text):
        c.floor, c.floor_quote = None, _sentence(text, m)   # high, but which floor isn't said
    for name, sign, pattern in CUES:
        if m := pattern.search(text):
            c.cues.append({"cue": name, "sign": sign, "quote": _sentence(text, m)})
    return c


def floor_term(c: Clues) -> float:
    if c.floor is None:
        return HIGH if c.floor_quote else 0.5   # a penthouse with no number, or nothing said
    for up_to, term in FLOOR_TERMS:
        if c.floor <= up_to:
            return term
    return HIGH


def text_term(c: Clues) -> float:
    return min(1.0, max(0.0, 0.5 + STEP * sum(cue["sign"] for cue in c.cues)))


def score(c: Clues, photo_rank: float | None) -> tuple[float, dict]:
    """light_score and light_basis (what the card's "estimated from …" line is built from)."""
    f, t = floor_term(c), text_term(c)
    p = 0.5 if photo_rank is None else photo_rank
    light = 0.35 * f + 0.35 * t + 0.30 * p
    if c.floor == -1:
        light = min(light, BASEMENT_CAP)
    basis = {
        "floor": None if c.floor is None and not c.floor_quote else
        {"value": c.floor, "term": f, "quote": c.floor_quote},
        "cues": c.cues,
        "photos": None if c.luminance is None else
        {"n": c.photos, "luminance": round(c.luminance, 4), "rank": round(p, 4)},
        "version": VERSION,
    }
    return round(light, 4), basis


def photo_ranks(clues: Iterable[Clues]) -> dict[str, float]:
    """Each listing's photo brightness as its rank among every listing with photos: 0 darkest, 1 brightest."""
    known = sorted(c.luminance for c in clues if c.luminance is not None)
    if len(known) < 2:
        return {}
    top = len(known) - 1
    return {c.listing_id: bisect_left(known, c.luminance) / top for c in clues if c.luminance is not None}


# Photos


def photo_key(url: str) -> str | None:
    m = _KEY.match(url)
    return m.group(1) if m else None


def luminance(png: bytes) -> float:
    """Relative luminance (0–1) of a 1×1 PNG: grey, RGB or palette, 8 bits, any alpha ignored."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, chunks = 8, {}
    while pos < len(png):
        (size,), kind = struct.unpack(">I", png[pos:pos + 4]), png[pos + 4:pos + 8]
        chunks.setdefault(kind, b"")
        chunks[kind] += png[pos + 8:pos + 8 + size]
        pos += 12 + size
    width, height, depth, colour = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    if (width, height, depth) != (1, 1, 8):
        raise ValueError(f"expected a 1×1 8-bit PNG, got {width}×{height} at {depth} bits")
    row = zlib.decompress(chunks[b"IDAT"])[1:]   # one filter byte; for a single pixel every filter is a no-op
    if colour == 3:
        i = row[0] * 3
        r, g, b = chunks[b"PLTE"][i:i + 3]
    elif colour in (2, 6):
        r, g, b = row[:3]
    elif colour in (0, 4):
        r = g = b = row[0]
    else:
        raise ValueError(f"unknown PNG colour type {colour}")
    return (0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)) ** (1 / 2.2)


def _linear(v: int) -> float:
    s = v / 255
    return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4


def measure_photos(clues: dict[str, Clues], photos: dict[str, list[str]], fetch: Callable[[str], bytes],
                   per_listing: int, log: Callable[[str], None] = print) -> None:
    """Fill each listing's luminance from its first `per_listing` photos, one pixel each."""
    done = 0
    for lid, urls in photos.items():
        values = []
        for url in urls[:per_listing]:
            key = photo_key(url)
            if not key:
                continue
            try:
                values.append(luminance(fetch(PIXEL.format(key=key))))
            except Exception as e:   # a missing or odd photo: skip it, keep the others
                log(f"  {lid}: photo skipped ({e})")
        if values and lid in clues:
            clues[lid].luminance, clues[lid].photos = sum(values) / len(values), len(values)
        done += 1
        if done % 50 == 0:
            log(f"  photos: {done}/{len(photos)} listings")


# Rows and loading


def rows(clues: dict[str, Clues], now: datetime | None = None) -> list[dict]:
    """One row per listing for data/stages/light/*.jsonl and for the database."""
    at = (now or datetime.now(UTC)).isoformat()
    ranks = photo_ranks(clues.values())
    out = []
    for lid, c in clues.items():
        light, basis = score(c, ranks.get(lid))
        provenance = {"light_score": {"source": "derived", "at": at, "version": VERSION}}
        if c.floor is not None:
            source = "derived" if c.floor_quote else "scraped"
            provenance["floor"] = {"source": source, "quote": c.floor_quote, "at": at, "version": VERSION}
        out.append({"listing_id": lid, "floor": c.floor, "light_score": light, "light_basis": basis,
                    "provenance": provenance})
    return out


UPDATE = """
update listings set floor = coalesce(%(floor)s, floor), light_score = %(light_score)s,
  light_basis = %(light_basis)s, provenance = provenance || %(provenance)s
where listing_id = %(listing_id)s
"""


def load(out: list[dict], database_url: str) -> int:
    import psycopg
    from psycopg.types.json import Jsonb

    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.executemany(UPDATE, [{**r, "light_basis": Jsonb(r["light_basis"]),
                                  "provenance": Jsonb(r["provenance"])} for r in out])
        return cur.rowcount


def write(out: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in out))
    return path


def _sentence(text: str, m: re.Match) -> str:
    """The sentence around a match, as written, cut to QUOTE_MAX characters around it."""
    start = max(text.rfind(c, 0, m.start()) for c in ".!?\n") + 1
    ends = [i for i in (text.find(c, m.end()) for c in ".!?\n") if i >= 0]
    end = min(ends) + 1 if ends else len(text)
    quote = text[start:end].strip()
    if len(quote) > QUOTE_MAX:
        lo = max(0, m.start() - start - QUOTE_MAX // 2)
        quote = quote[lo:lo + QUOTE_MAX].strip()
    return quote
