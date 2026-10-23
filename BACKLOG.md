# Backlog

Three lanes that run in parallel and meet at the contracts. Take the next open task in your lane, branch as `<lane>/<id>-<slug>`, and tick the box in the same PR that finishes it. **Done when** is the bar; nothing is done until that sentence is true and shown (a test, a command's output or a screenshot in the PR).

| Lane | Owner | Paths |
|---|---|---|
| Data, orchestration and scoring (`D-`, `O-`) + shared setup (`X-`) | Arian | `pipeline/`, `db/`, `server/landline/{contracts,db,orchestrator,scoring}` |
| Agents, voice and evals (`A-`, `E-`) | Danial | `server/landline/{voice,agents}`, `evals/` |
| UI, API read side and delivery (`U-`) | Mahyar | `web/`, `server/landline/api/` (except Twilio webhooks), `deploy/` |

## Timeline (Toronto time)

The event runs from **Fri 23 Oct, 18:00** to **Sat 24 Oct, 16:00–17:00** (confirm the exact deadline at kickoff and move freeze/submit if it's 16:00).

| When | What | Gate |
|---|---|---|
| Thu 22 → Fri 18:00 | **Pre-event**: only what the rules allow (accounts, keys, spikes, data prep). Ask the organizers what's allowed | — |
| Fri 18:00–18:45 | **Kickoff**: freeze contracts (X-01), confirm deadline, assign first tasks | Contracts tagged `contracts-v1` |
| Fri 18:45–23:00 | Block 1: foundations in all three lanes | **Checkpoint A, 23:00**: typed renter → ranked listings from Postgres on the live map (stub transport) |
| Fri 23:00–02:30 | Block 2: calls, board, feedback, SSE | **Checkpoint B, 02:30**: one real phone loop on the deployed VM (renter call → 2 listing calls → map updates) |
| Sat 02:30–07:30 | Sleep in shifts; overnight pipeline jobs (geocode, LLM extraction, photo light) | — |
| Sat 07:30–12:30 | Block 3: profiles, judge mode, evals, polish | **Checkpoint C, 10:30**: full demo script end to end, twice, no hands on keyboard |
| Sat 12:30 | **Feature freeze.** Only fixes after this | — |
| Sat 12:30–15:00 | Rehearse ×3, record the video, write-ups, `gitleaks`, make repo public, pin SHA | — |
| Sat 15:00–15:30 | Submit; 30 min buffer before a 16:00 deadline | Submission form done |

**Cut order if behind** (first item goes first): Avatar debrief → listen-in audio → embeddings "vibe" search → Mandarin landlord persona → TTS voicemail → restart recovery (keep the per-session locks). **Never cut:** Higgs on both call legs, `finalize()` with deadlines, the allowlist, the live map, returning-caller memory.

---

## Pre-event (only if the organizers allow it)

- [ ] **X-00 · Arian · Accounts and access**: invite Danial and Mahyar to the repo; ~~create the Supabase project~~ (the database runs on the Landline VM instead, see `deploy/README.md`); claim Boson credits and create keys; confirm the Twilio number and `DEMO_AGENT_PHONES` (teammates only); share secrets through a password manager. **Done when** all three can reach the VM database through the SSH tunnel, run `select postgis_version()`, and see the Boson dashboard.
- [ ] **X-0R · Arian · Rules**: email the organizers to ask whether a project first built at another event may be entered, and what pre-event work is allowed. **Done when** we have a written answer, or a note saying we asked and when.
- [ ] **A-01 · Danial · Higgs spike**: `scripts/spike_higgs.py` connects with `audio/pcmu`, measures time to first audio, checks `conversation.item.truncate`, opens 5 sessions at once and reports any 1013, and checks Farsi + English transcription on one recorded clip. **Done when** the numbers are posted in the team chat and added to the `higgs-realtime` skill.
- [x] **D-01 · Arian · Source survey**: pick one main listing source and an optional second; check robots.txt and terms; find the structured endpoint (map JSON / JSON-LD / `__NEXT_DATA__`). **Done when** `docs/DATA.md` names the sources, the endpoints and the field mapping.
- [x] **U-00 · Mahyar · Visual direction**: three phone-sized mockups (map + list, detail drawer, judge mode) following the `live-map-ui` skill. **Done when** the team picks one. *Picked by Mahyar on Fri 23 Oct: ink (`#1a1815`), no extra accent. The live app's look stays, and green, amber and red stay for call status.*

## Kickoff (all three)

- [ ] **X-01 · Contracts freeze**: turn `docs/CONTRACTS.md` into `server/landline/contracts/*.py`, `db/migrations/0001_init.sql` (from `docs/DATA.md`), `web/lib/types.ts` and `web/lib/fixtures/session.json`; add the contract snapshot test; set up `pyproject.toml` (uv), the FastAPI app skeleton, `make test`, and CI. **Done when** CI is green (the migration applies twice in a row), the deploy applies it to the VM database, and all three approve the PR; tag `contracts-v1`. *Drafted on `skeleton/initial`: `server/landline/contracts/`, `db/migrations/0001_init.sql`, `web/lib/types.ts`, the app skeleton and CI. Needs review from all three.*

---

## Arian: data

- [x] **D-02 · Fetcher**: polite fetcher for source 1 with an on-disk cache and raw snapshots. **Done when** ≥ 800 Toronto listings are snapshotted under `data/raw/`.
- [x] **D-03 · Parser + normalizer**: rows with address, unit label, rent, fees, beds, dens, baths, sqft, floor, unit type, description, photos, url; building ranges expanded to units. **Done when** `data/stages/parse/*.jsonl` passes the row validator with zero errors.
- [x] **D-09 · UI/eval fixture (by 21:00 Fri)**: export 50 real listings in `Card` shape to `web/lib/fixtures/listings.json` and `evals/fixtures/listings.json`. **Done when** Mahyar's map renders them.
- [ ] **D-04 · Geocoder**: map-pin coordinates first, geocoder fallback with cache and precision. **Done when** ≥ 90% of rows are street precision or better.
- [ ] **D-05 · Enricher**: neighbourhoods (point-in-polygon + adjacency), TTC stations + walk minutes, City registration/evaluation join, neighbourhood medians. **Done when** the coverage report prints join rates for each.
- [ ] **D-06 · Grounded extractor**: shared `grounding.py` (quote must appear in source text, fuzzy ≥ 0.85) + description extractor (fees, pets, parking, laundry, AC, floor, light cues, red-flag phrases). **Done when** 30 hand-checked rows show zero ungrounded values, and Danial's extraction eval runs on it.
- [ ] **D-07 · Light estimator**: floor + text cues + photo brightness → `light_score` with `light_basis`. **Done when** every row has a score and the 5 brightest/darkest look right to a human.
- [x] **D-08 · Dedupe + load + report**: upsert to Postgres, mark stale, `make data-report`. **Done when** the DB holds ≥ 800 active listings and the report is in the PR.

## Arian: orchestration and scoring

- [ ] **O-01 · DB layer**: async pool, migration runner, repositories for every table in the schema. **Done when** repository tests pass against a throwaway schema.
- [ ] **O-02 · Session runtime**: `SessionRuntime`, typed event bus with idempotency keys, `session_tx` (per-session lock, version bump, event append, snapshot upsert), SSE publish hook, restart recovery. **Done when** tests show (a) a preference re-rank racing an outcome keeps the outcome, (b) a duplicate event is a no-op, (c) a restart restores open sessions.
- [ ] **O-03 · Matcher v1**: feature matrix loaded at boot; hard filters, utilities, weights and adjustments from `docs/SCORING.md`; reasons per card. **Done when** ranking 2k listings takes < 5 ms and the monotonicity tests pass.
- [ ] **O-04 · Feedback loop**: `FeedbackGiven` → weight changes from the SCORING table, interactions saved, rejected listings excluded. **Done when** "too dark" on the focused card visibly sinks darker units in a test and on the map.
- [ ] **O-05 · Profiles**: users by E.164, consent, `forget_me`, "new since last visit" query, the opening context for the Concierge (≤ 120 words). **Done when** a second call from the same number is greeted with the remembered brief in sim and on a real phone.
- [ ] **O-06 · Narrator lines**: state diff → at most one spoken line per commit ("Dupont is really $3,470 with parking, so it drops to third"). **Done when** each diff type has a template and a test.

## Danial: agents and voice

- [ ] **A-02 · Voice runtime** (port the Realest bridge, see `docs/PORTING.md` #1–4, #9): Higgs GA client, turn-aware `Line` queue, Twilio transport with `<Parameter>` call ids, status callbacks, AMD, per-call deadline, `finalize()`, dial allowlist, Twilio signature checks, Toronto business hours. **Done when** 4 sim calls + 2 real calls all end within their deadline and nothing can dial outside the allowlist (tests).
- [ ] **A-04 · Concierge**: prompt (multilingual; replies in the renter's language; ≤ 2 sentences per turn) + tools from `docs/CONTRACTS.md`; `call_listings` returns at once. **Done when** a scripted sim conversation produces the expected tool calls, and a real call in mixed Farsi/English works.
- [ ] **A-05 · Caller**: checklist from listing gaps + renter extras; `record_fact` as facts are said; read back every number and time; `end_call` rules; voicemail via Higgs TTS after AMD. **Done when** sim calls reach ≥ 90% checklist coverage with no ungrounded facts.
- [ ] **A-06 · Board**: `ask_all`, `need_answer`, `reply`, `fact` routing; profile-first answers; 25 s expiry; quoted delivery between turns. **Done when** the board tests (round trip, expiry, nothing delivered while someone is speaking, injection quoted) pass.
- [ ] **A-03 · Browser + sim transports**: browser PCM16 24 kHz over WebSocket; sim connects a Caller to a landlord-persona Higgs session (landlord speaks first). **Done when** a judge-mode session runs with 3 sim landlords without a phone.
- [ ] **A-07 · Auditor**: tagged turns + listing context → facts (quotes from landlord turns only, via `grounding.py`), reconcile tool vs transcript, conflicts with listing/City, red flags, viewing time to a datetime. **Done when** the extraction eval passes its thresholds.
- [ ] **A-08 · Profiler**: preference normalization, anchor resolution (async → `AnchorResolved`), end-of-session memory summary. **Done when** a session's memory is ≤ 120 words, has only stated facts, and loads on the next call.
- [ ] **A-09 · Debrief (stretch)**: Narrator summary → Higgs TTS → Higgs Avatar clip on the page. **Done when** a 20–30 s debrief plays in the renter's language after calls finish.

## Danial: evals

- [ ] **E-01 · Harness**: fixture format, `make eval`, `evals/reports/latest.md`. **Done when** an empty run produces a report.
- [ ] **E-02 · Extraction eval**: 20 transcripts with gold facts (tricky fees, pets, a Mandarin call, two scams, read-backs). Metrics: precision/recall per field, ungrounded rate (must be 0). **Done when** results are in the report.
- [ ] **E-03 · Ranking eval**: ~10 persona briefs with acceptable top-3, monotonicity and stability checks. **Done when** hit@3 and feedback-turns-to-converge are in the report.
- [ ] **E-04 · Call eval (sim)**: checklist coverage, read-back rate, call length, time to first audio, barge-in recovery, across 5 landlord personas. **Done when** all metrics are in the report.
- [ ] **E-05 · Numbers for the write-up**: one table of the best evidence for "How you built it". **Done when** Mahyar has it for U-10.

## Mahyar: UI, API and delivery

- [x] **U-01 · Web scaffold**: Next.js 15, React 19, Tailwind 4, MapLibre GL with OpenFreeMap tiles, design tokens from the `live-map-ui` skill, renders `fixtures/session.json`. **Done when** it deploys to Vercel and shows the fixture on a map at 375 px and desktop. *Local build done (map, list, detail, mobile sheet, live updates). Deployed to Vercel at https://landline-mu.vercel.app from `release` (`deploy-web.yml`), checked at 375 px and desktop.*
- [ ] **U-02 · Map + list**: every catalogue listing as a faint dot that fades as filters apply; numbered pins by rank, coloured by status; list in sync with pins; mobile bottom sheet; desktop split view. **Done when** 60 fps pan/zoom on a mid-range phone and pins match card order. **Needs** X-01 (a session fixture with every card status), O-02 (live card statuses).
- [ ] **U-03 · Live updates**: SSE client with version + snapshot fetch, poll fallback, reorder animation by transform, focus sync both ways (tap → POST focus; voice focus → fly to). **Done when** a voice change moves the map in < 1 s on the deployed stack, and killing SSE falls back to polling with no blank board. **Needs** O-02 (version bumps, `ListingFocused`), A-04 (the Concierge's focus and preference tools), X-01 (`focus_area` on `SessionView`).
- [ ] **U-04 · Detail drawer**: photos, features with source badges (listing says / City record / agent heard), reasons and watch-outs, call facts with quotes, live captions while calling, red flags. **Done when** every field shows its source. **Needs** D-05 (City record), D-06 (extracted fields with quotes), O-03 (reasons and watch-outs), A-07 (call facts and red flags), A-02 (call turns for live captions), X-01 (`ListingDetail` fields).
- [ ] **U-05 · API read side**: `/api/sessions/{id}`, `/stream`, `/focus`, `/feedback`, `/catalogue`, `/listings/{id}`, `/calls/{id}/turns`. **Done when** contract tests pass and the web app uses only these. **Needs** X-01 (contracts and the snapshot test), O-01 (calls and turns in the database), O-02 (the runtime behind sessions, focus and feedback), A-02 (call turns recorded).
- [ ] **U-06 · Judge mode `/try`**: explainer, mic permission, browser audio to A-03, sim calls with listen-in, rate limit and session cap. **Done when** a teammate on another network completes the demo script from the public URL. **Needs** A-03 (browser and sim transports), A-04 (Concierge), A-05 (Caller), O-02 (sessions), A-01 (the Higgs session limit, for the cap).
- [ ] **U-07 · Deployment**: VM (systemd, TLS, WSS for Twilio), Vercel proxy, health checks, deploy script. **Done when** `/health` is green over cellular and Twilio reaches the bridge. **Needs** A-02 (the Twilio bridge), X-00 (the Twilio number and keys on the VM; Mahyar in the AAMEL team on Vercel, or web deploys of Mahyar's commits are blocked). *API, database, refresh timer and CI/CD (deploys from the `release` branch) are live at https://landline.134.87.8.230.sslip.io (see `deploy/README.md`); the web app is live on Vercel at https://landline-mu.vercel.app (`deploy-web.yml`, direct API calls with CORS). The Twilio and WSS check is left.*
- [ ] **U-08 · Returning-renter view**: landing state for a known number ("Welcome back, here's what changed"). **Done when** it shows on the second call in the demo. **Needs** O-05 (profiles and "new since last visit"), A-08 (the memory summary).
- [ ] **U-09 · Video and media**: 2–3 min demo video per `docs/DEMO.md`; README GIF. **Done when** the public link plays without an access request. **Needs** the full demo running (Checkpoint C), E-05 (numbers for the closing shot), and clean demo listings from Arian's pipeline: plausible sizes (88 Erskine Avenue shows 1 bed at 1,880 sq ft for $1,850) and unit photos rather than ad banners.
- [ ] **U-10 · Submission**: write-ups (≤ 200 words each), team section, known limitations, `gitleaks`, repo public, SHA pinned. **Done when** the form is submitted (see the `submission` skill). **Needs** E-05 (numbers for the write-up), X-0R (the organizers' answer on pre-event work).
