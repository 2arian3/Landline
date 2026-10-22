# Contracts

These are the shapes all three lanes code against. At kickoff they become Pydantic models in `server/landline/contracts/`, the first migration, and `web/lib/types.ts` (task X-01). After that they change only by adding fields, through a PR all three approve.

## Ids

| Id | Format | Minted by |
|---|---|---|
| `session_id` | `s_` + 10 url-safe chars | `/twiml/renter`, `/api/judge/start` |
| `call_id` | `c_` + 10 url-safe chars | `CallManager.create()` before dialing |
| `listing_id` | `<source>:<source_id>[:<unit>]` | pipeline `parse` |
| `msg_id` | `m_` + 10 url-safe chars | board |
| `user_id` | bigserial | `users` insert on first call |

No model ever produces an id. Tool handlers take `session_id` and `listing_id` from the `Call` they run under.

## Events (orchestrator input)

Every event has `type`, `session_id`, `at`, `idem_key` and a payload. Handlers are idempotent on `idem_key`.

| Event | Payload | Emitted by |
|---|---|---|
| `SessionStarted` | `channel`, `caller_phone?`, `user_id?` | voice / judge API |
| `PreferencesUpdated` | `PreferenceDelta` | Concierge tool |
| `FeedbackGiven` | `listing_id`, `reaction` (like / dislike / reject), `reason?`, `attribute?` | Concierge tool, UI tap |
| `ListingFocused` | `listing_id`, `origin` (ui / voice) | UI tap, Concierge tool |
| `AreaFocused` | `label`, `lat`, `lng`, `zoom` | Concierge tool |
| `AnchorResolved` | `label`, `lat`, `lng` | Profiler (async) |
| `CallsRequested` | `listing_ids[]`, `extra_questions[]`, `availability?` | Concierge tool |
| `CallStateChanged` | `call_id`, `state`, `twilio_status?` | voice layer |
| `FactRecorded` | `call_id`, `field`, `value`, `quote`, `turn_idx`, `confirmed` | Caller tool |
| `CallEnded` | `call_id`, `reason` | `finalize()` |
| `OutcomeReady` | `call_id`, `facts[]`, `conflicts[]`, `red_flags[]` | Auditor (async) |
| `RenterAsked` | `msg_id`, `call_id`, `listing_id`, `question` | Caller `ask_renter` |
| `RenterAnswered` | `msg_id`, `answer` | Concierge `answer_landlord` |
| `QuestionForAll` | `question` | Concierge `ask_listing_agents` |
| `BookingRequested` | `listing_id`, `slot_text`, `decision` (confirm / reject) | Concierge tool |
| `SessionEnded` | `reason` | voice / timeout |

## Concierge tools (Higgs Realtime function tools)

Return values are spoken aloud: at most 25 words, written as speech, no ids.

| Tool | Arguments | Notes |
|---|---|---|
| `update_preferences` | `max_monthly?`, `min_beds?`, `areas?[]`, `anchors?[{label}]`, `pets?[]`, `must_have?[]`, `priorities?[]`, `move_in?`, `occupants?`, `language?` | Call it every time something changes; deltas only |
| `give_feedback` | `listing_ref?` (defaults to the focused listing), `reaction`, `reason?` | |
| `focus_listing` | `listing_ref` (address words or rank number) | Resolved by code against the current board |
| `focus_area` | `place` | Map flies there |
| `call_listings` | `listing_refs?[]` (default: top 3), `extra_questions?[]`, `availability?` | Returns immediately; results arrive between turns |
| `ask_listing_agents` | `question` | Reaches every live call |
| `answer_landlord` | `answer` | Answers the oldest open `need_answer` |
| `book_viewing` | `listing_ref`, `slot?`, `decision` | Refuses dead or suspect listings |
| `remember_me` / `forget_me` | — | Sets consent / deletes the profile |

## Caller tools

| Tool | Arguments | Notes |
|---|---|---|
| `record_fact` | `field` (from the feature dictionary), `value`, `quote` | Use as soon as something is said; quote = landlord's exact words |
| `ask_renter` | `question` | Answered from shareable profile fields first; otherwise goes to the board |
| `end_call` | `reason` (done / refused / wrong_number / voicemail) | Code accepts `done` only when the checklist is complete |

## Board message

`msg_id`, `kind` (ask_all / need_answer / reply / fact), `from_role` (renter / listing), `call_id?`, `listing_id?`, `text`, `reply_to?`, `status` (open / answered / expired), `created_at`, `ttl_s`.

Delivered into a live Higgs session as one `user` `input_text` item, wrapped as quoted data:
`[renter asks] "…"` · `[landlord at 322 Dupont asks] "…"` · `[client says] "…"` · `[call result] 322 Dupont: parking is $180/month extra.` · `[renter is looking at] 412 Markham`.

## Web API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/sessions/{id}` | `SessionView` |
| GET | `/api/sessions/{id}/stream` | SSE: `{"version": n}` on every commit; a heartbeat every 15 s |
| POST | `/api/sessions/{id}/focus` | `{listing_id}` → `ListingFocused` |
| POST | `/api/sessions/{id}/feedback` | `{listing_id, reaction, reason?}` → `FeedbackGiven` |
| GET | `/api/catalogue` | `[{listing_id, lat, lng, monthly_cost, beds}]`, cached |
| GET | `/api/listings/{listing_id}` | `ListingDetail` (with provenance and City record) |
| GET | `/api/calls/{call_id}/turns` | live captions for a call |
| POST | `/api/judge/start` | `{session_id, ws_url}` for browser audio |
| GET | `/health` | `{status, listings, sessions, transport, higgs}` |

`SessionView` = `{session_id, version, user: {name?, returning}, preferences, focus: {listing_id?, area?}, matches: [listing_id], board: [Card], agent_says, calls: [{call_id, listing_id, state, started_at}]}`.

`Card` = listing summary (`listing_id, address, unit_label, lat, lng, monthly_cost, rent, beds, baths, sqft, floor, photo, light_score`) + `rank`, `status`, `reasons[]`, `watch_out?`, `reaction?`, `facts[]` (`field, value, quote, speaker, at`), `red_flags[]`.

Contract tests: `server/tests/test_contracts.py` checks that `web/lib/types.ts` and the Pydantic models agree (a JSON Schema snapshot), and a sample `SessionView` fixture lives in `web/lib/fixtures/session.json` for UI work before the backend exists.
