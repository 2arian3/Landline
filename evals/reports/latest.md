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

## Not measured yet

- Extraction (E-02) and ranking (E-03).
- Listing calls against landlord personas (E-04): needs the Caller (A-05). It will use the same driver, with a second Higgs session playing the landlord.
- Anything audio: time to first audio, barge-in, speech-to-text errors. Text mode can't see them; they need the audio sim transport (A-03).
- Claims in languages other than English: the "said it changed something" check reads English only.
