# Contracts

These are the shapes all three lanes code against. They are Pydantic models in `server/landline/contracts/`, the first migration, and `web/lib/types.ts` (task X-01). They change only by adding fields, through a PR all three approve.

## Ids

| Id | Format | Minted by |
|---|---|---|
| `session_id` | `s_` + 10 url-safe chars | `/twiml/renter`, `/api/judge/start` |
| `call_id` | `c_` + 10 url-safe chars | `CallManager.create()`, for the renter's call when it connects and for each listing call when the Dispatcher queues it |
| `listing_id` | `<source>:<source_id>[:<unit>]` | pipeline `parse` |
| `user_id` | bigserial | `users` insert on first call |

No model ever produces an id. Tool handlers take `session_id` and `listing_id` from the `Call` they run under. A `CallPlan` has no id of its own: there is at most one per session, keyed by `session_id`.

## Events (orchestrator input)

Every event has `type`, `session_id`, `at`, `idem_key` and a payload. Handlers are idempotent on `idem_key`.

**Flow 1: the brief**

| Event | Payload | Emitted by |
|---|---|---|
| `SessionStarted` | `channel` (phone / browser / judge), `caller_phone?`, `user_id?` | voice / judge API |
| `PreferencesUpdated` | `PreferenceDelta` | Concierge tool |
| `FeedbackGiven` | `listing_id`, `reaction` (like / dislike / reject), `reason?`, `attribute?` | Concierge tool, UI tap |
| `ListingFocused` | `listing_id`, `origin` (ui / voice) | UI tap, Concierge tool |
| `AreaFocused` | `label`, `lat`, `lng`, `zoom` | Concierge tool |
| `AnchorResolved` | `label`, `lat`, `lng` | Profiles, via the geocoder (background) |
| `CallsRequested` | `listing_ids[]`, `extra_questions[]`, `availability[]` (`{day, from, to}` as the Concierge passed them), `may_book` | Concierge `call_listings`. Records the plan; dials nothing |
| `SessionEnded` | `reason` (hangup / end_session / closed / timeout) | voice / timeout |

**Hand-off**

| Event | Payload | Emitted by |
|---|---|---|
| `CallsScheduled` | `plan` (`CallPlan`), `call_ids[]` | Dispatcher, on `SessionEnded` when the session has a plan |

**Flow 2: the calls**

| Event | Payload | Emitted by |
|---|---|---|
| `CallStateChanged` | `call_id`, `state`, `twilio_status?` | voice layer, Dispatcher |
| `FactRecorded` | `call_id`, `field`, `value`, `quote`, `turn_idx`, `confirmed` | Caller `record_fact` |
| `QuestionNoted` | `call_id`, `question` | Caller `note_question` |
| `ViewingBooked` | `call_id`, `listing_id`, `starts_at`, `slot_text` | Caller `book_viewing`, once code accepts the slot |
| `CallEnded` | `call_id`, `reason` | `finalize()` |
| `OutcomeReady` | `CallOutcome` | Auditor (async) |
| `CallsCompleted` | `call_ids[]` | Dispatcher, when every call in the plan has an outcome or timed out |

## Concierge tools (Higgs Realtime function tools)

Return values are spoken aloud: at most 25 words, written as speech, no ids.

| Tool | Arguments | Notes |
|---|---|---|
| `update_preferences` | `max_monthly?`, `min_beds?`, `areas?[]`, `anchors?[{label}]`, `pets?[]`, `must_have?[]`, `priorities?[]`, `move_in?`, `occupants?`, `language?` | Call it every time something changes; deltas only |
| `give_feedback` | `listing_ref?` (defaults to the focused listing), `reaction`, `reason?`, `attribute?` (light / location / size / cost / parking) | The Concierge maps the reason to its attribute ("too dark" → light); code applies the weight change from `docs/SCORING.md` |
| `focus_listing` | `listing_ref` (address words or rank number) | Resolved by code against the current board |
| `focus_area` | `place` | Map flies there |
| `call_listings` | `listing_refs?[]` (default: top 3), `extra_questions?[]`, `availability?[]` (`{day, from, to}`, e.g. `saturday`, `13:00`, `17:00`; `day` is a weekday, `today` or `tomorrow`), `may_book?` | Records the plan; nothing dials until the session ends. Code resolves the refs against the board, and the reply reads the addresses back. At most 5 listings. Refused while an earlier plan is still calling |
| `remember_me` / `forget_me` | — | Sets consent / deletes the profile |
| `end_session` | — | Code hangs up once the goodbye has played |

