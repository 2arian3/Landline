# Landline: instructions for coding agents

Read this file before you change anything. It applies to every coding agent and every teammate working in this repo, whichever tool you use. `CLAUDE.md` imports it; other agents read it directly.

## What we are building

**Landline is a voice agent that works for renters, not brokerages.** A renter phones in (or talks to it in the browser). While they talk, a live map of Toronto listings re-ranks from what they say, and they pick the places they want checked. Once they hang up, Landline phones those listing agents in parallel, asks what listings never say (is it still available, what does parking really cost, are dogs allowed, how much light does it get), books a viewing inside the times the renter gave, re-ranks from what those people answered, and texts the renter the results. It remembers each renter by phone number, so the next call picks up where the last one ended.

Built by Team AAMEL (Arian Boukani, Danial Kordmodanlou, Mahyar Jaberi).

Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Interfaces: [docs/CONTRACTS.md](docs/CONTRACTS.md). Data: [docs/DATA.md](docs/DATA.md). Ranking: [docs/SCORING.md](docs/SCORING.md). What we carry over from Realest and which bugs we must not repeat: [docs/PORTING.md](docs/PORTING.md). Demo: [docs/DEMO.md](docs/DEMO.md). **Your tasks are in [BACKLOG.md](BACKLOG.md).** Take the next open task in your lane.

## Repo map and owners

| Path | What lives there | Owner |
|---|---|---|
| `pipeline/` | Scraping, parsing, geocoding, enrichment, feature extraction, loading | Arian |
| `db/migrations/` | Postgres schema (PostGIS + pgvector), applied in order | Arian |
| `server/landline/contracts/` | Pydantic models for entities, events, tools, API payloads. **Frozen; see below** | Arian (all three approve changes) |
| `server/landline/db/` | Connection pool, repositories | Arian |
| `server/landline/orchestrator/` | System services: session runtime, event bus, `session_tx`, Narrator templates, profiles | Arian |
| `server/landline/scoring/` | Features, utilities, ranking, explanations | Arian |
| `server/landline/voice/` | Higgs Realtime client, Twilio/browser/sim transports, call lifecycle, Dispatcher | Danial |
| `server/landline/agents/` | The three agents that use a model (Concierge, Caller, Auditor): prompts, tools, logic | Danial |
| `evals/` | Fixtures, landlord personas, eval runners, reports | Danial |
| `server/landline/api/` | REST + SSE read side for the web app, judge-mode endpoints | Mahyar (Twilio webhooks: Danial) |
| `web/` | Next.js app: map, list, detail, judge mode; its Vercel deploys | Mahyar |
| `deploy/`, and the VM itself | VM service files, reverse proxy, deploy scripts; on the VM, its `~/landline/.env`, services and logs; shipping `main` to `release` | Arian |
| `docs/`, `BACKLOG.md` | Design and plan | Arian |

Work only inside your lane's paths. If a task needs a change in someone else's path, open a PR and tag them; don't push to their files.

## Hard rules

1. **Contracts are frozen.** `server/landline/contracts/`, `db/migrations/` and `web/lib/types.ts` change only through a PR that all three approve. Add fields; never rename or remove them. See the `contracts` skill.
2. **Code owns identity, time and money.** A model never supplies a session id, call id, listing id, phone number, timestamp or price total. Ids come from the call or session the code is running; numbers are computed in code.
3. **Every fact shown to a renter has a source.** Each listing field carries its provenance (`scraped`, `llm`, `city`, `call`, `derived`, `synthetic`) and, where a model extracted it, the verbatim quote it came from. A quote that is not found in the source text means the field is dropped. Never invent a value; leave it null.
4. **One writer per session.** All session state changes go through `orchestrator.session_tx(session_id)`. No module-level dicts holding session data.
5. **Every call ends exactly once.** Only `finalize(call_id, reason)` ends a call. Every call has a deadline.
6. **Never dial a number that isn't on the allowlist** (`DEMO_AGENT_PHONES`). Judge mode never touches the phone network. Outbound calls open with "I'm an AI assistant calling for a renter."
7. **The hot path has no network calls.** Ranking runs in memory from a feature matrix loaded at boot. Geocoding, LLM extraction and summaries run off the hot path and trigger a re-rank when they finish.
8. **The page is never load-bearing.** If the web app dies, the voice loop still finishes, books and texts.
9. **No secrets in git.** `.env` stays local; share keys through a password manager. Run `gitleaks detect` before any push that might include config.
10. **Commit messages never mention AI tools.** No `Co-Authored-By` trailers for AI assistants, no "generated with" lines, no tool names. The `commit-msg` hook rejects them; run `make setup` once after cloning to install it.

## How we work

- Branch per task: `<lane>/<task-id>-<slug>`, e.g. `data/D-04-geocoder`. Small PRs into `main`; the author merges once CI is green and, for contract changes, all three approved. Merging to `main` doesn't deploy. Shipping is `git push origin main:release` (see `deploy/README.md`), and Arian does it, since the VM is his. Vercel deploys only when the commit at the tip of `release` is Arian's, so each ship ends with a commit of his on top of `main`; a GitHub merge commit doesn't count. Ask him rather than changing the VM or running a deploy yourself.
- Commit messages: imperative, sentence case, what changed and why in one line (`Add geocoder cache with precision field`). Reference the task id in the body when useful.
- Before you start a task, read its "Done when" in `BACKLOG.md` and the skill for your area. When you finish, tick the box in `BACKLOG.md` in the same PR.
- Keep diffs to what the task asks. No drive-by refactors, no new abstractions "for later".
- Ask before adding a dependency, deleting files, or changing the database schema outside a migration.
- Tests run without credentials, phones or network: `make test`. Anything that needs a key is behind an env check and skipped in CI.

## Commands

These targets are added by the tasks that create the code; until then they print a reminder.

| Command | Does |
|---|---|
| `make setup` | Install git hooks (works now), then Python and web deps once they exist |
| `make db-migrate` | Apply `db/migrations/*.sql` to `$DATABASE_URL` |
| `make db-pull` | Copy production's data into the local DB, replacing it (needs `ADMIN_TOKEN` in `.env`; `ARGS=--audio` adds call recordings). Production is only read |
| `make data` | Scrape liv.rent, check, load into `$DATABASE_URL` (the local DB by default) and refresh the web fixture. Geocode, enrich, extract, light and dedupe come with D-04 to D-07 |
| `make dev` | API on :8000 with `TRANSPORT=stub` |
| `make web` | Web app on :3000 |
| `make test` | Python and web tests, no network |
| `make eval` | Extraction, ranking and call evals; writes `evals/reports/latest.md` |

## Skills

Load the one for your area before working in it (`.claude/skills/<name>/SKILL.md`; other agents can read the same files):

- `contracts`: how ids, events and payloads are shaped, and how to change them
- `higgs-realtime`: the Higgs Realtime protocol, verified differences from OpenAI Realtime, audio formats, limits
- `data-pipeline`: scraping rules, stage layout, provenance, grounding, the feature dictionary
- `agent-evals`: fixtures, personas, metrics, how to add a case
- `live-map-ui`: design rules for `web/`, the map, motion, live updates
