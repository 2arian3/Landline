---
name: contracts
description: Rules for Landline's shared interfaces, covering ids, events, tool schemas, board messages, the database schema and the web API types, and how to change them during the hackathon without breaking another lane. Use before editing server/landline/contracts/, db/migrations/, web/lib/types.ts, agent tool schemas, or any payload that crosses a lane boundary.
---

# Contracts

The spec is `docs/CONTRACTS.md`, and the database half is in `docs/DATA.md`. The code lives in `server/landline/contracts/` (Pydantic v2), `db/migrations/` (SQL, applied in order) and `web/lib/types.ts`.

## Rules

1. **Additive only after `contracts-v1`.** Add a field (optional, with a default) or a new event type. Never rename, retype or remove one during the event. If something is wrong, add the correct field and stop writing the old one.
2. **One PR, all three approve.** A contract PR touches the Pydantic model, the migration (if stored), `web/lib/types.ts`, the JSON Schema snapshot and the sample fixture together. CI fails if they disagree.
3. **Code mints ids.** `session_id`, `call_id`, `msg_id` and `listing_id` are created by code, never by a model. Tool handlers read them from the running `Call`.
4. **Numbers are computed, not extracted.** Models return words and quotes. Code computes `monthly_cost`, `real_rent`, datetimes, distances and scores.
5. **Every extracted value carries a quote.** A value without a quote that matches the source text (`grounding.verify`) is dropped and logged as `ungrounded`.
6. **Migrations are forward-only.** New file `NNNN_<what>.sql`; never edit an applied one. Ask before any `drop`.

## Adding an event

1. Add the model in `contracts/events.py`, with `type: Literal["MyEvent"]`, and add it to the `Event` union.
2. Add a handler in `orchestrator/handlers.py` that runs inside `session_tx` and is idempotent on `idem_key`.
3. Add a test that sends the event twice and checks the second is a no-op.

## Adding a field the UI shows

1. Add it to the Pydantic `Card` or `ListingDetail` with a default.
2. Mirror it in `web/lib/types.ts` and the fixture.
3. Give it a provenance entry when it comes from data or a call.
4. Run `make test`; the contract snapshot test regenerates with `make contracts-snapshot`.