## Caller tools

The Caller gets the renter's shareable answers and viewing windows in its instructions at dial time. No tool reaches the renter.

| Tool | Arguments | Notes |
|---|---|---|
| `record_fact` | `field` (from the feature dictionary), `value`, `quote` | Use as soon as something is said; quote = landlord's exact words. `field` is one of `available`, `available_from`, `rent`, `fees`, `parking`, `parking_cost`, `pets`, `laundry`, `ac`, `utilities_included`, `furnished`, `lease_months`, `sqft`, `floor`, `light`, `elevator`, `step_free`, `outdoor_space`, `other` (the answer to one of the renter's extras) |
| `note_question` | `question` | The landlord asked something the brief can't answer. The Caller says it will check with its client; the question comes back in the results |
| `book_viewing` | `slot` (spoken words, e.g. "Saturday at two") | Code converts it to a Toronto datetime and accepts it only if booking is allowed, it falls inside a window, it doesn't overlap another booking in the plan, the listing isn't dead or suspect, and no red-flag rule matched this call. The reply says it's booked, or what to say instead |
| `end_call` | `reason` (done / refused / wrong_number / voicemail) | Code accepts `done` only when the checklist is complete |

## CallPlan (the hand-off)

The only thing the calls read from the brief. Built by code on `SessionEnded`, stored in the session snapshot, never edited afterwards.

`session_id`, `user_id?`, `listing_ids[]` (1–5, in the renter's order), `questions[]` (the renter's extras), `windows[]` (`{start, end}`, Toronto datetimes that code computes from `availability` when it freezes the plan), `may_book`, `shareable` (`{name?, move_in?, occupants?, pets?}`, only the fields listed in `user_profiles.shareable`), `language` (for the results), `created_at`.

## CallOutcome

One per listing call, from the Auditor.

`call_id`, `listing_id`, `end_reason`, `facts[]` (the card's `Fact`: `field, value, quote, speaker, at`), `conflicts[]` (`{field, listed, heard, quote, against}`, where `against` is `listing` or `city`), `red_flags[]` (strings, as on the card), `open_questions[]`, `viewing?` (`{starts_at, slot_text}`).

`end_reason` (also on `CallEnded`) is `end_call`'s reason (`done` / `refused` / `wrong_number` / `voicemail`) or the voice layer's (`hangup` / `no_answer` / `busy` / `failed` / `blocked` / `timeout`).

The Pydantic models are in `contracts/events.py` (every event, and the `Event` union keyed on `type`), `contracts/plan.py` (`CallPlan`, `CallOutcome`), `contracts/tools.py` (tool arguments, and `concierge_tools()` / `caller_tools()` for `session.update`) and `contracts/session.py` (what the web app reads).

## Where it is stored

No new tables.

- The session phase is `sessions.status`: `open` (the brief is live) → `calling` → `done`, or `closed` when the brief ends with no plan.
- The `CallPlan` and the results text are in `sessions.snapshot`.
- Queued calls are `calls` rows with `state = 'queued'`.
- Noted questions and bookings are in `events` and the snapshot. A booking also writes `listing_interactions` (`booked`).
- `board_messages` from `0001_init.sql` is no longer written. Dropping it needs its own migration and an ask.

## Text injected into a live voice

Only into the Concierge, only between turns, as one `user` `input_text` item, quoted as data: `[renter is looking at] 412 Markham`, and the session summary after a reconnect. Nothing is injected into a Caller: everything it needs is in its instructions when it dials.

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
| GET | `/api/sessions/{id}/avatars` | `[{call_id, role, address, status}]` in call order, the renter's call first. A clip of Landline's voice for each finished call. No video bytes. `status` is `queued` / `rendering` / `ready` / `failed` / `skipped`. `address` is the listing's, empty on the renter's call |
| GET | `/api/sessions/{id}/avatars/{call_id}` | that clip's MP4 when `ready`, otherwise 404. Only a call in this session |
| POST | `/api/judge/start` | `{session_id, ws_url}` for browser audio |
| GET | `/health` | `{status, listings, sessions, transport, higgs}` |
| GET | `/api/me` | `Authorization: Bearer <Clerk session token>` → the signed-in renter's saved search, chosen places, sessions and calls (the admin user shape). The phone comes only from the verified token's `phone` claim, and a token's `azp`, when it has one, must be `WEB_URL`, a CORS origin or a preview matching `CORS_ORIGIN_REGEX`. A native app's token (Expo) has no `azp` and is accepted on our instance's signature. 401 with no token, an invalid or expired one, another app's, or one with no phone; 404 when the number isn't stored, there is no database, or `CLERK_SECRET_KEY` is unset. |
| GET | `/api/me/listings` | Signed in as for `GET /api/me` → every listing Landline called for the renter, newest first: `[{listing_id, address, unit_label, session_id, call_id, called_at, ended, facts, red_flags, viewing}]`. `ended` is `talked`, `voicemail` or `no_answer`; each fact is `{field, value, quote, at}`, the agent's own words its source; `viewing` is `{starts_at, slot_text, status}` (`booked` until X-04 stores `confirmed` or `declined`). Built from stored rows only (calls, each session's snapshot). Never-dialled calls are left out. Same 401 and 404 as `GET /api/me`. |
| POST | `/api/me` | `{phone}` → the same shape, for that number with no proof. **401 once `CLERK_SECRET_KEY` is set**; without it (local dev, CI): 422 when `phone` isn't a number; 404 when it isn't stored or there is no database. Transcripts and recordings stay on the admin routes. |
| GET | `/api/admin/users` | Admin: every renter with counts (needs `Authorization: Bearer $ADMIN_TOKEN`; 404 when unset) |
| GET | `/api/admin/users/{user_id}` | Admin: one renter's saved search, chosen places, sessions and calls |
| GET | `/api/admin/calls/{call_id}` | Admin: one call with its transcript (each line with `audio_start_ms`/`audio_end_ms` when known), the facts heard, and `audio` (whether it has a recording) |
| GET | `/api/admin/calls/{call_id}/audio` | Admin: the call's recording as a PCM16 WAV. `track=both` (stereo: caller left, agent right; default), `caller` or `agent`; `from_ms`/`to_ms` for one stretch, e.g. one line. 404 when nothing was recorded there |
| GET | `/api/admin/calls/{call_id}/avatar` | Admin: that call's Higgs Avatar clip as an MP4, the same file as `/api/sessions/{id}/avatars/{call_id}`. 404 until it is ready |
| GET | `/api/admin/export` | Admin: the whole database as one consistent, read-only copy (gzipped JSON header + `COPY` blocks, `server/landline/db/export.py`), for `make db-pull`. Call recordings only with `audio=true` |

