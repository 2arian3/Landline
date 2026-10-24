# Scoring

Owner: Arian (implementation). Danial (evals).

The Matcher is code, not a model: hard filters, then a weighted sum of per-feature utilities, then adjustments for verification. Every rank comes with its top reasons, so the Concierge can say *why* and the card can show it. The same inputs always give the same order. Ties break by previous rank, so cards never jitter between updates.

## 1. Hard filters

A listing is out if any of these hold. The renter can relax any of them by voice ("I could go to 3,600").

| Filter | Rule |
|---|---|
| Budget | `monthly_cost > max_monthly × 1.10` |
| Beds | `beds + 0.5·dens < min_beds` |
| Pets | renter has a dog and `pets ∈ {none, cats_only, small_only}` with a quote from a call or the City record (a listing that says nothing is kept and flagged as a question) |
| Must-haves | each `must_have` (e.g. `parking`, `laundry_in_suite`, `step_free`) is false *with evidence* |
| Dead | `verified = dead` (already leased) |
| Rejected | the renter rejected it before, unless they ask to see it again |
| Rooms | `unit_type = room`, unless `must_have` includes `room` |

"With evidence" means the value's provenance is a call or the City record, or a call found it (an override). The listing's own words only rank it. The pets rule covers any pet the place refuses: a dog at `none`, `cats_only` or `small_only`, a cat at `none` or `dogs_only`.

## 2. Utilities (each 0–1)

| Feature | Utility |
|---|---|
| Cost | 1 if `monthly_cost ≤ 0.85·budget`; linear to 0.4 at budget; linear to 0 at budget × 1.10 |
| Location | per anchor: `exp(-d_km / 2.5)`; if neighbourhoods were named: 1 inside, 0.6 adjacent, else `exp(-d_km_to_area / 2)` |
| Transit | `clamp(1 − (walk_min − 3) / 17)` |
| Size | `clamp(sqft / desired_sqft)` if a size was given, else rises gently with sqft by beds |
| Light | `light_score` |
| Floor | if they said "high floor": `clamp(floor / 15)`; "ground floor / no stairs": `1` for floor ≤ 1 or an elevator |
| Parking | needed: included 1, extra 0.6, none 0; not needed: 0.5 for everyone (neutral) |
| Pets | compatible 1, case-by-case 0.6, unknown 0.5 |
| Laundry / AC / dishwasher / outdoor space | 1 if present, 0.5 unknown, 0 absent; only weighted when mentioned |
| Building quality | `building_eval_score / 100`, 0.5 if unknown |
| Value | `clamp(1.3 − price_vs_median)` |
| Move-in fit | 1 if available within 14 days of `move_in`, falling to 0 at 60 days |

How v1 (`server/landline/scoring/`) fills the gaps:

