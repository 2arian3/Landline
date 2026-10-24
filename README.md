# Landline

**Every voice agent in real estate works for the brokerage. Landline works for the renter.**

Call it and say what you want, in whatever language you speak. A live map of Toronto listings re-ranks while you talk. Pick the places you like and hang up: it phones those listing agents in parallel to find out what listings never say (is it still available, what does parking really cost, are dogs allowed, how much light does it get), books a viewing when you said it could, and texts you what it learned. Every fact on the map shows who said it. Call again next week and it remembers where you left off.

Built on Boson AI's **Higgs Realtime** (the renter's call and every listing call) and **Higgs TTS** (voicemail), with Higgs Avatar as a stretch goal, by Team AAMEL for the Boson AI Higgs Audio Hackathon, 23–24 October 2026.

## Status

Skeleton in place: the database schema, a polite scraper with a scheduled refresh (about 1,080 Toronto listings, all with coordinates), the API read side with a live session stream, and the map UI for phones and laptops. Voice agents, scoring and calls come next. See [BACKLOG.md](BACKLOG.md).

## How it works

- Two flows that never overlap: first the renter's call, then the calls to listing agents. The only thing passed between them is a frozen call plan.
Three agents use a model:

- **Concierge** (Higgs Realtime) talks to the renter, turns what they say into tool calls, and takes the list of places to call.
- **Callers** (Higgs Realtime, one per listing) phone listing agents in parallel once the renter hangs up, read numbers back, record each fact with the landlord's exact words, and book viewings inside the renter's times.
- The **Auditor** (a text LLM) keeps a fact only if its quote appears in the transcript.

Everything else is plain code that manages the session:

- A deterministic **orchestrator** routes every event, keeps one writer per session, and ends every call exactly once.
- The **Matcher** ranks listings from typed features: location (PostGIS), real monthly cost, size, floor, natural light, parking, pets, laundry, transit and City of Toronto building records. It explains each rank in plain words.
- The **Dispatcher** dials one call per picked listing after the renter hangs up; **Profiles** remember renters by phone number; the **Narrator** fills the templates for what the agent says and texts.
- The **web app** shows a live map and list that follow the conversation; tapping a listing tells the agent what you're looking at.

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/DATA.md](docs/DATA.md) · [docs/SCORING.md](docs/SCORING.md) · [docs/CONTRACTS.md](docs/CONTRACTS.md)

## Team

| | GitHub | Builds |
|---|---|---|
| Arian Boukani | [@2arian3](https://github.com/2arian3) | Data pipeline, database, orchestrator, scoring |
| Danial Kordmodanlou | [@Danial-Kord](https://github.com/Danial-Kord) | Voice agents, call dispatcher, evals |
| Mahyar Jaberi | [@mahyar-jbr](https://github.com/mahyar-jbr) | Web app, live map, API read side, deployment, video |

## Getting started

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, and Docker (for the local database).

```bash
git clone https://github.com/2arian3/Landline.git
cd Landline
make setup           # git hooks (they reject AI attribution in commits), Python deps, web deps
cp .env.example .env
```

In `.env`, set two values. No keys are needed to run the demo:

```bash
SCRAPER_CONTACT_EMAIL=you@example.com                               # goes into the scraper's User-Agent
DATABASE_URL=postgresql://landline:landline@localhost:54329/landline  # so the API reads the local database
```

Then:

```bash
make db-up           # Postgres 17 + PostGIS + pgvector in Docker on :54329
make db-migrate      # applies db/migrations/*.sql
make data            # scrapes liv.rent politely (one page every 2 s), checks, loads ~1,000 listings
make dev             # API on :8000 (one terminal)
make web             # web app on :3000 (another terminal)
```

Open http://localhost:3000/?dev=1. The demo controls stand in for the voice agent: set a budget and beds and press **Say it** to re-rank the map, then **Call the top 4** and **Hang up**. Four scripted calls run (no phone rings; the fourth doesn't pick up), and the board ends with a viewing booked and each place's result. **Start over** runs it again.

To check the API, run `curl localhost:8000/health`. It should show `"catalogue":"postgres"` and `"listings"` around 1,000.

- **No Docker?** Skip `db-up`, `db-migrate` and `data`, and leave `DATABASE_URL` empty. `make dev` then falls back to the latest scrape, and after that to `web/lib/fixtures/listings.json` (400 listings, `"catalogue":"fixture"`), so the demo runs on any laptop.
- `make data` also rewrites `web/lib/fixtures/listings.json`. Commit that file only on purpose.
- `make test` runs the Python tests and the web typecheck with no keys, phones or network.

**Production:** web app https://landline-mu.vercel.app (Vercel), API https://landline.134.87.8.230.sslip.io (API and database on the Landline VM). Pushing to the `release` branch (`git push origin main:release`) deploys the API once CI passes, and deploys the web app when `web/` changed. The web build runs its own typecheck. See [deploy/README.md](deploy/README.md).

Read [AGENTS.md](AGENTS.md) before writing code, then take the next open task in your lane in [BACKLOG.md](BACKLOG.md).

## Prior work

Landline grows out of **Realest**, which the same team built at *Agents, Everywhere* (Toronto, 12 Sep 2026). We port selected pieces and fix them on the way in. Each ported piece arrives in its own commit marked as ported, and [docs/PORTING.md](docs/PORTING.md) lists exactly what came from where.
