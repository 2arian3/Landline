# Porting from Realest

Realest (Team AAMEL, *Agents, Everywhere*, 12 Sep 2026) is the starting point. We decided to **port selectively**: Landline has its own layout and contracts, and we copy in only the pieces listed here, fixing them on the way in. Each port goes in its own commit whose message says it was ported from Realest, so the judges see exactly what is new.

Source repo: `AamelAI/realest` (public mirror). Audit of commit `fbc8813`, 21 Oct 2026.

## What we port

| Realest piece | Lands in | Changes on the way in | Task |
|---|---|---|---|
| `server/bridge.py` Twilio ↔ Realtime bridge | `voice/twilio.py` | Higgs GA protocol, `audio/pcmu`, `<Parameter>` call ids, `finalize()`, no "demo" fallback | A-02 |
| `server/transport.py` stub transport | `voice/stub.py` | `STUB_DELAY=0` in tests | A-02 |
| `server/calls.py` SMS with dedupe + retry | `voice/sms.py` | dedupe keyed on Session/Call, not module dicts | A-02 |
| `server/calls.py` `as_e164` | `contracts/phone.py` | unchanged, plus tests | X-01 |
| `server/listings.py` `rank()` ideas | `scoring/` | rewritten on typed features (see below) | O-03 |
| `web/lib/usePolling.ts` safeguards | `web/lib/live.ts` | SSE first, polling as fallback; keep: in-flight guard, out-of-order drop, never blank a live board | U-03 |
| `web/` design rules and card motion | `web/`, `live-map-ui` skill | adds the map; keeps the restraint | U-01 |
| Scraped rentals.ca snapshot (124 buildings) | `pipeline` fixtures | only as a test fixture; the new pipeline re-scrapes | D-02 |
| `tests/` patterns (inbound continuity, demo scripts) | `server/tests`, `evals/` | rewritten for the new contracts | E-01 |

Not ported: the ElevenLabs provider, the ElevenLabs admin monitor, the hand-made neighbourhood and transit tables, the `_ensure_session` fallbacks.

## Bugs we must not bring across

Each one maps to the Landline task that makes it impossible.

| # | Realest bug (file:line at `fbc8813`) | Landline fix | Task |
|---|---|---|---|
| 1 | The bridge reads session/listing from the Stream URL query string (`bridge.py:128, 136`), which Twilio doesn't deliver | `<Parameter name="call_id">`, read from `start.customParameters` | A-02 |
| 2 | Twilio-path calls never end: no status callback, no deadline (`calls.py:327`) | `statusCallback` + AMD + deadline + `finalize()` | A-02 |
| 3 | A phone number spoken to the model becomes a dial target (`main.py:425`, `calls.py:97`); `/agent/*` unauthenticated | One `dial()` with an allowlist; Twilio signature checks | A-02 |
| 4 | Business hours use the server's clock (`calls.py:254`) | `ZoneInfo("America/Toronto")` | A-02 |
| 5 | A preference re-rank can erase a call result (`main.py:381`) | Read-rank-write inside `session_tx` | O-02 |
| 6 | Duplicate outcomes can apply twice (`main.py:546` vs `562`) | Compare-and-set on the Call, idempotent events | O-02 |
| 7 | Two minutes of silence while calls run (`main.py:525`) | Calls run only after the renter hangs up; `call_listings` just records the plan | A-04, A-06 |
| 8 | Session state spread over ten module dicts | Session and Call records in Postgres | O-01 |
| 9 | Missing ids create orphan sessions (`main.py:265`) | Ids come from the Call; unknown ids are rejected | A-02 |
| 10 | Fallbacks call or book listings nobody picked (`main.py:436, 446, 595`) | Resolve or ask; never guess targets that reach people. Only listings in the `CallPlan` are called, and bookings land only inside the renter's windows | A-04, A-05 |
| 11 | Pet policy by substring: "No pets allowed" scores as pet-friendly | Typed `pets` enum + renter species | O-03, D-06 |
| 12 | Fees by first-number regex: "2 spots at $180" adds $2 | Structured fees; code computes `monthly_cost` | D-06, A-07 |
| 13 | The model does rent arithmetic without the listed rent | Model never returns totals | A-07 |
| 14 | The extractor invents provenance ("Mark, 1:42pm") | Code stamps time and call; quotes are verified | A-07 |
| 15 | The agent's own words can be read as the landlord's | Speaker-tagged turns; quotes must come from landlord turns | A-07 |
| 16 | Viewing times are free text | Spoken time converted to a Toronto datetime in code | A-07 |
| 17 | No coordinates; neighbourhood and transit from hand tables | Geocoding + PostGIS + TTC GTFS | D-04, D-05 |
| 18 | Buildings collapsed into one fake unit, duplicate addresses | Explicit unit rows with labels | D-03 |
| 19 | `/api/state` ships all transcripts every 1.2 s | Versioned SSE; transcripts only in detail calls | U-05 |
