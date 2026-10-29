# Landline

**Every voice agent in real estate works for the brokerage. Landline works for the renter.**

Call it and say what you want, in whatever language you speak. A live map of Toronto listings re-ranks while you talk. Pick the places you like and hang up: it phones those listing agents in parallel to find out what listings never say (is it still available, what does parking really cost, are dogs allowed, how much light does it get), books a viewing when you said it could, and texts you what it learned. Every fact on the map shows who said it.

Built on Boson AI's **Higgs Realtime** (the renter's call and every listing call, transcribed by Higgs speech-to-text), with a Higgs Avatar clip of each call, by Team AAMEL.

![The scripted demo (?dev=1): the brief re-ranks the map, Call the top 4 starts four calls at once, What the calls found comes back with a viewing booked, and a card opens into its detail](media/landline-demo.gif)

## Try it

- **Call +1 (289) 512-2338** and say what you want, in any language: *"A one-bedroom near Bloor under $2,600. I have a dog."*
- **Or talk in the browser** at **https://landline-mu.vercel.app/try**: press **Start talking**, allow the microphone, and watch the map re-rank as you speak. Try *"natural light matters most"*, *"that one's too dark"* or *"call the top three"*, then press **Done**.
- **No microphone?** https://landline-mu.vercel.app/?dev=1 runs a scripted demo: **Say it** → **Call the top 4** → **Hang up**.

