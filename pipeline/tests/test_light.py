"""The light estimate (D-07): rules with quotes, the one-pixel photos, the score, and reading descriptions."""
import struct
import zlib

import pytest

from pipeline import light
from pipeline.sources import livrent


def _png(colour: int, pixel: bytes, palette: bytes | None = None) -> bytes:
    """A 1×1 8-bit PNG, as liv.rent's image service sends one."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, colour, 0, 0, 0)
    body = chunk(b"IHDR", ihdr) + (chunk(b"PLTE", palette) if palette else b"")
    return b"\x89PNG\r\n\x1a\n" + body + chunk(b"IDAT", zlib.compress(b"\x00" + pixel)) + chunk(b"IEND", b"")


def test_one_pixel_photos_decode_to_their_brightness():
    white, black = _png(2, b"\xff\xff\xff"), _png(2, b"\x00\x00\x00")
    assert light.luminance(white) == pytest.approx(1.0) and light.luminance(black) == 0.0
    warm = _png(3, b"\x00", palette=bytes([123, 109, 91]))   # the palette PNG the service actually sends
    assert light.luminance(warm) == pytest.approx(light.luminance(_png(2, bytes([123, 109, 91]))))
    assert 0.4 < light.luminance(warm) < 0.5
    assert light.luminance(_png(6, b"\x80\x80\x80\xff")) == light.luminance(_png(0, b"\x80"))
    with pytest.raises(ValueError):
        light.luminance(b"not a png")


def test_photo_keys_come_from_the_listing_photo_urls():
    url = "https://cdn.liv.rent/800x/filters:quality(70)/static_files/unit/1/unit_1_a.png"
    assert light.photo_key(url) == "static_files/unit/1/unit_1_a.png"
    assert light.photo_key("https://example.com/x.png") is None


@pytest.mark.parametrize(("text", "unit_type", "floor"), [
    ("Rarely available on the 28th floor. Great views.", "condo", 28),
    ("Bright 1-bedroom basement suite with a separate entrance.", "apartment", -1),
    ("Lower-level unit, freshly painted.", "apartment", -1),
    ("Coin laundry in the basement of the building.", "apartment", None),   # the laundry, not the unit
    ("Ground floor unit with a patio.", "apartment", 0),
    ("A cozy place.", "basement", -1),                                       # the listing's own unit type
])
def test_the_floor_comes_from_what_the_text_says(text, unit_type, floor):
    assert light.read_clues("t:1", text, unit_type).floor == floor


def test_light_words_are_kept_with_the_exact_sentence():
    text = ("Spacious south-facing suite. Floor-to-ceiling windows throughout! Walk to the subway.\n"
            "The bedroom is north-facing.")
    c = light.read_clues("t:1", text, "condo")
    assert [(q["cue"], q["sign"], q["quote"]) for q in c.cues] == [
        ("south- or west-facing", 1, "Spacious south-facing suite."),
        ("floor-to-ceiling windows", 1, "Floor-to-ceiling windows throughout!"),
        ("north-facing", -1, "The bedroom is north-facing."),
    ]
    assert all(q["quote"] in text for q in c.cues)
    assert light.text_term(c) == pytest.approx(0.65)


def test_the_score_follows_the_formula_and_caps_a_basement():
    nothing = light.read_clues("t:1", "", "condo")
    assert light.score(nothing, None)[0] == 0.5                     # no evidence either way
    high = light.read_clues("t:2", "Sun-filled corner unit on the 14th floor.", "condo")
    value, basis = light.score(high, photo_rank=1.0)
    assert value == pytest.approx(0.35 * 0.85 + 0.35 * 0.8 + 0.30 * 1.0)
    assert basis["floor"] == {"value": 14, "term": 0.85, "quote": "Sun-filled corner unit on the 14th floor."}
    cellar = light.read_clues("t:3", "Bright basement apartment with large windows.", "apartment")
    assert light.score(cellar, photo_rank=1.0)[0] == light.BASEMENT_CAP


def test_photo_brightness_is_ranked_against_every_listing():
    clues = [light.Clues(f"t:{i}", luminance=v) for i, v in enumerate([0.2, 0.5, 0.8])]
    clues.append(light.Clues("t:9"))   # no photos: no rank
    assert light.photo_ranks(clues) == {"t:0": 0.0, "t:1": 0.5, "t:2": 1.0}


def test_measuring_photos_skips_a_bad_one_and_averages_the_rest():
    clues = {"t:1": light.Clues("t:1")}
    urls = [f"https://cdn.liv.rent/800x/{name}.png" for name in "abc"]
    pixels = {"a.png": _png(2, b"\xff\xff\xff"), "b.png": b"broken"}
    light.measure_photos(clues, {"t:1": urls}, lambda url: pixels[url.rsplit("/", 1)[1]], per_listing=2,
                         log=lambda _: None)
    # b.png is broken and c.png is past the limit of two: only a.png counts.
    assert (clues["t:1"].luminance, clues["t:1"].photos) == (pytest.approx(1.0), 1)


def test_descriptions_follow_references_to_rows_that_run_together():
    # Two text rows back to back with no newline between them, the first ending in a hex digit.
    stream = ('1:{"__typename":"FullListing","listing_id":11,"description":"$68"}\n'
              '2:{"__typename":"FullListing","listing_id":12,"description":"$6a"}\n'
              '3:{"__typename":"FullListing","listing_id":13,"description":"Short and plain."}\n'
              '68:T9,Sunny 7th6a:T10,Café on the 2nd\n')   # lengths in bytes: é is two
    html = '<script>self.__next_f.push([1,' + _json(stream) + '])</script>'
    assert livrent.descriptions(html) == {"11": "Sunny 7th", "12": "Café on the 2nd",
                                          "13": "Short and plain."}


def _json(s: str) -> str:
    import json
    return json.dumps(s)