- **Unknown is 0.5.** A value the listing doesn't state scores 0.5 and is never a reason or a watch-out. A column that is 0.5 for every listing (no data yet, as for light and transit before D-05 and D-07) adds a constant and moves nothing.
- **Size without a desired size:** `clamp(0.5 + sqft / typical − 1)`, with typical 450 / 650 / 900 / 1,200 / 1,500 ft² for studio / 1 / 2 / 3 / 4+ beds. A unit of typical size scores 0.5, one 1.5× as big scores 1. A stated size under 150 ft² is a data error and counts as unknown.
- **Location** is the mean of the known parts: each resolved anchor's `exp(-d_km / 2.5)`, and 1 inside a named area. Until neighbourhoods and adjacency arrive (D-05), a listing outside the named areas is unknown, not 0.
- **Amenities are evidence of presence only.** "Insuite Laundry" sets laundry to in-suite, "Parking" sets parking to extra (included isn't known), "Air Conditioning", "Elevator", "Balcony" / "Patio / Deck / Terrace" and "Wheelchair Accessible" set theirs. A missing amenity proves nothing.
- **Floor** has two modes. "High floor" uses `clamp(floor / 15)`. "Ground floor", "step free" or "no stairs" gives 1 on floor 1 or below, or with an elevator or step-free access, and 0 above that.
- **Laundry:** in-suite 1, shared 0.5, none 0. **Move-in fit** counts days either way: a place free six weeks early fits as badly as one free six weeks late.
- Building quality and value stay unknown until the City join (D-05) adds their fields.

## 3. Weights from priorities

- Default weights: cost 3, location 3, size 1.5, transit 1, light 1, building quality 0.5, value 0.5, move-in fit 1. Everything else is 0 until mentioned.
- When the renter ranks priorities ("parking matters most, then light"), the stated order gets 4, 3, 2, 1.5, 1. Later statements override earlier ones (most recent first). Features the renter didn't rank keep their default weight.
- **No signal, no weight.** No budget means no cost term, no resolved anchor or named area means no location term, and no move-in date means no move-in term. Parking, pets, laundry, AC, dishwasher, outdoor space and floor weigh 0 until mentioned. A must-have also ranks, at weight 2 or more, and pets weigh 2 once the renter has one.
- **Priority words.** The Matcher maps what the renter says to a feature: `cost` (price, budget, rent), `location` (area, commute, near), `size` (space, bigger), `transit` (subway, TTC), `light` (bright, sunny), `floor` (high floor, view), `ground_floor` (step free, no stairs), `parking`, `pets`, `laundry`, `ac`, `dishwasher`, `outdoor_space` (balcony, patio), `building_quality`, `value`, `move_in`. The full list is `SYNONYMS` in `scoring/weights.py`. A word it doesn't know is ignored, so the Concierge should send these keys.
- **Feedback moves weights.** Rejecting a listing with a reason maps to its attribute and adds +1 to that weight (cap 5):

| Said | Attribute | Effect |
|---|---|---|
| "too dark", "basement vibes" | light | light +1; basement units sink |
| "too far", "long commute" | location | location +1; tighter decay (2.5 → 1.8 km) |
| "too small", "cramped" | size | size +1; `desired_sqft` = this unit's sqft × 1.15 |
| "too expensive" | cost | cost +1; budget kept |
| "I need parking" | parking | becomes a must-have |
| "love this one" | — | its neighbourhood and top attributes get +0.5 |

## 4. Adjustments after calls

- `verified`: +7 points on the 100-point scale ("we phoned and confirmed" beats an unconfirmed bargain).
- `suspect` (any grounded red flag): −25 and it can't be ranked first; the card says why.
- Facts from calls replace listing values (for example, real parking cost changes `monthly_cost`).
- `booked` pins to the top.

## 5. Final score and explanation

`score = 100 × Σ wᵢ·uᵢ / Σ wᵢ + adjustments`. `contributions` stores `wᵢ·uᵢ` per feature. The explanation is the three largest positive contributions and the largest negative one, written in plain words:

> Why #1: $310 under budget · 6 min walk to Spadina · bright (14th floor, floor-to-ceiling windows). Watch out: parking not included.

The UI shows these reasons, never the number. Percentages and match bars read as made-up precision.

A feature's pull is `wᵢ·(uᵢ − 0.5)`, over known values only. A reason needs `uᵢ ≥ 0.6` and a watch-out `uᵢ ≤ 0.4`, so a middling value is neither. The phrases are in `scoring/reasons.py`: "$310 under budget", "0.9 km from King and Bay" ("right by" under 100 m), "1,050 sq ft" / "only 450 sq ft", "in-suite laundry", "parking available", "bright (estimated)". In the demo, a call that adds a mandatory fee changes the card's price and reasons but not its rank. Re-ranking from calls and feedback is O-04.

## 6. Light score

`light = 0.35·floor_term + 0.35·text_term + 0.30·photo_term`, and capped at 0.25 for basement units.

- `floor_term`: basement 0.1, ground 0.35, 2–4: 0.5, 5–9: 0.7, 10+: 0.85, unknown 0.5.
- `text_term`: from LLM-extracted cues, each with a quote. Positive: south- or west-facing, corner unit, floor-to-ceiling windows, skylight, "sun-filled". Negative: semi-basement, "cozy" with no windows mentioned, north-facing only. Start at 0.5, ±0.15 per cue, clamp 0–1.
- `photo_term`: mean luminance of up to five interior photos (Pillow, grayscale mean ÷ 255), normalized against the city-wide distribution. Unknown if there are no photos.
- `light_basis` stores all three, and the card says "Light: estimated from floor 14, 'floor-to-ceiling windows', bright photos."

## 7. Performance

The feature matrix (≈2k rows × ~25 features) lives in memory. Ranking all of it takes under 5 ms in plain Python with precomputed columns; numpy is optional. Distances to anchors are computed once when an anchor is resolved, not on every re-rank.

v1 keeps each utility column on the matrix, keyed by the preference values it depends on (the budget, the anchors, the desired size, the renter's pets, the move-in date). A re-rank reads cached columns, recomputes only rows that calls overrode, and sorts once. Measured on 2,000 listings: 1.3–1.8 ms (best of 20) for a budget with an anchor, 2.2 ms with twelve weighted features, 3.3 ms with call statuses and overrides on top (`server/tests/test_scoring.py` asserts < 5 ms).

## 8. How Danial evaluates it

- **Monotonicity tests:** raising a weight never lowers a listing that is strictly better on that feature; a brighter twin outranks a darker one when light matters.
- **Persona scenarios:** ~10 renter briefs with hand-labelled acceptable top-3 sets. Report hit@3 and how many feedback turns it takes to converge.
- **Stability:** the same input gives the same order, and one feedback turn moves at most the cards it should.
