# Landline eval report

Built by `make eval` (`evals/run_concierge.py`, then `evals/report.py`) from `evals/reports/concierge.json`. Every number in the submission comes from this file. Quote the holdout set: prompts are changed against the tune set only.

## Concierge: simulated calls in text

The real Concierge (its prompt and tools) on Higgs Realtime in text mode, talking to a scripted renter whose lines are fixed, so a failure is the Concierge's. Its tools run through the phone bridge's own `run_tool` on an in-memory session runtime over a frozen catalogue. A case passes when the session at the end is right: preferences, reactions, focus, which listings to call, the viewing windows, permission to book, and that it ended with `end_session`. Each case runs several times; a model never grades anything.

### Holdout (quote these)

2026-10-24T08:42:35+00:00 · `9ca2759` · 6 cases × 3 runs · live text · listings.json (400 listings)

| | This run | Previous |
|---|---|---|
| **Cases passing every run** | **4 / 6** | **3 / 6** |
| Runs passing | 78% | 78% |
| Checks on time (in the turn they belong to) | 45/51 (88%) | 48/51 (94%) |
| Preferences recorded | 42/48 (88%) | 48/48 (100%) |
| Reaction on the right listing | 6/9 (67%) | 6/9 (67%) |
| Focused listing | 3/3 (100%) | 3/3 (100%) |
| A call request recorded | 6/6 (100%) | 6/6 (100%) |
| Listings to call | 3/6 (50%) | 5/6 (83%) |
| Viewing windows | 5/6 (83%) | 6/6 (100%) |
| Permission to book | 6/6 (100%) | 6/6 (100%) |
| No booking allowed before the renter said so | 3/3 (100%) | 3/3 (100%) |
| No calls when none were asked for | 6/6 (100%) | 6/6 (100%) |
| Replies in the renter's language | 3/3 (100%) | 3/3 (100%) |
| Welcome back named what was remembered | 3/3 (100%) | 3/3 (100%) |
| Welcome back said nothing it didn't know | 6/6 (100%) | 6/6 (100%) |
| Ended with end_session after the goodbye | 18/18 (100%) | 17/18 (94%) |
| Said goodbye before hanging up | 18/18 (100%) | 17/18 (94%) |
| Turns over two sentences | 47 of 90 turns | 46 of 90 turns |
| Said it changed something, with no tool call | 2 | 0 |
| Tool replies "which one?" (ref didn't resolve) | 2 | 3 |
| Agent turn, median / p90 (text, tools included) | 0.9 s / 1.1 s | 0.9 s / 1.1 s |
| How runs ended | end_session 18 | end_session 17, script_done 1 |

| Case | Runs passing | Failing most often |
|---|---|---|
| `cat-like-call-top2-friday` | 3/3 | — |
| `mandarin-renter` | 2/3 | `final.prefs.max_monthly` ×1; `final.prefs.min_beds` ×1; `final.prefs.pets` ×1 |
| `parking-must-and-focus` | 3/3 | — |
| `returning-calls-running` | 3/3 | — |
| `studio-near-work` | 3/3 | — |
| `too-expensive-then-call-default` | 0/3 | `final.reaction.rank:1` ×3; `final.request.listings` ×3; `final.prefs.max_monthly` ×1 |

Traces: `evals/runs/20261024T084106Z` (not committed).

### Tune (prompts change against these)

2026-10-24T08:42:35+00:00 · `9ca2759` · 13 cases × 3 runs · live text · listings.json (400 listings)

| | This run | Previous |
|---|---|---|
| **Cases passing every run** | **6 / 13** | **4 / 13** |
| Runs passing | 74% | 51% |
| Checks on time (in the turn they belong to) | 90/99 (91%) | 84/99 (85%) |
| Preferences recorded | 46/54 (85%) | 42/54 (78%) |
| Reaction on the right listing | 15/18 (83%) | 14/18 (78%) |
| Focused listing | 12/12 (100%) | 12/12 (100%) |
| Map moved to the area | 3/3 (100%) | 3/3 (100%) |
| A call request recorded | 12/12 (100%) | 12/12 (100%) |
| Listings to call | 12/12 (100%) | 11/12 (92%) |
| Viewing windows | 12/12 (100%) | 12/12 (100%) |
| Permission to book | 12/12 (100%) | 12/12 (100%) |
| Renter's extra questions | 3/3 (100%) | 3/3 (100%) |
| No booking allowed before the renter said so | 6/6 (100%) | 6/6 (100%) |
| No calls when none were asked for | 18/18 (100%) | 18/18 (100%) |
| Replies in the renter's language | 1/3 (33%) | 0/3 (0%) |
| Welcome back named what was remembered | 4/6 (67%) | 5/6 (83%) |
| Welcome back said nothing it didn't know | 3/3 (100%) | 3/3 (100%) |
| Ended with end_session after the goodbye | 34/39 (87%) | 28/39 (72%) |
| Said goodbye before hanging up | 34/39 (87%) | 28/39 (72%) |
| Turns over two sentences | 123 of 212 turns | 115 of 213 turns |
| Said it changed something, with no tool call | 6 | 11 |
| Tool replies "which one?" (ref didn't resolve) | 7 | 7 |
| Agent turn, median / p90 (text, tools included) | 0.9 s / 1.2 s | 0.9 s / 1.2 s |
| How runs ended | end_session 34, script_done 5 | end_session 28, script_done 11 |

| Case | Runs passing | Failing most often |
|---|---|---|
| `ambiguous-street-then-rank` | 3/3 | — |
| `budget-beds-goodbye` | 3/3 | — |
| `call-named-no-book-parking` | 3/3 | — |
| `call-top3-saturday-book` | 2/3 | `final.ended` ×1; `final.ended.goodbye` ×1 |
| `change-mind-budget` | 2/3 | `final.ended` ×1; `final.ended.goodbye` ×1 |
| `farsi-switch` | 1/3 | `final.prefs.max_monthly` ×2; `final.prefs.min_beds` ×2; `final.prefs.pets` ×2 |
| `feedback-light-and-like` | 2/3 | `final.reaction.rank:2` ×1 |
| `focus-rank-street-area` | 2/3 | `final.ended` ×1; `final.ended.goodbye` ×1 |
| `like-looks-amazing-dislike-far` | 0/3 | `final.reaction.rank:2` ×2; `final.ended` ×2; `final.ended.goodbye` ×2 |
| `returning-results-ready` | 2/3 | `final.greeting.says.Erskine` ×1; `final.greeting.says.Saturday` ×1 |
| `shared-address-ask-by-price` | 3/3 | — |
| `shared-address-focused-then-call` | 3/3 | — |
| `tap-is-not-a-like` | 3/3 | — |

Traces: `evals/runs/20261024T084106Z` (not committed).

## Listing calls: the Caller against landlord personas (text)

One renter plan per run through the real Dispatcher: the Caller (its brief, prompt and tools) phones one listing through the sim runner, where a second Higgs session plays a landlord persona with hidden facts, viewing times and questions of its own. Scored in code from the session afterwards: the card's status, the booking and its window, red flags, the questions noted, and each expected fact's value. Personas in `holdout/` never tune the prompt.

### Holdout (quote these)

2026-10-24T08:28:27+00:00 · `cfb384e` · 2 personas × 5 runs · live text

| | This run | Previous |
|---|---|---|
| **Personas passing every run** | **1 / 2** | **1 / 2** |
| Runs passing | 90% | 83% |
| Checklist coverage | 24/25 (96%) | 13/15 (87%) |
| Landlord turns with a number, read back next turn | 14/20 (70%) | 10/16 (62%) |
| Numbers the Caller saved, read back in that turn | 12/14 (86%) | — |
| Quotes refused as not said (ungrounded) | 0 | 0 |
| Questions code noted after the Caller promised to check | 9 | 5 |
| Said it's an AI assistant first | 10/10 (100%) | 6/6 (100%) |
| Card ends with the right status | 10/10 (100%) | 6/6 (100%) |
| Booked the expected slot, or nothing | 10/10 (100%) | 6/6 (100%) |
| Red flags exactly when the landlord's words deserve them | 10/10 (100%) | 6/6 (100%) |
| Noted the question it couldn't answer | 5/5 (100%) | 3/3 (100%) |
| Ended the right way | 10/10 (100%) | 6/6 (100%) |
| Facts recorded with the right value | 19/20 (95%) | 11/12 (92%) |
| Call, median: Caller turns / seconds to results | 7 / 9.6 s | 8 / 9.5 s |
| How calls ended | done 9, hangup 1 | done 5, hangup 1 |

| Persona | Runs passing | Card status | Failing most often |
|---|---|---|---|
| `omar-scam-abroad` | 5/5 | suspect 5 | — |
| `priya-no-booking` | 4/5 | verified 5 | `final.fact.laundry` ×1 |

Traces: `evals/runs/20261024T082307Z-calls` (not committed).

### Tune (prompts change against these)

2026-10-24T08:28:27+00:00 · `cfb384e` · 5 personas × 5 runs · live text

| | This run | Previous |
|---|---|---|
| **Personas passing every run** | **3 / 5** | **3 / 5** |
| Runs passing | 88% | 73% |
| Checklist coverage | 83/85 (98%) | 51/51 (100%) |
| Landlord turns with a number, read back next turn | 25/47 (53%) | 13/34 (38%) |
| Numbers the Caller saved, read back in that turn | 23/24 (96%) | — |
| Quotes refused as not said (ungrounded) | 0 | 1 |
| Questions code noted after the Caller promised to check | 11 | 6 |
| Said it's an AI assistant first | 24/25 (96%) | 15/15 (100%) |
| Card ends with the right status | 24/25 (96%) | 15/15 (100%) |
| Booked the expected slot, or nothing | 24/25 (96%) | 15/15 (100%) |
| Bookings inside the renter's windows | 14/14 (100%) | 9/9 (100%) |
| Red flags exactly when the landlord's words deserve them | 25/25 (100%) | 15/15 (100%) |
| Noted the question it couldn't answer | 10/10 (100%) | 4/6 (67%) |
| Ended the right way | 25/25 (100%) | 15/15 (100%) |
| Facts recorded with the right value | 49/50 (98%) | 27/30 (90%) |
| Call, median: Caller turns / seconds to results | 7 / 10.1 s | 7 / 9.0 s |
| How calls ended | done 18, hangup 7 | done 12, hangup 3 |

| Persona | Runs passing | Card status | Failing most often |
|---|---|---|---|
| `linda-honest` | 5/5 | booked 5 | — |
| `mark-scam` | 5/5 | suspect 5 | — |
| `raj-upsell` | 4/5 | booked 5 | `final.disclosed` ×1 |
| `sam-voicemail` | 5/5 | no_answer 5 | — |
| `wei-mandarin` | 3/5 | booked 4, verified 1 | `final.fact.pets` ×1; `final.status` ×1; `final.booked` ×1 |

Traces: `evals/runs/20261024T082307Z-calls` (not committed).

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

## Matcher: ranking renter briefs

The real ranking (`server/landline/scoring/`, with feedback from `orchestrator/feedback.py`) on 10 renter briefs over the frozen catalogue (`evals/run_ranking.py`), through the session runtime. Each brief's acceptable listings are labelled from criteria written in its fixture, not from the ranker. **hit@3**: one of them is in the top 3. **Turns to converge**: the renter keeps disliking the top card for a reason the brief didn't give ("too small") until a listing with what they want reaches the top 3, at most 5 turns. Pure code: the same numbers every run, no key.

### Holdout (quote these)

2026-10-24T08:30:50+00:00 · `dabed61` · 3 briefs · listings.json

| | Now | Previous |
|---|---|---|
| **hit@3** | 100% | — |
| Precision@3 | 89% | — |
| Converged after feedback | 2 / 3 (1 mean, 1 max) | — |
| Monotonicity violations | 0 | — |
| Stability violations | 2 | — |

| Brief | hit@3 | Precision@3 | Turns (reason) |
|---|---|---|---|
| dog-near-tmu | yes | 1.00 | 1 (too small) |
| liberty-couple | yes | 1.00 | 1 (too small) |
| scarborough | yes | 0.67 | not converged (too far) |

Violations:

- dog-near-tmu: 'too small' on the top card left it first
- scarborough: 'too far' on the top card left it first

### Tune (prompts change against these)

2026-10-24T08:30:50+00:00 · `dabed61` · 7 briefs · listings.json

| | Now | Previous |
|---|---|---|
| **hit@3** | 100% | — |
| Precision@3 | 76% | — |
| Converged after feedback | 2 / 7 (2 mean, 3 max) | — |
| Monotonicity violations | 0 | — |
| Stability violations | 11 | — |

| Brief | hit@3 | Precision@3 | Turns (reason) |
|---|---|---|---|
| cheapest-studio | yes | 1.00 | 1 (too small) |
| family-space | yes | 0.33 | not converged (too expensive) |
| one-bed-space | yes | 1.00 | not converged (too expensive) |
| three-bed-value | yes | 0.33 | not converged (too expensive) |
| union-commuter | yes | 1.00 | 3 (too small) |
| uoft-student | yes | 0.67 | not converged (too small) |
| yonge-eglinton | yes | 1.00 | not converged (too small) |

Violations:

- cheapest-studio: 'too small' on the top card left it first
- family-space: 'too expensive' on the top card left it first
- one-bed-space: 'too expensive' on the top card left it first
- three-bed-value: 'too expensive' on the top card left it first
- union-commuter: 'too small' on the top card left it first
- union-commuter: 'too small' moved livrent:108451 above livrent:139609, which is no worse on size
- union-commuter: 'too small' moved livrent:130282 above livrent:139609, which is no worse on size
- union-commuter: 'too small' moved livrent:100747 above livrent:139609, which is no worse on size
- uoft-student: 'too small' on the top card left it first
- uoft-student: 'too small' moved livrent:125599 above livrent:137404, which is no worse on size
- yonge-eglinton: 'too small' on the top card left it first

### Checks

- Light twins (a brighter copy never loses): pass
- "Too dark" on a dark top card (a small catalogue where light varies): **fail**: too-dark: 'too dark' on the top card left it first
- Ranking time, warm: p50 0.103 ms, p95 0.153 ms on 400 listings (cold 1.1 ms); p50 1.632 ms, p95 2.847 ms on 4000. Target < 5 ms.

## Voice: real audio on Higgs Realtime

The real voice paths, unchanged (`evals/run_audio.py`): the Concierge through the bridge on 24 kHz PCM (the browser), the Caller through the phone runner on μ-law (Twilio's format). The other side streams speech in real time with silence between lines, as a phone line does. Its lines are clips synthesized with Higgs TTS (`higgs-tts-v3`), not recordings of people. Time to first audio runs from the end of the other side's voice to the agent's first audio chunk (what a caller waits through, including the VAD's end-of-speech wait), and from Higgs' `speech_stopped` to it. A barge-in talks over a reply 1.2 s in. It counts when Higgs hears it within 2 s, the audio is cleared, and the next reply picks up what was said (its words or its tool arguments).

2026-10-24T08:56:30+00:00 · `674e37d` · 8 sessions · clips synthesized with Higgs TTS (higgs-tts-v3, voice default)

| | p50 / p90 | Previous p50 / p90 |
|---|---|---|
| Concierge greeting: `response.create` → first audio | 508 / 515 ms (n=2) | — |
| **Concierge (browser): end of the renter's voice → first audio** | 1,472 / 1,804 ms (n=12) | — |
| Concierge: `speech_stopped` → first audio | 922 / 1,141 ms (n=12) | — |
| **Caller (phone): end of the landlord's voice → first audio** | 1,516 / 2,344 ms (n=20) | — |
| Caller: `speech_stopped` → first audio | 960 / 1,687 ms (n=20) | — |

| Barge-in | This run |
|---|---|
| **Recovered** (heard, cleared, next reply answers it) | **12 / 12** |
| Voice onset → `speech_started` | 292 / 342 ms (n=12) |
| Voice onset → audio cleared at the caller | 292 / 342 ms (n=12) |
| Next reply picked up the interruption | 12 / 12 |
| Interrupted reply's response cancelled by the server | 0 / 12 |
| `conversation.item.truncate` sent by our code | 0 / 12 |
| Agent audio played before the cut | 1,594 / 1,656 ms (n=12) |

With `conversation.item.truncate` sent on `speech_started` (tried in the harness only; the bridge doesn't send it yet):

| | This run |
|---|---|
| Recovered | 6 / 6 |
| Truncates sent / acknowledged by Higgs (`conversation.item.truncated`) | 6 / 6 |
| Higgs' `audio_end_ms` equal to what had played | 6 / 6 |
| Errors | none |

| Caller call on audio | Length | Caller turns | Ended | Facts | Booked | Red flags |
|---|---|---|---|---|---|---|
| `linda-honest` | 94 s | 9 | hangup | 6 | yes | 0 |
| `raj-upsell` | 100 s | 8 | hangup | 3 | yes | 0 |
| `mark-scam` | 70 s | 5 | refused | 1 | no | 3 |

Timelines: `evals/runs/20261024T084935Z-audio` (not committed).

## Not measured yet

- Speech-to-text errors on real voices: the audio sim speaks synthesized clips.
- Claims in languages other than English: the "said it changed something" check reads English only.
