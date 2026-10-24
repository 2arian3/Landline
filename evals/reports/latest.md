# Landline eval report

Built by `make eval` (`evals/run_concierge.py`, then `evals/report.py`) from `evals/reports/concierge.json`. Every number in the submission comes from this file. Quote the holdout set: prompts are changed against the tune set only.

## Concierge: simulated calls in text

The real Concierge (its prompt and tools) on Higgs Realtime in text mode, talking to a scripted renter whose lines are fixed, so a failure is the Concierge's. Its tools run through the phone bridge's own `run_tool` on an in-memory session runtime over a frozen catalogue. A case passes when the session at the end is right: preferences, reactions, focus, which listings to call, the viewing windows, permission to book, and that it ended with `end_session`. Each case runs several times; a model never grades anything.

### Holdout (quote these)

2026-10-24T06:00:25+00:00 · `dde000c` · 6 cases × 3 runs · live text · listings.json (400 listings)

| | This run | Previous |
|---|---|---|
| **Cases passing every run** | **4 / 6** | **4 / 5** |
| Runs passing | 78% | 80% |
| Checks on time (in the turn they belong to) | 48/51 (94%) | 48/51 (94%) |
| Preferences recorded | 48/48 (100%) | 39/39 (100%) |
| Reaction on the right listing | 6/9 (67%) | 6/9 (67%) |
| Focused listing | 3/3 (100%) | 3/3 (100%) |
| A call request recorded | 6/6 (100%) | 5/6 (83%) |
| Listings to call | 5/6 (83%) | 5/5 (100%) |
| Viewing windows | 6/6 (100%) | 5/5 (100%) |
| Permission to book | 6/6 (100%) | 5/5 (100%) |
| No booking allowed before the renter said so | 2/3 (67%) | 3/3 (100%) |
| No calls when none were asked for | 6/6 (100%) | 3/3 (100%) |
| Replies in the renter's language | 3/3 (100%) | 3/3 (100%) |
| Welcome back named what was remembered | 3/3 (100%) | — |
| Welcome back said nothing it didn't know | 6/6 (100%) | — |
| Ended with end_session after the goodbye | 17/18 (94%) | 15/15 (100%) |
| Said goodbye before hanging up | 17/18 (94%) | 15/15 (100%) |
| Turns over two sentences | 28 of 90 turns | 19 of 84 turns |
| Tool replies "which one?" (ref didn't resolve) | 1 | 4 |
| Agent turn, median / p90 (text, tools included) | 0.9 s / 1.2 s | 1.0 s / 1.2 s |
| How runs ended | end_session 17, script_done 1 | end_session 15 |

| Case | Runs passing | Failing most often |
|---|---|---|
| `cat-like-call-top2-friday` | 0/3 | `final.reaction.rank:1` ×3; `final.may_book.not_before_permission` ×1; `final.ended` ×1 |
| `mandarin-renter` | 3/3 | — |
| `parking-must-and-focus` | 3/3 | — |
| `returning-calls-running` | 3/3 | — |
| `studio-near-work` | 3/3 | — |
| `too-expensive-then-call-default` | 2/3 | `final.request.listings` ×1 |

Traces: `evals/runs/20261024T055922Z` (not committed).

### Tune (prompts change against these)

2026-10-24T06:00:25+00:00 · `dde000c` · 9 cases × 3 runs · live text · listings.json (400 listings)

| | This run | Previous |
|---|---|---|
| **Cases passing every run** | **2 / 9** | **3 / 8** |
| Runs passing | 63% | 75% |
| Checks on time (in the turn they belong to) | 57/69 (83%) | 67/69 (97%) |
| Preferences recorded | 42/54 (78%) | 44/45 (98%) |
| Reaction on the right listing | 9/9 (100%) | 8/9 (89%) |
| Focused listing | 6/6 (100%) | 6/6 (100%) |
| Map moved to the area | 3/3 (100%) | 3/3 (100%) |
| A call request recorded | 6/6 (100%) | 6/6 (100%) |
| Listings to call | 6/6 (100%) | 6/6 (100%) |
| Viewing windows | 6/6 (100%) | 6/6 (100%) |
| Permission to book | 6/6 (100%) | 6/6 (100%) |
| Renter's extra questions | 3/3 (100%) | 3/3 (100%) |
| No booking allowed before the renter said so | 3/3 (100%) | 3/3 (100%) |
| No calls when none were asked for | 12/12 (100%) | 9/9 (100%) |
| Replies in the renter's language | 0/3 (0%) | 3/3 (100%) |
| Welcome back named what was remembered | 5/6 (83%) | — |
| Welcome back said nothing it didn't know | 3/3 (100%) | — |
| Ended with end_session after the goodbye | 20/27 (74%) | 19/24 (79%) |
| Said goodbye before hanging up | 20/27 (74%) | 19/24 (79%) |
| Turns over two sentences | 51 of 138 turns | 51 of 132 turns |
| Said it changed something, with no tool call | 10 | 2 |
| Tool replies "which one?" (ref didn't resolve) | 3 | 4 |
| Agent turn, median / p90 (text, tools included) | 0.8 s / 1.1 s | 0.9 s / 1.2 s |
| How runs ended | end_session 20, script_done 7 | end_session 19, script_done 5 |

| Case | Runs passing | Failing most often |
|---|---|---|
| `ambiguous-street-then-rank` | 3/3 | — |
| `budget-beds-goodbye` | 2/3 | `final.ended` ×1; `final.ended.goodbye` ×1 |
| `call-named-no-book-parking` | 2/3 | `final.ended` ×1; `final.ended.goodbye` ×1 |
| `call-top3-saturday-book` | 3/3 | — |
| `change-mind-budget` | 2/3 | `final.ended` ×1; `final.ended.goodbye` ×1 |
| `farsi-switch` | 0/3 | `final.prefs.max_monthly` ×3; `final.prefs.min_beds` ×3; `final.prefs.pets` ×3 |
| `feedback-light-and-like` | 2/3 | `final.ended` ×1; `final.ended.goodbye` ×1 |
| `focus-rank-street-area` | 2/3 | `final.ended` ×1; `final.ended.goodbye` ×1 |
| `returning-results-ready` | 1/3 | `final.ended` ×1; `final.ended.goodbye` ×1; `final.greeting.says.Saturday` ×1 |

Traces: `evals/runs/20261024T055922Z` (not committed).

## Listing calls: the Caller against landlord personas (text)

One renter plan per run through the real Dispatcher: the Caller (its brief, prompt and tools) phones one listing through the sim runner, where a second Higgs session plays a landlord persona with hidden facts, viewing times and questions of its own. Scored in code from the session afterwards: the card's status, the booking and its window, red flags, the questions noted, and each expected fact's value. Personas in `holdout/` never tune the prompt.

### Holdout (quote these)

2026-10-24T06:46:38+00:00 · `bcaab31` · 2 personas × 3 runs · live text

| | This run | Previous |
|---|---|---|
| **Personas passing every run** | **1 / 2** | — |
| Runs passing | 83% | — |
| Checklist coverage | 13/15 (87%) | — |
| Landlord numbers read back | 10/16 (62%) | — |
| Quotes refused as not said (ungrounded) | 0 | — |
| Questions code noted after the Caller promised to check | 5 | — |
| Said it's an AI assistant first | 6/6 (100%) | — |
| Card ends with the right status | 6/6 (100%) | — |
| Booked the expected slot, or nothing | 6/6 (100%) | — |
| Red flags exactly when the landlord's words deserve them | 6/6 (100%) | — |
| Noted the question it couldn't answer | 3/3 (100%) | — |
| Ended the right way | 6/6 (100%) | — |
| Facts recorded with the right value | 11/12 (92%) | — |
| Call, median: Caller turns / seconds to results | 8 / 9.5 s | — |
| How calls ended | done 5, hangup 1 | — |

| Persona | Runs passing | Card status | Failing most often |
|---|---|---|---|
| `omar-scam-abroad` | 3/3 | suspect 3 | — |
| `priya-no-booking` | 2/3 | verified 3 | `final.fact.available_from` ×1 |

Traces: `evals/runs/20261024T064504Z-calls` (not committed).

### Tune (prompts change against these)

2026-10-24T06:46:38+00:00 · `bcaab31` · 5 personas × 3 runs · live text

| | This run | Previous |
|---|---|---|
| **Personas passing every run** | **3 / 5** | — |
| Runs passing | 73% | — |
| Checklist coverage | 51/51 (100%) | — |
| Landlord numbers read back | 13/34 (38%) | — |
| Quotes refused as not said (ungrounded) | 1 | — |
| Questions code noted after the Caller promised to check | 6 | — |
| Said it's an AI assistant first | 15/15 (100%) | — |
| Card ends with the right status | 15/15 (100%) | — |
| Booked the expected slot, or nothing | 15/15 (100%) | — |
| Bookings inside the renter's windows | 9/9 (100%) | — |
| Red flags exactly when the landlord's words deserve them | 15/15 (100%) | — |
| Noted the question it couldn't answer | 4/6 (67%) | — |
| Ended the right way | 15/15 (100%) | — |
| Facts recorded with the right value | 27/30 (90%) | — |
| Call, median: Caller turns / seconds to results | 7 / 9.0 s | — |
| How calls ended | done 12, hangup 3 | — |

| Persona | Runs passing | Card status | Failing most often |
|---|---|---|---|
| `linda-honest` | 3/3 | booked 3 | — |
| `mark-scam` | 3/3 | suspect 3 | — |
| `raj-upsell` | 0/3 | booked 3 | `final.note_question` ×2; `final.fact.pets` ×1 |
| `sam-voicemail` | 3/3 | no_answer 3 | — |
| `wei-mandarin` | 2/3 | booked 3 | `final.fact.available_from` ×1; `final.fact.laundry` ×1 |

Traces: `evals/runs/20261024T064504Z-calls` (not committed).

## Auditor: facts from call transcripts

The real Auditor (`server/landline/agents/auditor/`) on 20 written call transcripts with gold facts (`evals/run_extraction.py`): tricky fees, pets, a landlord who switches to Mandarin, two scams, read-backs where the agent's own words must not count, a no-answer and a voicemail. **Rules** is the path with no model: the Caller's live `record_fact`s, some of them wrong on purpose, checked against the landlord's turns, plus the red-flag rules and the booked viewing. **With extractor** adds the text LLM's facts, which go through the same quote check. **Ungrounded** is facts shown whose quote no landlord said, re-checked outside the Auditor; the bar is 0. Scored in code.

### Holdout (quote these)

2026-10-24T07:56:31+00:00 · `f905e7f` · 8 transcripts · extractor: replayed recordings

| | Rules | With extractor | Previous (best mode) |
|---|---|---|---|
| **Ungrounded facts shown** | **0 of 11** | **0 of 17** | **0 of 17** |
| Precision | 100% | 100% | 100% |
| Recall | 65% | 91% | 91% |
| Transcripts fully right | 3 / 8 | 6 / 8 | 6 / 8 |
| Claims refused (quote not a landlord's, or the words say otherwise) | 0 | ungrounded 1 | ungrounded 1 |

| Field | Rules P / R | With extractor P / R |
|---|---|---|
| ac | — / 0% (0/1) | 100% / 100% (1/1) |
| available | 100% / 17% (1/6) | 100% / 83% (5/6) |
| move-in date | 100% / 100% (2/2) | 100% / 100% (2/2) |
| fees | 100% / 100% (2/2) | 100% / 100% (2/2) |
| lease length | — / 0% (0/1) | — / 0% (0/1) |
| parking | 100% / 100% (1/1) | 100% / 100% (1/1) |
| parking price | 100% / 50% (1/2) | 100% / 100% (2/2) |
| pets | 100% / 100% (1/1) | 100% / 100% (1/1) |
| red flags | 100% / 100% (3/3) | 100% / 100% (3/3) |
| rent | 100% / 100% (2/2) | 100% / 100% (2/2) |
| utilities included | 100% / 100% (1/1) | 100% / 100% (1/1) |
| viewing time | 100% / 100% (1/1) | 100% / 100% (1/1) |

### Tune (prompts change against these)

2026-10-24T07:56:31+00:00 · `f905e7f` · 12 transcripts · extractor: replayed recordings

| | Rules | With extractor | Previous (best mode) |
|---|---|---|---|
| **Ungrounded facts shown** | **0 of 21** | **0 of 29** | **0 of 29** |
| Precision | 96% | 100% | 100% |
| Recall | 69% | 94% | 94% |
| Transcripts fully right | 5 / 12 | 10 / 12 | 10 / 12 |
| Claims refused (quote not a landlord's, or the words say otherwise) | agent_words 3 | agent_words 3, unreadable 1 | agent_words 3, unreadable 1 |

| Field | Rules P / R | With extractor P / R |
|---|---|---|
| available | 100% / 46% (5/11) | 100% / 91% (10/11) |
| move-in date | 100% / 67% (2/3) | 100% / 67% (2/3) |
| fees | 100% / 100% (2/2) | 100% / 100% (2/2) |
| laundry | 100% / 100% (2/2) | 100% / 100% (2/2) |
| lease length | 100% / 100% (1/1) | 100% / 100% (1/1) |
| parking price | 100% / 67% (2/3) | 100% / 100% (3/3) |
| pets | 100% / 50% (2/4) | 100% / 100% (4/4) |
| red flags | 100% / 100% (5/5) | 100% / 100% (5/5) |
| rent | 67% / 67% (2/3) | 100% / 100% (3/3) |
| utilities included | 100% / 100% (1/1) | 100% / 100% (1/1) |

## Not measured yet

- Ranking (E-03).
- Anything audio: time to first audio, barge-in, speech-to-text errors. Text mode can't see them; they need the audio sim transport (A-03).
- Claims in languages other than English: the "said it changed something" check reads English only.