`SessionView` = `{session_id, version, phase, user_name?, returning, preferences, focus_listing_id?, focus_area?, matches: [listing_id], board: [Card], agent_says, plan: [listing_id], calls: [{call_id, listing_id, state, started_at?}], results?}`. `phase` is `open` / `calling` / `done` / `closed`. Call `state` is `queued` / `blocked` / `dialing` / `ringing` / `live` / `voicemail` / `ended` (the call lifecycle in ARCHITECTURE.md). `focus_area` is `{label, lat, lng, zoom}`. `results` is the Narrator's results text for the page, set once the phase is `done`. `preferences` also carries `anchors[]` (`{label, lat?, lng?}`, coordinates filled by `AnchorResolved`), `move_in?`, `occupants?` and `language?`.

`Card` = listing summary (`listing_id, address, unit_label, neighbourhood, lat, lng, monthly_cost, rent, beds, baths, sqft, floor, unit_type, photo, light_score, source, url`) + `rank`, `status`, `reasons[]`, `watch_out?`, `reaction?`, `facts[]` (`field, value, quote, speaker, at`), `red_flags[]`, `viewing?` (`CardViewing`: `{starts_at, slot_text, status}`, status `booked` / `confirmed` / `declined`; `booked` waits for the listing agent to confirm; X-04, filled from O-08b).

`ListingDetail` (the `Listing` contract) also carries `grocery_name?`, `grocery_walk_min?`, `pharmacy_name?` and `pharmacy_walk_min?`: the nearest grocery store and pharmacy with walking minutes (X-04, filled by D-10b).

Contract tests: `server/tests/test_contracts.py` checks that `web/lib/types.ts` has the same fields as the Pydantic models, that the models match the JSON Schema snapshot in `server/tests/snapshots/contracts.json` (regenerate it with `make contracts-snapshot` and commit it with the change), and that no tool argument carries an id, a phone number, a time or a total. A sample `SessionView` (phase `calling`, one card per status) lives in `web/lib/fixtures/session.json` for UI work before the backend exists.