Status, Sat 24 Oct, on about 1,080 real Toronto listings: call the number and you're texted a link to your live map as the call connects. When you hang up, Landline phones the places you picked over Twilio from the same number, but only to the team's own allowlisted phones (`DEMO_AGENT_PHONES`), with teammates playing the listing agents, and only between 09:00 and 20:00 Toronto time; a call queued outside those hours waits for 9:00. The results come back by text. On `/try` and in the browser, the listing calls go to simulated landlords, each a second Higgs session, and no phone rings. See [BACKLOG.md](BACKLOG.md) and [Known limitations](#known-limitations).

## What it looks like

| | |
|---|---|
| ![The board on a 1920 px projector after the calls: What the calls found in the glass panel, and pins whose shape and badge say how each call went](media/lucent-projector.png) | ![The board on a phone, 375 px wide: the map under a glass header, and the sheet raised with what Landline heard and the ranked places](media/lucent-phone.png) |
| **On a projector:** after the calls, the panel leads with what each one found, and each pin's shape and badge say how it went. | **On a phone:** the map under a glass header, and a sheet with what Landline heard and the places it ranked. |
| ![/me signed in: the renter's calls with what each agent said, what they want, and the number to call](media/lucent-me.png) | ![The Expo app's Home on an iPhone: the tally, Call Landline, the latest result and the glass tab bar](media/lucent-app.png) |
| **Your calls (`/me`):** sign in with your phone number to see every place Landline called, each agent's words, and edit what you want. | **The app (`mobile/`):** the same calls and search on an iPhone, with a button that calls Landline. |

## How it works

- Two flows that never overlap: first the renter's call, then the calls to listing agents. The only thing passed between them is a frozen call plan.
Three agents use a model:

- **Concierge** (Higgs Realtime) talks to the renter, turns what they say into tool calls, and takes the list of places to call.
- **Callers** (Higgs Realtime, one per listing) phone listing agents in parallel once the renter hangs up, read numbers back, record each fact with the landlord's exact words, and book viewings inside the renter's times.
- The **Auditor** (a text LLM) keeps a fact only if its quote appears in the transcript.

Everything else is plain code that manages the session:

- A deterministic **orchestrator** routes every event, keeps one writer per session, and ends every call exactly once.
- The **Matcher** ranks listings from typed features: location (PostGIS, with the City of Toronto's 158 neighbourhoods), real monthly cost, size, floor, natural light, parking, pets, laundry, and the walk to the nearest TTC subway station. Facts heard on a call update a listing's features and re-rank it. It explains each rank in plain words.
- The **Dispatcher** dials one call per picked listing after the renter hangs up; **Profiles** keep each renter's last brief for the team (the next call starts fresh); the **Narrator** fills the templates for what the agent says and texts.
- The **web app** is a full-screen live map with a glass panel beside it (a bottom sheet on a phone) that follows the conversation: each pin's shape, badge and words say how its call is going, and tapping a listing tells the agent what you're looking at. On **`/me`** a renter signs in with their phone number to see their calls and edit their search; the **app** (`mobile/`, Expo) does the same on an iPhone. See [mobile/README.md](mobile/README.md).

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/DATA.md](docs/DATA.md) · [docs/SCORING.md](docs/SCORING.md) · [docs/CONTRACTS.md](docs/CONTRACTS.md)

## Numbers

Every figure here comes from a file in this repo, and each row names the run it came from. Eval figures are the **holdout** sets in [evals/reports/latest.md](evals/reports/latest.md). Prompts were tuned on separate tune sets, never on these. Times are UTC, Saturday 24 October.

### Higgs models and what each one does

| Model or API | Role in Landline | Code |
|---|---|---|
| **Higgs Realtime** (`higgs-realtime`, speech to speech over WebSocket) | The **Concierge** on the renter's call (μ-law 8 kHz from Twilio, or 24 kHz PCM in the browser), whose six function tools move the live map and record the call plan. After the renter hangs up, one **Caller** per picked listing records facts, notes questions and books viewings through its own tools. In judge mode and the evals, a second Realtime session in text mode plays the landlord | [voice/higgs.py](server/landline/voice/higgs.py) |
| **Higgs speech-to-text** (`higgs-stt-3.1`, inside each Realtime session) | Transcribes both sides of every call into the captions and stored call turns that the Auditor checks quotes against | [voice/higgs.py](server/landline/voice/higgs.py) |
| **Higgs text chat** (`higgs-realtime` on `api.boson.ai/v1`, JSON-schema output) | One choice of text model (`LLM_PROVIDER=boson`) for the Auditor's fact extractor, the results-text translation and the reader for renter lines in non-Latin scripts. The extraction eval's recorded answers came from it | [agents/auditor/llm.py](server/landline/agents/auditor/llm.py) |
| **Higgs TTS** (`higgs-tts-v3`) | Synthesizes the renter's and landlords' lines that the audio eval streams into the real voice paths. The live voicemail is Twilio `<Say>`, not Higgs TTS | [evals/run_audio.py](evals/run_audio.py) |

### Technical execution

| What | Number | Run · source |
|---|---|---|
| Concierge vs scripted renters, 8 holdout cases × 3 runs, live Higgs text | 6/8 cases pass every run; 83% of runs pass; preferences recorded 72/72; goodbye, then `end_session`, 24/24; 0 claims of a change with no tool call | 14:32, `e738796` · latest.md, Concierge holdout |
| Caller vs landlord personas, 2 holdout personas × 5 runs | Checklist coverage 24/25 (96%); facts with the right value 19/20 (95%); 0 ungrounded quotes; said it's an AI assistant first 10/10; card ends with the right status 10/10 | 08:28, `cfb384e` · latest.md, Listing calls holdout |
| Bookings inside the renter's windows | 14/14 (tune personas; the two holdout personas, a scam and a plan with no booking allowed, book nothing) | 08:28, `cfb384e` · latest.md, Listing calls tune |
| Auditor on 8 holdout call transcripts | **0 of 17 facts shown without a landlord's quote**; precision 100%, recall 91% (rules alone, with no model: precision 100%, recall 65%) | 15:03, `768699e`, extractor answers replayed from recorded Higgs responses · latest.md, Auditor holdout |
| Voice on Higgs audio: end of the other side's voice → the agent's first audio, p50 / p90 | Concierge (browser) 1,472 / 1,804 ms (n=12); Caller (phone) 1,516 / 2,344 ms (n=20). From Higgs' `speech_stopped`, p50 922 ms and 960 ms. Greeting, `response.create` → first audio, p50 508 ms (n=2) | 08:56, `674e37d` · latest.md, Voice |
| Barge-in on Higgs audio | 12/12 recovered (heard, audio cleared, the next reply answers it); heard 292 ms (p50) after the voice starts | 08:56, `674e37d` · latest.md, Voice |
| Higgs Realtime spike, μ-law | 487 ms from connect to the first audio; 282 ms from `response.create`; 8 sessions opened at once with no 1013 close | Fri 23 Oct, A-01 spike · [higgs-realtime skill](.claude/skills/higgs-realtime/SKILL.md) |
| Matcher on 3 holdout renter briefs, pure code | hit@3 100%; precision@3 89%; 0 monotonicity violations | 15:13, `4b67c0a` · latest.md, Matcher holdout |
| Ranking time, warm, in memory | p50 1.389 ms, p95 2.123 ms on 4,000 listings; p95 0.25 ms on 400 (target < 5 ms) | 15:13, `4b67c0a` · latest.md, Matcher checks |
| Listings in production | 1,082 active Toronto listings. All 1,082 are at street precision, in one of the City's 158 neighbourhoods, and matched to the nearest of 68 TTC subway stations; 424 (39%) are within a 15-minute walk of one. All 1,082 have a light score, 1,077 of them with photo brightness | Sat 09:45 · [BACKLOG.md](BACKLOG.md) D-04, D-05, D-07 |

What these numbers don't show:

- Speech-to-text on real voices: the audio eval speaks synthesized clips. In the spike, a mixed Farsi and English sentence on 8 kHz phone audio came back garbled.
- The tune sets score lower. The Concierge passes 62% of runs on its 21 tune cases.
- Feedback on the top card doesn't always move it: "too small" and "too far" left the top card first in 2 of 3 holdout briefs, and the "too dark" check fails.
- The Concierge, Caller and voice runs are older than some changes on `main`, such as the place-lookup fallback and the board-link text. Extraction and ranking were rerun on current `main`.

## Example: one call, start to finish

From the `?dev=1` demo, where the listing agents are scripted (`server/landline/api/demo.py`). The renter asked for a one-bedroom under $2,800, picked the top four and hung up. Landline's call to 650 Briar Hill Avenue, as its captions show it:

> **Landline:** Hi, this is Landline. I'm an AI assistant calling for a renter about the place at 650 Briar Hill Avenue. Is it still available?<br>
> **Listing agent:** Yes.<br>
> **Landline:** Are pets okay? My client has a dog.<br>
> **Listing agent:** Dogs are fine. There are a couple in the building already.<br>
> **Landline:** Could they see it Saturday afternoon?<br>
> **Listing agent:** Saturday at two works.<br>
> **Landline:** Saturday at 2 pm, then. I'll let my client know.

The place's card afterwards (`GET /api/sessions/{id}`, trimmed). The fact keeps the agent's exact words:

```json
{
  "listing_id": "livrent:101529",
  "address": "650 Briar Hill Avenue",
  "monthly_cost": 2500,
  "status": "booked",
  "reasons": ["999 sq ft", "$300 under budget"],
  "facts": [
    {
      "field": "pets",
      "value": "Dogs are fine",
      "quote": "Dogs are fine. There are a couple in the building already.",
      "speaker": "Agent heard"
    }
  ]
}
```

And the results the renter gets once all four calls are done, written by code from the outcomes:

> I booked 650 Briar Hill Avenue for Saturday at 2 pm, inside your Saturday afternoon. 30 Carabob Court is really $2,179 a month with parking. The agent at 30 Carabob Court asked, "Would they sign a 12-month lease?" I said I'd check with you. 701 Don Mills Rd wants a deposit by e-transfer before anyone sees the unit, so I'd skip it. Nobody picked up at 1915 Martin Grove Rd.

## API

Base URL `https://landline.134.87.8.230.sslip.io`. Payload shapes are in [docs/CONTRACTS.md](docs/CONTRACTS.md).

| Method | Path | Does |
|---|---|---|
| GET | `/health` | Status, listing count, live sessions, whether Higgs answers |
| GET | `/api/catalogue` | Every listing as a map point |
| GET | `/api/listings/{listing_id}` | One listing, each field with its source |
| GET | `/api/sessions/{id}` | A session's ranked board, plan and calls |
| GET | `/api/sessions/{id}/stream` | Server-sent events: the new version on every change |
| POST | `/api/sessions/{id}/focus` | `{listing_id}`: the renter tapped a listing |
| POST | `/api/sessions/{id}/feedback` | `{listing_id, reaction, reason?}`: like, dislike or reject |
| GET | `/api/calls/{call_id}/turns?after=` | Live captions of a call |
| POST | `/api/judge/start` | Opens a browser session: `{session_id, ws_url}` |
| WS | `/api/judge/{id}/audio` | The browser's audio, PCM16 24 kHz both ways |
| POST | `/api/judge/{id}/done` | Ends the brief, like hanging up |
| POST | `/api/judge/{id}/hang-up` | Frees the judge's line when the page closes |
| POST | `/twiml/renter` | Twilio's webhook for a call to the number (signed by Twilio) |
| WS | `/twilio/media` | Twilio's audio stream for that call |
| POST | `/api/sessions/{id}/preferences`, `/area`, `/calls`, `/hang-up`, `/reset`, `/returning` | `?dev=1` demo controls that stand in for the voice agent |

## Team

| | GitHub | Builds |
|---|---|---|
| Arian Boukani | [@2arian3](https://github.com/2arian3) | Data pipeline, database, orchestrator, scoring, the VM |
| Danial Kordmodanlou | [@Danial-Kord](https://github.com/Danial-Kord) | Voice agents, call dispatcher, evals |
| Mahyar Jaberi | [@mahyar-jbr](https://github.com/mahyar-jbr) | Web app, live map, the Expo app, API read side, Vercel, video |

## Known limitations

- **Snapshot data.** Listings are scraped from liv.rent and refreshed every 6 hours, so a place can be rented before the next refresh notices.
- **Demo phones only.** Listing calls go only to the team's allowlisted phones (`DEMO_AGENT_PHONES`), never to real listing agents.
- **Business hours.** Listing calls go out only between 09:00 and 20:00 Toronto time. A plan made outside those hours waits for 9:00.
- **Two conversations at once.** Landline holds up to two live conversations, by phone and on `/try` together: a judge and a phone renter, two judges, or two phone renters (`HIGGS_MAX_SESSIONS=2`). A third is told the lines are busy. Listing calls run three at a time across all sessions (`CALL_CONCURRENCY=3`), and more wait their turn.
- **Pets, parking and laundry aren't read from descriptions yet** (D-06). Parking and laundry come only from a listing's amenity tags, and whether pets are allowed is known only once a call asks.
- **No City building records.** The RentSafeTO join isn't done. Each neighbourhood's median rent by bedrooms is computed, but it doesn't feed the ranking or the page yet.
- **Some languages get English replies.** In a phone-audio probe, the agent saved Farsi, Arabic and Punjabi renters' preferences but replied in English; Chinese and Spanish renters got replies in their language.
- **No Higgs TTS in the product.** A voicemail is read by Twilio's built-in voice. Higgs TTS only makes the audio eval's clips.

## Getting started

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, and Docker (for the local database).

```bash
git clone https://github.com/2arian3/Landline.git
cd Landline
make setup           # git hooks (they reject AI attribution in commits), Python deps, web deps
cp .env.example .env
```

In `.env`, set your email for the scraper and uncomment `DATABASE_URL` (`.env.example` ships with it commented out). No keys are needed to run the demo:

```bash
SCRAPER_CONTACT_EMAIL=you@example.com                               # goes into the scraper's User-Agent
DATABASE_URL=postgresql://landline:landline@localhost:54329/landline  # so the API reads the local database
```

Then:

```bash
make db-up           # Postgres 17 + PostGIS + pgvector in Docker on :54329
make db-migrate      # applies db/migrations/*.sql
make data            # scrapes liv.rent politely (one page every 2 s), checks, loads ~1,000 listings
                     # or, for the team: make db-pull, a copy of production's data (needs the team's ADMIN_TOKEN in .env)
make dev             # API on :8000 (one terminal)
make web             # web app on :3000 (another terminal)
```

Open http://localhost:3000/?dev=1. The demo controls stand in for the voice agent: set a budget and beds and press **Say it** to re-rank the map, then **Call the top 4** and **Hang up**. Four scripted calls run (no phone rings; the fourth doesn't pick up), and the board ends with a viewing booked and each place's result. **Start over** runs it again.

To check the API, run `curl localhost:8000/health`. It should show `"catalogue":"postgres"` and `"listings"` around 1,000.

- **No Docker?** Skip `db-up`, `db-migrate` and `data`, and leave `DATABASE_URL` commented out. `make dev` then falls back to the latest scrape, and after that to `web/lib/fixtures/listings.json` (400 listings, `"catalogue":"fixture"`), so the demo runs on any laptop.
- **Talking to it locally** on http://localhost:3000/try needs a Boson key: set `BOSON_API_KEY` and `JUDGE_AUDIO_READY=1` in `.env`. Its listing calls go to the simulated landlords, so no phone rings.
- `make data` also rewrites `web/lib/fixtures/listings.json`. Commit that file only on purpose.
- `make test` runs the Python tests, the web typecheck and the web tests with no keys, phones or network. Without a database, the tests that need Postgres are skipped.

**Production:** web app https://landline-mu.vercel.app (Vercel), API https://landline.134.87.8.230.sslip.io/health (the API's root has no page; the API and database run on the Landline VM). Pushing to the `release` branch (`git push origin main:release`) deploys the API once CI passes, and deploys the web app when `web/` changed. The web build runs its own typecheck. See [deploy/README.md](deploy/README.md).

Read [AGENTS.md](AGENTS.md) before writing code, then take the next open task in your lane in [BACKLOG.md](BACKLOG.md).

## Prior work

Landline grows out of **Realest**, which the same team built at *Agents, Everywhere* (Toronto, 12 Sep 2026). We port selected pieces and fix them on the way in. Each ported piece arrives in its own commit marked as ported, and [docs/PORTING.md](docs/PORTING.md) lists exactly what came from where.
