# Pitch numbers (X-03f)

From a read-only copy of production's database (`scripts/db_pull.py`, copied Wed 28 Oct around 01:05 Toronto time), plus the evals. **All of it is the team's own testing.** No real renter has used Landline yet, and listing calls went only to the team's allowlisted phones (`DEMO_AGENT_PHONES`), with teammates playing the listing agents. Say "in testing" with every number below.

| What | Number, in testing | Source |
|---|---|---|
| Testing window | Sat 24 Oct 00:01 to Wed 28 Oct 00:37, Toronto time | `sessions.started_at` |
| Calls to Landline | 44 by phone, 10 in the browser (`/try`) | `calls`, role `renter` |
| Phone numbers that called | 4, all team phones; 3 called more than once (one 23 times) | `sessions`, channel `phone`, by user |
| Listing calls placed on the phone network | 19: 15 reached a person, 2 reached voicemail, 1 got no answer, 1 was left ringing on Oct 24, before the #217 fix (2 more are queued outside 09:00–20:00) | `calls`, role `listing`, transport `voice` |
| Simulated listing calls (sim landlords on Higgs) | 5 | `calls`, transport `sim` |
| A listing call, once answered | 61 s on average (median 73 s), over 17 answered calls | `calls.answered_at` → `ended_at` |
| Facts heard on real listing calls | 49 on the cards, from 18 calls. **Every one carries the listing agent's exact words, and all 49 quotes are found word for word in that call's transcript** | `OutcomeReady` events, re-checked against `call_turns` |
| Viewings booked on calls | 1 on a real call, 3 on simulated calls | `ViewingBooked` events |
| Plans finished, each followed by a results text (sending isn't stored) | 18 phone plans | `CallsCompleted` events on phone sessions |
| Cost per search, as tested (1 renter call, 1 listing call, 2 texts) | about **$0.11–$0.19** (USD). Each more listing called adds about $0.04–$0.05, so three listings come to about $0.20–$0.28 | measured minutes × list prices, below |
| Evals (holdout) | Concierge: 6/8 cases pass every run. Caller: 96% checklist coverage. Auditor: 0 ungrounded facts, precision 100%, recall 91.3%. Matcher: hit@3 100%; ranks 4,000 listings in 1.4 ms (p95) | `evals/reports/latest.md`, reruns; dates, SHAs and staleness below |

## How we counted

**The copy.** `uv run python scripts/db_pull.py --database-url <a throwaway Postgres>`, a GET of `/api/admin/export` with the admin token. Production was only read, and the throwaway database was removed afterwards. The queries ran on that copy.

**Calls and sessions** (SQL on the copy):
```sql
-- calls to Landline, and listing calls, by transport
select role, transport, count(*) from calls group by 1, 2;
-- how listing calls on the phone network ended (answered_at set = picked up; voicemail = a machine)
select end_reason, state, count(*), count(answered_at) from calls
where role = 'listing' and transport = 'voice' group by 1, 2;
-- 3 rows have no end: one left 'ringing' on Oct 24 (before the #217 fix) and two 'queued' after 20:00
-- length once answered
select avg(extract(epoch from ended_at - answered_at)),
       percentile_cont(0.5) within group (order by extract(epoch from ended_at - answered_at))
from calls where role = 'listing' and transport = 'voice' and answered_at is not null;
-- numbers that called, and how often
select user_id, count(*) from sessions where channel = 'phone' group by 1;
```

**Facts.**
- Facts on the cards are the Auditor's outcomes: `jsonb_array_elements(payload->'outcome'->'facts')` over `OutcomeReady` events joined to `calls` for the transport. That's 49 from real calls, 28 from simulated ones; 36 + 23 more were recorded live by the Caller (`FactRecorded`).
- Each quote was checked again with the product's own check (`server/landline/agents/auditor/grounding.py`, `find`) against that call's landlord turns in `call_turns`: 49 of 49 real-call facts and 28 of 28 simulated ones match.
- Facts the Auditor *dropped* as ungrounded are only logged, not stored, so the share it refused can't be counted from the database. The eval number below covers that.

**Viewings and texts.**
- `ViewingBooked` events, by the call's transport.
- Results texts aren't stored as sent; Twilio's message log has them. The results text itself is stored on 18 phone sessions, one per `CallsCompleted`.

**Cost per search.** A search is one phone call to Landline that placed listing calls. As tested, every plan placed exactly one (19 plans, 19 calls). Measured on the copy:
- A call to Landline: 1.31 min on average, 1.89 billed minutes (each call rounded up). The agent spoke 0.81 min of it, from the audio spans in `call_turns`, scaled up for the 10 of 306 turns with no span.
- A listing call: 89% were answered. An answered one lasts 1.02 min, billed as 1.59; the agent spoke 0.57 min.
- Texts: the board link is 1 segment, and a results text is 1–5 segments (2.7 with the link, on average).

Prices, in USD, pay-as-you-go:

| Item | Price | Source |
|---|---|---|
| Twilio, a call in to a Canadian local number | $0.0085 / min | [twilio.com/en-us/voice/pricing/ca](https://www.twilio.com/en-us/voice/pricing/ca) |
| Twilio, a call out to a Canadian number | $0.0140 / min | same page |
| Twilio Media Streams (both calls stream audio to Higgs) | $0.0044 / min | same page |
| Twilio answering-machine detection (each listing call) | $0.0075 / call | same page |
| Twilio billing | per minute, rounded up, per call leg | [support.twilio.com/…/223132307](https://support.twilio.com/hc/en-us/articles/223132307-How-are-Twilio-voice-and-calling-product-minutes-rounded-for-billing) |
| Twilio SMS from a Canadian long code | $0.0083 / segment + a carrier fee of $0.0064–$0.0087 | [twilio.com/en-us/sms/pricing/ca](https://www.twilio.com/en-us/sms/pricing/ca) |
| Higgs Realtime, audio in / out | $0.0023 / $0.014 a minute (Boson's own estimates: Higgs bills by token) | [docs.boson.ai/pricing.md](https://docs.boson.ai/pricing.md) |
| Higgs transcription, if billed on top of Realtime | $0.0025 / min (the upper end of the range) | same page |
| Google Maps (stores, walks) | $0 a search. It runs in the pipeline, cached, inside the free monthly caps (Nearby Search: 5,000; Route Matrix: 10,000 elements) | [developers.google.com/maps/billing-and-pricing/pricing](https://developers.google.com/maps/billing-and-pricing/pricing) |

How the range is built:
- Twilio minutes are the billed (rounded-up) ones. Higgs counts audio in for the whole call and audio out for the agent's measured speech.
- The low end has no separate transcription charge and the cheapest SMS carrier with the shortest results text. The high end adds transcription and uses the dearest carrier with the longest text.
- An extra listing call adds AMD, plus (when answered) its minutes on Twilio, Streams and Higgs.
- **Not included:** the phone number ($1.15 a month), the Auditor's text-model tokens, Higgs Avatar clips, and texts sent in another language (only the English results are stored).

**Evals.** Every eval ran on Sat 24 Oct, and the code has changed since. Extraction and ranking need no key, so they were rerun on Oct 28: ranking on `fae5f0f` with `--no-report`, and extraction by the runner on `ca81888`, after #273.

| Eval (holdout) | Number | Run | Stale? |
|---|---|---|---|
| Concierge vs scripted renters, 8 cases × 3 runs | 6/8 cases pass every run, 83% of runs | Oct 24, `e738796` | yes: 35 commits to `agents/concierge` since |
| Caller vs landlord personas, 2 × 5 runs | checklist coverage 96%, 0 ungrounded quotes | Oct 24, `cfb384e` | yes: 25 commits to `agents/caller` since |
| Auditor on 8 call transcripts (recorded extractor answers) | 0 of 17 facts ungrounded, precision 100%, recall 91.3%, 6/8 transcripts fully right | rerun Oct 28, `ca81888` (#273) | fresh. It had dropped to 87% since `f618ddb` (#216), which made a spoken amount like 一千七 one token, under the one-word rule; #273 fixed it |
| Matcher on 3 renter briefs | hit@3 100%, precision@3 89%; p95 1.364 ms on 4,000 listings | rerun Oct 28, `fae5f0f` | fresh, unchanged |
| Voice on Higgs audio | end of speech → first audio about 1.5 s (p50); barge-in recovered 12/12 | Oct 24, `674e37d` | yes: that SHA isn't in `main`'s history |

The README's Numbers section still quotes the Oct 24 runs. The Concierge, Caller and voice numbers need a live run with a key before the pitch, or should be said with their date.
