---
name: data-pipeline
description: How Landline scrapes, parses, geocodes, enriches, extracts features from and loads rental listings. Covers the stage layout, politeness and legal rules, the grounding check for LLM extraction, provenance, the City of Toronto joins, the light score and the feature dictionary. Use when working in pipeline/, db/, on listing features, or on anything that writes listing data.
---

# Data pipeline

Spec: `docs/DATA.md` (sources, schema, feature dictionary) and `docs/SCORING.md` (the light score). This skill covers how to work in the code.

## Layout

```
pipeline/
  cli.py                # python -m pipeline <stage> [--source X] [--limit N]
  sources/<name>.py     # fetch + parse for one site; nothing else
  stages/fetch.py parse.py geocode.py enrich.py extract.py light.py dedupe.py load.py report.py
  grounding.py          # shared with the Auditor: verify(quote, source_text) -> bool
  cache/                # http + geocode caches (gitignored)
data/raw/<source>/<date>/     # immutable snapshots (gitignored)
data/stages/<stage>/*.jsonl   # each stage's output (gitignored, except small fixtures)
```

Every stage reads the previous stage's JSONL and writes its own, so stages can be rerun alone and in any order after `parse`. `make data` runs them all; `make data-report` prints coverage per field.

## Rules

- **Polite fetching:** at most 1 request every 2 s per host, a User-Agent with a contact address, a disk cache so reruns don't hit the site, and `robots.txt` respected. No logins, no CAPTCHA solving, no proxy rotation.
- **Structured data first:** the JSON the site's map view loads (usually has lat/lng), JSON-LD (`schema.org` Apartment / Offer / `geo`), or `__NEXT_DATA__`. Use HTML selectors or Playwright only when none of those exist.
- **No contact details:** never store a listing agent's phone or email. Calls go to `DEMO_AGENT_PHONES` only.
- **Photos:** keep URLs and attribution, don't commit image files. For the light score, download to `pipeline/cache/` and store only the computed luminance.
- **Provenance on every field:** `provenance[field] = {"source": "scraped|llm|city|call|derived|synthetic", "quote": "...", "at": iso, "version": "extract-v1"}`.
- **Grounding:** an LLM-extracted value is kept only if `grounding.verify(quote, text)` passes (normalized whitespace and case, fuzzy ratio ≥ 0.85, quote must come from the right speaker for calls). Failures are counted in the report, never silently fixed.
- **Money in code:** extract fee lines as `{name, amount, period, mandatory, quote}`. `monthly_cost = rent + sum(mandatory monthly fees)` is computed in `parse`/`extract`, never by the model.
- **Synthetic values** (only if we must fill a demo gap) are labelled `synthetic` and the card says so.

## Extraction prompt shape

Use structured outputs with a schema where every field is `{value, quote} | null`. The system prompt says: extract only what the text states; if it isn't stated, return null; quote the exact words; amounts as digits. Give the model the listing's address and rent as context, and never ask it for totals.

## City of Toronto joins

- Pull the registration (resource `3ad76a8c-0518-4df2-b94e-8c747d62f8c1`) and the 2023+ evaluation datasets through the CKAN `datastore_search` API with `limit` and `offset`, and cache them as JSONL.
- Match listings to buildings by nearest evaluation point within 30 m with a matching street number; join registration by `RSN`.
- A City value is a *claim about the building*, not the unit. When it disagrees with the listing, add a question to the Caller's checklist and show both on the card.
