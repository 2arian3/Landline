# Landline

**Every voice agent in real estate works for the brokerage. Landline works for the renter.**

Call it and say what you want, in whatever language you speak. A live map of Toronto listings re-ranks while you talk. Ask it to, and it phones the listing agents in parallel to find out what listings never say: is it still available, what does parking really cost, are dogs allowed, how much light does it get. Every fact on the map shows who said it. Call again next week and it remembers where you left off.

Built on Boson AI's **Higgs Realtime** (the renter's call and every listing call) and **Higgs TTS** (voicemail), with Higgs Avatar as a stretch goal, by Team AAMEL for the Boson AI Higgs Audio Hackathon, 23–24 October 2026.

## Status

Skeleton in place: the database schema, a polite scraper with a scheduled refresh (about 1,080 Toronto listings, all with coordinates), the API read side with a live session stream, and the map UI for phones and laptops. Voice agents, scoring and calls come next. See [BACKLOG.md](BACKLOG.md).

## How it works

- **Concierge** (Higgs Realtime) talks to the renter and turns what they say into tool calls.
- **Callers** (Higgs Realtime, one per listing) phone listing agents in parallel, read numbers back and record each fact with the landlord's exact words.
- A deterministic **orchestrator** routes every event, keeps one writer per session, and ends every call exactly once.
- The **Matcher** ranks listings from typed features: location (PostGIS), real monthly cost, size, floor, natural light, parking, pets, laundry, transit and City of Toronto building records. It explains each rank in plain words.
- The **Auditor** keeps a fact only if its quote appears in the transcript. The **Profiler** remembers renters by phone number.
- The **web app** shows a live map and list that follow the conversation; tapping a listing tells the agent what you're looking at.

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/DATA.md](docs/DATA.md) · [docs/SCORING.md](docs/SCORING.md) · [docs/CONTRACTS.md](docs/CONTRACTS.md)

## Team

| | GitHub | Builds |
|---|---|---|
| Arian Boukani | [@2arian3](https://github.com/2arian3) | Data pipeline, database, orchestrator, scoring |
| Danial Kordmodanlou | [@Danial-Kord](https://github.com/Danial-Kord) | Voice agents, agent-to-agent board, evals |
| Mahyar Jaberi | [@mahyar-jbr](https://github.com/mahyar-jbr) | Web app, live map, API read side, deployment, video |

## Getting started

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, and Docker (for the local database).

```bash
git clone https://github.com/2arian3/Landline.git
cd Landline
make setup           # git hooks, Python deps, web deps
cp .env.example .env # set SCRAPER_CONTACT_EMAIL; the rest can wait
make db-up           # Postgres 17 + PostGIS + pgvector on :54329
make db-migrate
make data            # scrape ~1,000 Toronto listings (politely), check, load
make dev             # API on :8000      (in one terminal)
make web             # web on :3000      (in another); open /?dev=1 for demo controls
```

No database? `make dev` falls back to the latest scrape, then to `web/lib/fixtures/listings.json`, so the web app works on any laptop.

**Production:** web app https://landline-mu.vercel.app (Vercel), API https://landline.134.87.8.230.sslip.io (API and database on the Landline VM). Pushing to the `release` branch deploys both once CI passes (`git push origin main:release`). See [deploy/README.md](deploy/README.md).

Read [AGENTS.md](AGENTS.md) before writing code, then take the next open task in your lane in [BACKLOG.md](BACKLOG.md).

## Prior work

Landline grows out of **Realest**, which the same team built at *Agents, Everywhere* (Toronto, 12 Sep 2026). We port selected pieces and fix them on the way in. Each ported piece arrives in its own commit marked as ported, and [docs/PORTING.md](docs/PORTING.md) lists exactly what came from where.
