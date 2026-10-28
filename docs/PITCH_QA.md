# Answers to the judges' questions (X-03i)

**Draft, for the team to rehearse** before Build North's Demo Day (Thu 29 Oct). Part of #248.

Each answer has three parts:

- **Say**: two or three sentences to speak.
- **Behind it**: the file or doc that makes it true.
- **Not built yet**: say this plainly if asked.

Everything here describes what `main` does today. If a sentence stops being true, fix it here before you say it on stage.

The numbers come from `docs/PITCH_NUMBERS.md` (X-03f, #272). **All of them are from the team's own testing**: no real renter has used Landline yet, and listing calls went only to our own phones. So say "in testing" with every number, and say the date with the Oct 24 eval runs. Don't quote a number that isn't in that file.

---

## Judges' questions (#248)

### 1. Is it all right for an AI to phone people?

**Say:** "Every call opens with 'Hi, this is Landline. I'm an AI assistant calling for a renter about [the address]. This call is recorded.' So nobody is fooled about who's calling, why, or that it's recorded. It only calls a short allowlist of numbers, today our own team's phones, between 9 a.m. and 8 p.m. Toronto time, and only for listings the renter picked. Every call has a hard five-minute limit."

**Behind it:**
- The first words: `server/landline/agents/caller/brief.py` (`disclosure()` and the `CALLER` prompt). Hard rule 6 in `AGENTS.md`. The recording notice follows them when the audio is stored (`RECORDED` in `voice/outbound.py` `opening()`, `RECORD_AUDIO`, #280).
- The allowlist: `voice/outbound.py` `dial()` is the only place a listing call reaches the phone network, and it refuses any number not in `DEMO_AGENT_PHONES`. `voice/dispatch.py` checks it too.
- The hours: `OPENS, CLOSES = 9:00, 20:00` in `voice/dispatch.py`.
- The time limit: `listing_call_max_s = 300` in `config.py`, plus Twilio's own `TimeLimit`. Every call ends once, through `finalize` (hard rule 5).
- Judge mode (`/try`) never touches the phone network: its calls go to simulated landlords (`voice/routing.py`).

**Not built yet:**
- **No "don't call me again" list.** There's no do-not-call list for agents yet. Today it only dials our own phones, so nobody else can be called.
- **The opening doesn't give a call-back number, and the weekend hours are too wide.** It names Landline (#280), but a call back would reach the renter line until callbacks (A-13) are built. Canada's automated-call rules expect a number to reach us, and allow weekend calls only from 10:00 to 18:00 (question 10).

### 2. What happens to the renter's data?

**Say:** "We remember a renter by their phone number: what they're looking for, what they liked, and the calls we made for them, so their next call picks up where they left off. A listing agent only hears what the renter lets us share: by default their move-in date, how many people and whether they have pets, and they can change that in their profile. Transcripts and recordings sit behind an admin key and never appear on the renter's page."

**Behind it:**
- What's stored: `users`, `user_profiles` and `listing_interactions` in `db/migrations/0001_init.sql`; transcripts in `call_turns`; audio in `call_audio` (0002).
- What the Caller may say: `user_profiles.shareable`, defaulting to `move_in`, `occupants` and `pets` (`SHARED_BY_DEFAULT` in `server/landline/api/me.py`), edited through `PATCH /api/me/profile`. `agents/caller/brief.py`: "Nothing about the renter reaches the Caller any other way."
- Who can see what: with Clerk switched on (`CLERK_SECRET_KEY`), the renter sees their own history only after a phone sign-in with an SMS code (`api/me.py`). Transcripts and recordings are only on `/api/admin/*`, which needs `ADMIN_TOKEN`.
- Listing agents' phone numbers and emails are never stored from listings (the `data-pipeline` skill).
- The shared listing memory (O-09a, `orchestrator/memory.py`) stores what agents said about a **listing**, each with its quote, never anything about the renter.

**Not built yet:**
- **"Forget me" isn't switched on.** The code to delete a renter is written (`Profiles.forget` in `orchestrator/profiles.py`), but the Concierge's `forget_me` and `remember_me` tools are off (`DEFERRED` in `agents/concierge/tools.py`), and there's no button for it. Today we'd delete someone by hand on request.
- **We remember every phone caller by default.** Asking first (`remember_me`) is off too. The renter call does say it's recorded, but not that we keep their search for next time.
- **No retention period.** Nothing expires recordings yet.

### 3. What if the listing agent says something wrong, or the listing disagrees?

**Say:** "Every fact we show carries the agent's exact words and who said them: 'Agent heard: "It's 150 a month for parking."' If the quote isn't something the agent actually said, the fact is dropped, and words our own AI said never count. When the call disagrees with the listing, the renter hears both, like 'The listing says $2,400 in all, but with what the agent said it's $2,550.'"

**Behind it:**
- Hard rule 3 in `AGENTS.md`: every fact has a source.
- `agents/auditor/audit.py`: a quote must be found in the landlord's turns (`grounding.find`). A quote that only matches the AI's own words is dropped as `agent_words`, anything else as `ungrounded`. Facts are labelled "Agent heard".
- The conflict sentences: `_conflict()` in `orchestrator/narrator.py`. Amounts are recomputed in code, never taken from the model (hard rule 2).
- Dates come from the agent's words too ("from tomorrow" becomes Sunday, October 25), never a date the model offers (`free_from()` in `agents/auditor/values.py`, #138).
- Red flags such as money before a viewing make the card "Something's off", and the text says "so I'd skip it" (`agents/auditor/flags.py`, `narrator.py`).

**Not built yet:** conflicts with the City's building records. The contract allows them (`against: "city"`), but the RentSafeTO join (D-05) isn't loaded, so only the listing is compared today.

### 4. How accurate is it?

**Say:** "In testing, every one of the 49 facts our calls put on the cards carries the agent's exact words, and we re-checked that each quote is in that call's transcript, word for word. On held-out transcripts we never tuned on, it pulls facts out at 100% precision and 91% recall, with nothing shown that lacks a quote. In simulated listing calls it covers 96% of what it was sent to ask, and in those runs it never booked a viewing outside the renter's times."

**Behind it:**
- `docs/PITCH_NUMBERS.md`: the 49 facts from 18 real test calls, all 49 quotes re-checked against `call_turns`. The Auditor's holdout was rerun Oct 28: 0 of 17 facts ungrounded, precision 100%, recall 91.3%.
- The Caller's 96% checklist coverage is from the Oct 24 run, so say its date.
- How the evals work: the `agent-evals` skill.

**Not built yet:**
- None of this is from real renters, and no person has checked each fact by hand.
- Facts the Auditor *dropped* are logged, not stored, so production can't say how many it refused. Only the eval covers that.

### 5. What does one search cost to run, and who pays?

**Say:** "In testing, one search, meaning the renter's call, one listing call and two texts, costs about 11 to 19 US cents, mostly phone and voice-model minutes. Each extra listing adds about 4 or 5 cents, so calling three comes to about 20 to 28 cents. The renter could pay per search, but we think the better payer is whoever moves people to Toronto, like employers relocating hires and universities housing students, who already spend money on this."

**Behind it:**
- `docs/PITCH_NUMBERS.md`, "Cost per search": measured minutes from the test calls times Twilio's and Boson's list prices, with sources.
- It leaves out the phone number ($1.15 a month), the Auditor's text-model tokens, avatar clips, and translated texts.
- The "who pays" line is from the pitch plan (#248, X-03h).

**Not built yet:** billing, accounts for organisations, and usage limits.

### 6. What happens when an agent won't talk to an AI?

**Say:** "It says it's an AI in its first sentence, so the agent can say no straight away. If they won't talk, it says goodbye and hangs up; it never pretends to be a person or argues. The renter's text then says plainly '[the address]'s agent wouldn't answer my questions', so they know to call that one themselves."

**Behind it:** the `CALLER` prompt in `agents/caller/brief.py` (end the call with reason `refused` if they won't talk), and `_unheard()` in `orchestrator/narrator.py` for the results sentence.

**Not built yet:**
- Handing a refused listing to a person.
- Letting an agent call back (A-13, in progress): today the voicemail says "I'll try again later".

### 7. How does it work outside Toronto?

**Say:** "The calls, the fact-checking and the ranking don't know anything about Toronto. What's Toronto-specific is the data: one listings site, the City's neighbourhood map, the TTC stations, the time zone and calling hours, and the box we search places in. A new city means a listings source and those reference files, not a rebuild, but we haven't done one yet."

**Behind it:**
- The listings source: `pipeline/sources/livrent.py`.
- Neighbourhoods and stations: `pipeline/geo.py` (the City's neighbourhoods, and `pipeline/data/ttc_subway_stations.json`).
- The place-lookup box: `SOUTH, WEST, NORTH, EAST` in `orchestrator/places.py`.
- The time zone and hours: `voice/dispatch.py`.
- Google's walking and transit minutes (D-10) work anywhere Google has the data.

---

## Investors' questions

### 8. Why now, and why you?

**Say:** "Speech-to-speech models only recently got fast and reliable enough to hold a real phone call and act on it: in our tests the caller starts answering about a second and a half after the other person stops talking, and it calls tools to record facts and book viewings mid-call. That turns the hours a renter spends phoning agents into something that costs about twenty cents a search. We built the whole loop ourselves: the listings data, the voice agents, the checking, and evals that tell us when a change makes it worse."

**Behind it:**
- Time to first audio: about 1.5 s from the end of speech (p50), in the Oct 24 voice eval (`docs/PITCH_NUMBERS.md`). It's stale, so say "in our tests" and its date if asked.
- Cost: about $0.20–$0.28 a search with three listings (`docs/PITCH_NUMBERS.md`).
- Who built what: the repo map in `AGENTS.md` (Arian: data, ranking, orchestration; Danial: voice, agents, evals; Mahyar: web, app, API).

**Not built yet:** any market sizing. Don't quote a market number unless it's sourced in the pitch deck.

### 9. What's defensible?

**Say:** "The voice models are a commodity, so our moat isn't there; it's what the calls learn. Every call adds facts about a listing in the agent's own words, with the date, so over time we hold a picture of the real rental market that no listing site has. The other part is trust: it works for the renter, never invents a fact and only books inside their times, which is what lets an employer or a university hand it their people."

**Behind it:**
- The shared listing memory: O-09a (`orchestrator/memory.py`, built; live run pending the SurrealDB keys).
- Grounding: hard rule 3.
- No booking outside the renter's windows: `agents/caller/tools.py` `book_viewing`, and the call eval in `evals/reports/latest.md`.

**Not built yet:** reusing an earlier call's facts in the next call (O-09b) is planned, not built. Today the facts are stored, not reused.

### 10. Isn't automated calling a regulatory risk?

**Say:** "Our calls don't sell the agent anything; they ask about the agent's own listing for a renter who asked us to, so Canada's telemarketing and do-not-call rules most likely don't apply. The CRTC's rules for automated calls apply whatever the purpose, and it's consulting right now on whether AI voices count, so we build to the strict reading: the AI says what it is in its first sentence, and calls one listing per request, in business hours. That first sentence already names us and says the call is recorded. Before we call anyone outside our team, it will also give a call-back number."

*Background for the team, with sources. This is not legal advice; get a lawyer's read before calling real agents.*

**Canada:**
- **The CRTC's rules.** The Unsolicited Telecommunications Rules have four parts: general provisions, the National Do Not Call List rules, the Telemarketing Rules, and the Automatic Dialing-Announcing Device (ADAD) Rules ([CRTC](https://crtc.gc.ca/eng/trules-reglest.htm)).
- **Telemarketing.** Telemarketing means calls "for the purpose of solicitation", that is, selling or promoting something or asking for money. The do-not-call and telemarketing rules "apply only to telemarketing" ([CRTC Notice 2026-132](https://crtc.gc.ca/eng/archive/2026/2026-132.htm)). A call asking an agent about their own listing sells them nothing. If a call ever pitched Landline to an agent, it would become telemarketing.
- **The ADAD Rules.** These are broader. They cover equipment that conveys "a pre-recorded or synthesized voice message", even when nothing is sold (2026-132). An AI voice plausibly counts, and the CRTC's open consultation asks exactly that: Q2 on AI voices, and Q10 on disclosing that "it is not a live person". Interventions closed in July 2026, with no decision as of 28 October.
- **What the ADAD Rules require of a call that sells nothing:**
  - Calls only on weekdays 9:00–21:30 and weekends 10:00–18:00, in the called person's time zone.
  - No calls to emergency or healthcare lines.
  - The call starts by "identifying the person on whose behalf the telecommunication is made" and its purpose.
  - It gives an email or postal address and a local or toll-free number that reaches a person, valid for 60 days.
  - The line is released within 10 seconds of a hang-up.
  - Sources: [CRTC rules](https://crtc.gc.ca/eng/trules-reglest.htm) and [CRTC telemarketing guidance](https://crtc.gc.ca/eng/phone/telemarketing/politi.htm). Check the exact wording on crtc.gc.ca before quoting it; our research read it through quotations, because the site blocked automated fetching.
- **Penalties.** Up to $15,000 per violation for a corporation ([Telecommunications Act s.72.01](https://laws-lois.justice.gc.ca/eng/acts/t-3.4/page-10.html)).
- **Recording.** One party's consent is enough under the Criminal Code ([s.184(2)(a)](https://laws-lois.justice.gc.ca/eng/acts/c-46/section-184.html)). PIPEDA still expects notice that the call is recorded, its purpose, and a choice ([OPC guidance](https://www.priv.gc.ca/en/privacy-topics/surveillance/02_05_d_14/)). PIPEDA governs in Ontario; its proposed successor, Bill C-36, is at second reading ([LEGISinfo](https://www.parl.ca/legisinfo/en/bill/45-1/c-36)).
- **The results text.** Under CASL, a text the renter asked for, about renting, likely needs no separate consent. It must still identify the sender, give contact details and offer a way to unsubscribe ([CASL s.6](https://laws-lois.justice.gc.ca/eng/acts/e-1.6/page-1.html)).

**If we went to the US:**
- **AI voices are "artificial" voices.** The FCC ruled in 2024 that AI voices fall under the TCPA ([FCC 24-17](https://docs.fcc.gov/public/attachments/FCC-24-17A1.pdf)). Calls to cell phones then need the called party's prior express consent, and the call must name who's calling and give a number ([47 CFR 64.1200](https://www.law.cornell.edu/cfr/text/47/64.1200)). A published listing number may not count as consent; that's unverified.
- **State laws.** California requires automated calls to say when the voice is AI-generated ([AB 2905](https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202320240AB2905)), and needs every party's consent to record ([Penal Code §632](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=PEN&sectionNum=632)).

**What we already do:**
- The AI says it's an AI in its first words (`agents/caller/brief.py`).
- It calls only numbers on our own allowlist (`DEMO_AGENT_PHONES`, `voice/outbound.py`).
- It calls one listing per renter request, in business hours (`voice/dispatch.py`), and never pitches anything.
- It ends every call once, with a deadline (hard rule 5).

**Not built yet (do these before calling anyone outside the team):**
1. A call-back number and email in the opening line. It already says "Hi, this is Landline. I'm an AI assistant calling for a renter about … This call is recorded." (#280), but a call back reaches the renter line until callbacks (A-13) are built.
2. Weekend hours of 10:00–18:00. `voice/dispatch.py` allows 9:00–20:00 every day, which is too wide on weekends.
3. A do-not-call list for agents who ask not to be called again.
4. STOP wording and contact details in the renter's texts, checked against CASL. They already start with "Landline:" (`voice/dispatch.py`, `voice/bridge.py`).
5. A lawyer's read, and a watch on the CRTC's 2026-132 decision.

---

*Draft by the team, Wed 28 Oct. Fix anything that's no longer true before Thursday 15:00's rehearsal.*
