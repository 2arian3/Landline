# Eval findings: the Concierge in simulation

What the simulated-call eval (E-01) found in the Concierge (A-04), what was fixed, and what is still open. Every number here comes from `evals/reports/latest.md` or from the traces of the runs named below. Measured on Sat 24 Oct 2026, between 01:15 and 01:50 Toronto time.

- First full run: harness only, before any fix (`de67523` plus the uncommitted harness, 05:17 UTC).
- Final run: after the fixes (`6f99149`, 05:44 UTC). PRs [#58](https://github.com/2arian3/Landline/pull/58) (harness) and [#68](https://github.com/2arian3/Landline/pull/68) (fixes).
- Returning callers: the welcome-back fix (`dde000c`, 05:59 UTC), which added one case to each set. That run's report sits next to `6f99149` in `evals/reports/latest.md`. Without the new cases: tune 16/24, holdout 11/15, both within noise of the run before.

## How the eval works

`make eval` runs the real Concierge on Higgs Realtime in **text mode**. That means the same prompt and tools, with `output_modalities: ["text"]`. The other side is a scripted renter whose lines are fixed, so a failure is the Concierge's. Tool calls go through the phone bridge's own `run_tool`, on an in-memory session runtime over a frozen copy of 400 listings. Each case runs 3 times and is scored **in code** from the session state at the end: preferences, reactions, focus, which listings to call, viewing windows, permission to book, and whether the call ended with `end_session` and a goodbye. A model grades nothing.

The cases are split into 8 **tune** cases, which prompts are changed against, and 5 **holdout** cases, which are reported. Details are in the `agent-evals` skill.

## Results

| | Holdout, first | Holdout, final | Tune, first | Tune, final |
|---|---|---|---|---|
| **Cases passing every run** | **0 / 5** | **4 / 5** | **0 / 8** | **3 / 8** |
| Runs passing | 27% | 80% | 12% | 75% |
| Reaction stored on the right listing | 3/9 | 6/9 | 2/9 | 8/9 |
| Viewing windows recorded | 5/6 | 5/5 | 2/6 | 6/6 |
| No booking allowed before the renter agreed | 1/3 | 3/3 | 0/3 | 3/3 |
| Ended with `end_session` | 10/15 | 15/15 | 13/24 | 19/24 |
| Said a goodbye before hanging up | not checked | 15/15 | not checked | 19/24 |
| Replies in the renter's language | 2/3 | 3/3 | 1/3 | 3/3 |
| Said it changed something with no tool call | 1 | 0 | 10 | 2 |
| Turns over two sentences | 20 of 84 | 19 of 84 | 49 of 132 | 51 of 132 |
| Agent turn, median (text, tools included) | 0.9 s | 1.0 s | 0.8 s | 0.9 s |

The same code scored 67% and 83% on the tune set in two runs in a row. At 3 runs per case, expect about ±15 points of noise. The moves above are well beyond that; smaller ones aren't.

## What the eval found

### Tool calls that saved nothing (fixed in code)

| # | Finding | Evidence | Fix |
|---|---|---|---|
| 1 | **Rank numbers sent as JSON numbers fail validation.** `listing_refs: [1, 2]` against a `list[str]` contract → "Sorry, I didn't catch that", and no plan is recorded. This may be why the U-06 run ended with no plan. | First trial run, `call-top3-saturday-book`: three `call_listings` in a row failed. | `tools.handle` turns numeric refs into words before validating. The contract is unchanged. |
| 2 | **Plan fields sent to the wrong tool are dropped silently.** The Concierge puts `availability` and `may_book` in `update_preferences`, Pydantic ignores keys it doesn't know, and the reply ("The list stays the same") sounds like success. In one run the viewing window went into `move_in` instead. | First full run: viewing windows 2/6 on tune. | A tool that gets arguments it doesn't take now answers "Not saved: availability, may_book; call call_listings with them." |
| 3 | **A second `call_listings` replaced the whole plan.** One without `listing_refs` fell back to the top three, and one without `availability` dropped the windows. Fix 2 makes the Concierge send its answers one at a time, so this would have lost them. | Found while fixing 2. | A second call updates the plan: omitted fields keep their values and questions add up. The reply reads back the windows and whether it may book. |

### Concierge behaviour (fixed in the prompt, tuned on tune only)

| # | Finding | First | Final |
|---|---|---|---|
| 4 | "Call the top three" records **one** address, sometimes while saying "I'll call the top three". | 2 runs | 0 runs. The prompt says ranks are "1", "2", "3". |
| 5 | `give_feedback` **without `listing_ref`**, so "the second one looks dark" gets "which one?" and nothing is stored. | reactions 5/18 | 14/18 |
| 6 | **`may_book: true` before the renter agreed**, often in the first `call_listings`. | 5 of 6 runs | 0 of 6 |
| 7 | **No `end_session`** after the renter says goodbye; the line stays open until they hang up. | 16 of 39 runs | 5 of 39 |
| 8 | **Says it changed something without a tool call**: "I've noted your interest", "I've saved your search". | 11 | 2 |
| 9 | **After switching to Farsi, it stops calling tools** and narrates in English ("I've updated your search to one bedroom…") while nothing is saved. | 0/3 runs pass | 2/3 runs pass. Still open: see below. |

### Bugs the fixes introduced, which the first scorer missed

These matter more than any single number. Both passed the scorer as it was, because it checked which tools were called, not what the renter would hear.

- **Silent hang-ups.** Asking for `end_session` "in the goodbye turn" together with "call the tool first" made the Concierge call `end_session` before saying anything. The bridge sends nothing after `end_session`, so in 4 runs in a row **every** call ended in silence (21/22, 21/22, 21/21, 36/36), against 0/23 before. Fixed in three places:
  - The prompt says the goodbye first.
  - `run_tool` asks for a goodbye when `end_session` came before any word.
  - The scorer fails a call that hangs up silently (`final.ended.goodbye`).
- **Goodbyes that greet.** The first version of that guard asked through `response.create` instructions. **21 of 21** of those goodbyes came out as "Hello! How can I help you today?" (see Higgs, below). Now the request goes in `end_session`'s tool answer, followed by a plain `response.create`. After that, 15 of 15 were real goodbyes, and the Mandarin ones were in Mandarin. The scorer now also fails a last turn that greets.

### Higgs behaviour, verified live

- **Text mode works for evals.** `output_modalities: ["text"]` is accepted with or without the `audio` block. Text arrives as `response.output_text.delta` and `.done` (the full text is in `text`), and tool events are the same as in audio. An agent's turn ends at the first `response.done` whose `output` has no `function_call`.
- **Event order in a response with text and then a tool call:** the text deltas stream first, then `response.function_call_arguments.done`, and only then `response.output_text.done` for that text. "Did it speak before the tool?" has to be read from deltas, not `.done`.
- **Instructions on `response.create` replace the session's instructions for that response**, and the model then answers as if the conversation so far weren't there. Two cases seen:
  - A goodbye asked for that way came out as a fresh greeting, 21 of 21 times.
  - The returning-caller greeting was sent the same way (`WELCOME_BACK`). With a remembered renter (Zara, 2 bedrooms, 88 Erskine, a Saturday viewing), **0 of 5 greetings used any of it, and 5 of 5 made a memory up** ("near Queen and Spadina", "near the 401 and Kennedy", "a yard for your dog"). **Fixed** (see "Returning callers" below).
- `response.function_call_arguments.done` carries `response_id`, and `response.done` carries `response.id`. That's how the bridge tells the silent `end_session` response from the goodbye after it.

### Returning callers (fixed)

Three openings were compared live, 5 runs each, on the Zara memory:

| Opening | Used the memory | Made one up | Tool calls before the first words |
|---|---|---|---|
| `WELCOME_BACK` as `response.create` instructions (before) | 0 of 5 | 5 of 5 | 0 |
| `WELCOME_BACK` in the session instructions, then a plain `response.create` | 4 of 5 | 0 | 2 (re-ran `update_preferences`) |
| The same, starting with a quoted "[The call just connected.]" user note **(now)** | 5 of 5 | 0 | 0 |

`voice/bridge.py` `opening()` builds a returning caller's session instructions (`INSTRUCTIONS + WELCOME_BACK + RETURNING + memory`) and the events that start the call. The simulator uses the same function. First-time callers still get `GREETING` as response instructions, which is fine, since there's nothing to remember.

The probe also showed **"$3,000" read as "thirty thousand"** in 3 of 5 welcomes, and in 3 of 5 again on the first eval runs. That led to a general speaking rule: "Say amounts the way they're written". Afterwards, 0 of 8.

Two cases now cover this. The tune case (`returning-results-ready`) checks that the welcome gives the booking and doesn't misread the budget. The holdout case (`returning-calls-running`) checks that it recalls the search and claims no results. Over 5 live runs each: **tune 4 of 5, holdout 5 of 5**. Across the 8 tune runs so far (5 plus 3 in `make eval`), the welcome gave the booking in 6 and made nothing up in any.

### Listing calls: the Caller (A-05) against landlord personas

Added Sat 24 Oct, 02:30–02:50 Toronto. Each run is one renter plan through the real Dispatcher. The sim runner (`voice/sim.py`) puts the Caller on one Higgs text session and a landlord persona on another; the persona has hidden facts, viewing times and questions of its own, and picks up first. Seven personas: 5 tune (honest, upselling, scam, a switch to Mandarin, voicemail) and 2 holdout (an honest agent where booking isn't allowed, and a different scam). The final run on `bcaab31`:

| | Tune (5 × 3) | Holdout (2 × 3) |
|---|---|---|
| Runs passing | 73% | 83% |
| Checklist coverage | 100% | 87% |
| Right card status, right booking (or none), red flags exactly when deserved | 15/15 | 6/6 |
| Bookings outside the renter's windows | 0 | 0 |
| Facts recorded with the right value | 27/30 | 11/12 |
| Landlord numbers read back | 13/34 | 10/16 |
| Questions code had to note after a promise to check | 6 | 5 |
| Median call | 7 Caller turns, 9 s | 8 turns, 9.5 s |

What the first runs found, in order:

- **Talking instead of recording.** The first version read every answer back and called `record_fact` 0 times in 3 calls, so every outcome was empty and the results said "Nobody picked up" for a call that went fine. The same "your tools are the only way anything is saved" rule that fixed the Concierge, plus a worked example of two facts in one turn, took it to 6 facts per call.
- **"Read it back" turned into a question**, so the landlord confirmed, and in one run it looped three times. Tool replies now say "repeat the number back in a few words, then ask …" and name the next item.
- **Scam calls never ended.** The Caller kept asking about future viewings until the turn cap. Then `end_call(done)` was refused by the checklist gate, and it restarted its introduction. Now a red flag lets `end_call` through at once, and code ends a call when the Caller repeats itself three turns running.
- **"I'll check with my client" with no `note_question`**, in about half the calls. As with the goodbye, code now backstops it: it notes the landlord's last question in their own words, counted separately in the report (11 times across 21 calls).
- **`available` recorded as "yes"**, though the quote held the date. The tool reply now says "They said when, so also record available_from."
- **A double booking caught.** Two calls in one plan both took Saturday at 11. The clash check refused the second, and the reply now names the taken time, so the Caller doesn't offer 10:30 against 11:00.
- **Persona and fixture problems, not the Caller's:**
  - A persona read "$150" as "fifteen hundred dollars", so personas now write amounts in digits.
  - Wei only switched to Mandarin once told "from your third reply on".
  - Cases first demanded one exact slot. They now accept any time on the right day inside the windows.
  - The scorer now counts "one hundred dollars" as 100. Once a landlord said an amount in words, the Caller copied it into its tool arguments despite the digits rule.

### Data that trips the agent

- **Addresses that several cards share can't be named.** The catalogue has 137 listings on D'Arcy Street and 26 on Carabob Court with no unit labels, so two "30 Carabob Court" cards can sit on one board. The Concierge repeats the address it heard from the board line ("30 Carabob Court moves to the top"), the ref matches two cards, and every call gets "which one?". In one holdout run that meant no plan at all. **Fixed in code, Sat 04:45** (see below).

### Concierge fixes, Sat 24 Oct 03:50–04:45 (A-04, `agents/A-04-concierge-fixes`)

**Shared addresses, fixed in code.** `refs.match` returns one card, or the cards an address fits equally. Among those, a rank on the board that is one of them ("30 Carabob Court, number 5"), a price only one of them has ("the 1,950 one"), or the focused card picks one. Otherwise the tool records nothing and answers "Which one? 3 places share 30 Carabob Court: number 1 at $1,999, number 5 at $1,950 and number 6 at $1,999. Ask the renter." The first of them is never taken by default, because a wrong card gets a stranger phoned. A plan's read-back adds the number when the board repeats an address. Unit tests cover each rule. Tune cases: `shared-address-ask-by-price` 3/3 (the renter picks by price after being asked), `shared-address-focused-then-call` 3/3 (the address alone means the focused card). On `main`'s code, run at the same time, both were 0/3.

The first version said "Ask, then use its number". The Concierge then reused the numbers in that reply as "the top two" (`["1", "6"]`, two Carabob cards), and holdout listings-to-call fell from 5/6 to 2/6. Holdout traces were read to find this, and the fix was to reword our own reply, not to tune against holdout.

**Pin taps.** The sim can now tap a pin (`{"tap": "rank:3"}` in a case): the runtime focuses it with `origin="ui"`, and the Concierge gets the same `[renter is looking at] …` note as on a live call (`voice/line.py`). Without a prompt line, 2 of 8 taps were stored as a reaction, and 3 of 8 replies said "noted your interest". A line that said "never say they like it or are interested in it" stopped the reactions, but put "your interest" into 2 of 5 goodbyes. Reworded ("A tap is not an opinion: call no tool for it, and say nothing about what they think of it"): 0 of 5 reactions, 0 of 5 "your interest", then `tap-is-not-a-like` 3/3 in the report run.

**"Looks great" is still a focus.** Prompt wording (praise is a reaction; "if they say anything good or bad about it, that is give_feedback") left "Ooh, the second one looks amazing" a focus in 6 of 6 probe runs. Adding "Did they praise or criticise it? Then call give_feedback" to `focus_listing`'s reply made it invent likes: "Tell me about the fifth one" got a like "for its location near the subway". Invented feedback is worse than a missed like, so it was removed. Holdout `cat-like-call-top2-friday` passed 3/3 in the final report but 1/5 in a 5-run check just before it, which is noise, not a fix.

**Farsi: a Higgs limit in text mode.**

| Probe (same tune lines unless said) | Tool calls after the switch | Replies in Persian |
|---|---|---|
| `main`'s prompt | 0 of 5 | 0 of 5 |
| Three prompt variants (a stronger rule, a worked Farsi example, the rule first) | 0 of 9 | 0 of 9 |
| Persian digits replaced by Latin ones | 0 of 3 | 0 of 3 |
| Starting the call in Farsi | 1 of 3 | 0 of 3 |
| The same lines in Spanish / Mandarin | 3 of 3 / 2 of 3 | 3 of 3 / 3 of 3 |
| A note after a turn that claimed a change with no tool | 1 of 3 | mixed |

In Farsi the model answers in English, as if it had translated the line ("I'll switch to Persian. I've noted you want a one-bedroom…"), with no text deltas and no tool call. Forcing `tool_choice: "required"` for the session loops until the turn times out.

**A `response` object on `response.create` wipes the context, even with no instructions in it.** `{"type": "response.create", "response": {"tool_choice": "required"}}` came back as "Hi! How can I help you find a place to rent?" in 3 of 3 runs. That's the same failure as response instructions, so send `response.create` bare mid-call.

**Catch-up, tried and dropped.** After each renter turn, a Higgs chat call with a JSON schema (`agents/auditor/llm.py`, Boson) read the renter's line, and code applied only what the tools had missed, through the same handlers. It saved the Farsi search in 5 of 5 runs and "looks amazing" as a like in 5 of 5. A quoted note afterwards made the following replies Persian, but the switch turn itself stayed English. Over the full tune set it cut runs passing to 33% (61% with it off, same time):
- False updates: "It'll just be me living there" became `min_beds: 0` (studios), and "the one that's 1,950 a month" became a $1,950 budget, which then lined up the wrong listing for a call.
- More missed `end_session`: the note sits just before the renter's goodbye.
- `429 Too Many Requests` from `api.boson.ai/v1/chat/completions` three times in one run. That endpoint shares the key, and its limits, with live calls.

| Final report (`make eval`, 3 runs) | Holdout | Tune |
|---|---|---|
| Before (`dde000c`, Sat 02:00) | 4/6 cases, 78% of runs | 2/9 cases, 63% |
| After (this branch) | 4/6 cases, 78% of runs | 6/13 cases, 74% |
| Same time, same key, holdout 5 runs each | this branch 77%, 3/6 cases | `main` 57%, 1/6 cases |

### Any language, Sat 24 Oct 09:40–10:30 (A-04, `agents/A-04-multilingual`)

The goal changed from "Farsi and English" to any language a renter speaks. Higgs detects the spoken language and replies in kind (docs.boson.ai); nothing in our code was tied to Farsi except the eval. What changed:
- **Prompt:** renters may speak any language; tool replies and `[…]` notes are English and are never read out as is; the first non-English line sets `language` to its two-letter code, whichever language it is. The greeting says they can talk in any language.
- **Returning callers** are welcomed back in their stored language, and it goes to Higgs as the transcription hint (`bridge.hint`).
- **The results text** is translated into the renter's language (`voice/translate.py`) and code checks that every number in it was in the English, as often (Persian and Arabic digits read the same). Otherwise, or on a timeout, the English goes. Live on Higgs: Spanish, Korean, Chinese, Punjabi and Arabic translated with every number kept; Farsi took 14 to 30 s, so the limit is 25 s.
- **Catch-up for lines in another script** (`agents/concierge/backstop.py`): after a switch to Korean or Farsi the Concierge answers in that language ("a two-bedroom up to $3,000, got it") and calls no tool. When a renter line is mostly in a non-Latin script and the turn ended with no tool call, a text model reads that line alone and `update_preferences`' own handler saves it. Latin-script lines never reach it, which is where the dropped catch-up above made its false updates; it adds no note to the conversation, which is what cost it the goodbyes.

New tune cases: `spanish-renter`, `arabic-renter`, `punjabi-renter` (each speaks it from the first line) and `korean-switch`. Text mode, 3 runs each, same hour:

| 5 language cases, 15 runs | `main` | prompt only | prompt + catch-up |
|---|---|---|---|
| Preferences saved right | 12/15 | 10/15 | **15/15** (9 catch-ups, all right) |
| Replies in the renter's language | 7/15 (Arabic 0/3, Punjabi 0/3) | 12/15 | 11/15 |
| `end_session` called | 11/15 | 11/15 | 11/15 |
| Runs passing | 40% | 47% | 53% |

The whole tune set with the prompt change: 51% of runs. The failures outside the new cases are the missed `end_session` (16 of 51), as on `main` this morning (item 10 below).

On the phone path (the real bridge, 8 kHz μ-law; the renter is Higgs TTS saying a brief, a pet and goodbye; one run each, so a probe, not a score):

| Language | Prompt only: replies in it / saved right (budget, beds, pet, language) | Prompt + catch-up |
|---|---|---|
| Chinese | yes / 4 of 4 | not rerun |
| Spanish | yes / 3 of 4 (the dog was missed) | not rerun |
| Korean | yes / 4 of 4 | English / 4 of 4 |
| Arabic | English / 3 of 4 (the cat line was misheard) | English / 3 of 4 |
| Punjabi | English / 3 of 4 (transcribed as romanized Punjabi, "$10", so the budget was a guess) | English / 3 of 4 |
| Farsi | English / **0 of 4**, while saying "I've noted your budget", then a made-up dog and parking | English / **4 of 4** |

Higgs transcribed Farsi, Korean, Chinese and Arabic well at 8 kHz, and Punjabi badly. For a demo where the renter hears replies in their language, Chinese and Spanish are the safe picks today; in Farsi the map and the texted results follow, but the voice answers in English.

## Still open

1. **A returning caller whose calls are still running isn't told so.** None of the 5 holdout welcomes mentioned it; they recall the search instead. The case doesn't check for it yet. Also, about 1 in 4 welcomes skips the booking.
2. **"The first one looks great" is taken as a focus, not a like.** Still open after Sat 04:45: 0 of 6 in a probe whatever the prompt says. See below.
3. ~~Shared addresses never resolve.~~ Fixed in code (Sat 04:45). Unit labels from the pipeline would still read better than "number 5".
4. **Farsi, Arabic and Punjabi replies are often English** (text and phone audio). The preferences are saved now (the catch-up, Sat 10:30), but the Concierge may answer in English. Spanish, Korean and Chinese reply in kind. See "Any language" above.
5. **About a quarter of turns run past two sentences**, mostly by reading the whole board line back.
6. **Viewing times made up once**: "today 10 am to 6 pm", before the renter gave any.
7. **The "claims with no tool call" check reads English only.** The Farsi "I added your cat", with no tool called, wasn't counted.
8. **Read-backs (mostly fixed, Sat 04:25).** The old count (13/34 tune, 10/16 holdout) also counted numbers in the landlord's own questions ("want a locker for $60?") and goodbyes. The real misses: a landlord turn with an answer and a question, where the last tool reply ("tell them you'll check") won over the read-back, and two numbers in one turn with only one read back. Tool replies now carry every read-back owed for the turn. On `cfb384e`: saved numbers read back 23/24 tune, 12/14 holdout; landlord turns with a number read back 25/47 tune, 14/20 holdout.
9. **The Caller leans on the code backstop for `note_question`** (11 of 21 calls; on `cfb384e` 20 notes by code in 35 calls, now also when the landlord repeats a question the Caller ignored). An upsell question ("would your client want a locker?") gets recorded as a fact instead of brought back as a question.
10. **`end_session` is missed after about a third of tune goodbyes today** (Sat 04:30, on `main`'s code too: 10 of 36 runs), against about 1 in 4 on Friday night. The renter hears a goodbye, but the line stays open until they hang up.
11. **`give_feedback` without a ref isn't resent.** The reply now says "call give_feedback again with listing_ref" instead of asking the renter, but in holdout `too-expensive-then-call-default` the Concierge moved on without resending it.
12. **Rank numbers go stale after a re-rank.** A like moved the renter's card from 5 to 6, the Concierge sent "5", and a different listing was lined up for a call. A prompt rule ("use the street words for a listing you already talked about") made it send addresses for "the first one" too, so it was reverted. A fix in code would resolve ranks against the board the renter last heard.
13. **Audio (measured Sat 04:57, `evals/run_audio.py`).** Time to first audio is about 1.5 s from the end of the caller's voice on both paths (about 0.6 s of it is the VAD's end-of-speech wait). Barge-in recovers 12 of 12. Two problems found:
    - **The interrupted reply isn't trimmed.** Higgs sends a reply's audio in one burst, so the reply is already `completed` when the caller talks over it (0 of 12 cancelled), and the bridge sends no `conversation.item.truncate`. The model then believes it said the whole reply, though the caller heard about 1.6 s. Sending `truncate` with the played ms on `speech_started` works: Higgs acknowledged 6 of 6 with that exact `audio_end_ms`. **Open** (a small change in `voice/bridge.py` and `voice/phone_caller.py`).
    - **Two tool calls in one reply send two `response.create`s.** `run_tool` asks for a response after each, and Higgs rejects the second with 400 "No user input". Harmless (one reply comes out) but noisy in the logs. **Open.**
    - Speech-to-text on real voices isn't measured: the clips are synthesized.

## Lessons for the next eval

- **Score what the renter hears and what gets saved, not which tools ran.** Both bugs the fixes introduced passed a tool-shaped check.
- **Read the tool replies in the traces.** "Which one?" and "didn't catch that" mean the agent thinks it acted when nothing changed.
- **Three runs per case is noisy.** Use `--runs 5` or more before trusting a change under about 15 points.
- **Tune with `--no-report`**, so the report keeps comparing against the last full run. The write-up quotes holdout only.
- **A holdout fixture fix gets disclosed.** One was made: "that's too small for us" now accepts `reject` as well as `dislike`.

## Reproduce

```bash
make eval                                                   # both sets, 3 runs each, about 1.5 min
uv run python -m evals.run_concierge --set tune --no-report # a tuning run that leaves the report alone
uv run python -m evals.run_concierge --replay               # the recorded run, no key needed
```

Each run's traces go to `evals/runs/<stamp>/<set>/<case>-<n>.json`, which holds the score and the full trace: turns, tool calls with arguments and replies, the board each one saw, and every Higgs event.
