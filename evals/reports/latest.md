# Landline eval report

Built by `make eval` (`evals/run_concierge.py`, then `evals/report.py`) from `evals/reports/concierge.json`. Every number in the submission comes from this file. Quote the holdout set: prompts are changed against the tune set only.

## Concierge: simulated calls in text

The real Concierge (its prompt and tools) on Higgs Realtime in text mode, talking to a scripted renter whose lines are fixed, so a failure is the Concierge's. Its tools run through the phone bridge's own `run_tool` on an in-memory session runtime over a frozen catalogue. A case passes when the session at the end is right: preferences, reactions, focus, which listings to call, the viewing windows, permission to book, and that it ended with `end_session`. Each case runs several times; a model never grades anything.

### Holdout (quote these)

2026-10-24T05:44:20+00:00 · `6f99149` · 5 cases × 3 runs · live text · listings.json (400 listings)

| | This run | Previous |
|---|---|---|
| **Cases passing every run** | **4 / 5** | **0 / 5** |
| Runs passing | 80% | 27% |
| Checks on time (in the turn they belong to) | 48/51 (94%) | 45/51 (88%) |
| Preferences recorded | 39/39 (100%) | 37/39 (95%) |
| Reaction on the right listing | 6/9 (67%) | 3/9 (33%) |
| Focused listing | 3/3 (100%) | 3/3 (100%) |
| A call request recorded | 5/6 (83%) | — |
| Listings to call | 5/5 (100%) | 5/6 (83%) |
| Viewing windows | 5/5 (100%) | 5/6 (83%) |
| Permission to book | 5/5 (100%) | 6/6 (100%) |
| No booking allowed before the renter said so | 3/3 (100%) | 1/3 (33%) |
| No calls when none were asked for | 3/3 (100%) | 3/3 (100%) |
| Replies in the renter's language | 3/3 (100%) | 2/3 (67%) |
| Ended with end_session after the goodbye | 15/15 (100%) | 10/15 (67%) |
| Said goodbye before hanging up | 15/15 (100%) | — |
| Turns over two sentences | 19 of 84 turns | 20 of 84 turns |
| Said it changed something, with no tool call | 0 | 1 |
| Tool replies "which one?" (ref didn't resolve) | 4 | 3 |
| Agent turn, median / p90 (text, tools included) | 1.0 s / 1.2 s | 0.9 s / 1.3 s |
| How runs ended | end_session 15 | end_session 10, script_done 5 |

| Case | Runs passing | Failing most often |
|---|---|---|
| `cat-like-call-top2-friday` | 0/3 | `final.reaction.rank:1` ×3; `final.request` ×1 |
| `mandarin-renter` | 3/3 | — |
| `parking-must-and-focus` | 3/3 | — |
| `studio-near-work` | 3/3 | — |
| `too-expensive-then-call-default` | 3/3 | — |

Traces: `evals/runs/20261024T054318Z` (not committed).

### Tune (prompts change against these)

2026-10-24T05:44:20+00:00 · `6f99149` · 8 cases × 3 runs · live text · listings.json (400 listings)

| | This run | Previous |
|---|---|---|
| **Cases passing every run** | **3 / 8** | **0 / 8** |
| Runs passing | 75% | 12% |
| Checks on time (in the turn they belong to) | 67/69 (97%) | 55/69 (80%) |
| Preferences recorded | 44/45 (98%) | 36/45 (80%) |
| Reaction on the right listing | 8/9 (89%) | 2/9 (22%) |
| Focused listing | 6/6 (100%) | 6/6 (100%) |
| Map moved to the area | 3/3 (100%) | 3/3 (100%) |
| A call request recorded | 6/6 (100%) | — |
| Listings to call | 6/6 (100%) | 5/6 (83%) |
| Viewing windows | 6/6 (100%) | 2/6 (33%) |
| Permission to book | 6/6 (100%) | 5/6 (83%) |
| Renter's extra questions | 3/3 (100%) | 2/3 (67%) |
| No booking allowed before the renter said so | 3/3 (100%) | 0/3 (0%) |
| No calls when none were asked for | 9/9 (100%) | 9/9 (100%) |
| Replies in the renter's language | 3/3 (100%) | 1/3 (33%) |
| Ended with end_session after the goodbye | 19/24 (79%) | 13/24 (54%) |
| Said goodbye before hanging up | 19/24 (79%) | — |
| Turns over two sentences | 51 of 132 turns | 49 of 132 turns |
| Said it changed something, with no tool call | 2 | 10 |
| Tool replies "which one?" (ref didn't resolve) | 4 | 7 |
| Agent turn, median / p90 (text, tools included) | 0.9 s / 1.2 s | 0.8 s / 1.2 s |
| How runs ended | end_session 19, script_done 5 | end_session 13, script_done 11 |

| Case | Runs passing | Failing most often |
|---|---|---|
| `ambiguous-street-then-rank` | 2/3 | `final.reaction.rank:1` ×1; `final.ended` ×1; `final.ended.goodbye` ×1 |
| `budget-beds-goodbye` | 2/3 | `final.ended` ×1; `final.ended.goodbye` ×1 |
| `call-named-no-book-parking` | 3/3 | — |
| `call-top3-saturday-book` | 3/3 | — |
| `change-mind-budget` | 1/3 | `final.ended` ×2; `final.ended.goodbye` ×2 |
| `farsi-switch` | 2/3 | `final.prefs.pets` ×1 |
| `feedback-light-and-like` | 3/3 | — |
| `focus-rank-street-area` | 2/3 | `final.ended` ×1; `final.ended.goodbye` ×1 |

Traces: `evals/runs/20261024T054318Z` (not committed).

## Not measured yet

- Extraction (E-02) and ranking (E-03).
- Listing calls against landlord personas (E-04): needs the Caller (A-05). It will use the same driver, with a second Higgs session playing the landlord.
- Anything audio: time to first audio, barge-in, speech-to-text errors. Text mode can't see them; they need the audio sim transport (A-03).
- Claims in languages other than English: the "said it changed something" check reads English only.
