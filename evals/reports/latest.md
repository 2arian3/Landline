# Landline eval report

Built by `make eval` (`evals/run_concierge.py`, then `evals/report.py`) from `evals/reports/concierge.json`. Every number in the submission comes from this file. Quote the holdout set: prompts are changed against the tune set only.

## Concierge: simulated calls in text

The real Concierge (its prompt and tools) on Higgs Realtime in text mode, talking to a scripted renter whose lines are fixed, so a failure is the Concierge's. Its tools run through the phone bridge's own `run_tool` on an in-memory session runtime over a frozen catalogue. A case passes when the session at the end is right: preferences, reactions, focus, which listings to call, the viewing windows, permission to book, and that it ended with `end_session`. Each case runs several times; a model never grades anything.

### Holdout (quote these)

2026-10-24T05:17:02+00:00 · `de67523+dirty` · 5 cases × 3 runs · live text · listings.json (400 listings)

| | This run | Previous |
|---|---|---|
| **Cases passing every run** | **0 / 5** | — |
| Runs passing | 27% | — |
| Checks on time (in the turn they belong to) | 45/51 (88%) | — |
| Preferences recorded | 37/39 (95%) | — |
| Reaction on the right listing | 3/9 (33%) | — |
| Focused listing | 3/3 (100%) | — |
| Listings to call | 5/6 (83%) | — |
| Viewing windows | 5/6 (83%) | — |
| Permission to book | 6/6 (100%) | — |
| No booking allowed before the renter said so | 1/3 (33%) | — |
| No calls when none were asked for | 3/3 (100%) | — |
| Replies in the renter's language | 2/3 (67%) | — |
| Ended with end_session after the goodbye | 10/15 (67%) | — |
| Turns over two sentences | 20 of 84 turns | — |
| Said it changed something, with no tool call | 1 | — |
| Tool replies "which one?" (ref didn't resolve) | 3 | — |
| Agent turn, median / p90 (text, tools included) | 0.9 s / 1.3 s | — |
| How runs ended | end_session 10, script_done 5 | — |

| Case | Runs passing | Failing most often |
|---|---|---|
| `cat-like-call-top2-friday` | 0/3 | `final.reaction.rank:1` ×3; `final.may_book.not_before_permission` ×2 |
| `mandarin-renter` | 0/3 | `final.prefs.language` ×2; `final.ended` ×2; `final.replies_in.zh` ×1 |
| `parking-must-and-focus` | 2/3 | `final.reaction.rank:2` ×1; `final.ended` ×1 |
| `studio-near-work` | 2/3 | `final.ended` ×1 |
| `too-expensive-then-call-default` | 0/3 | `final.reaction.rank:1` ×2; `final.request.availability` ×1; `final.request.listings` ×1 |

Traces: `evals/runs/20261024T051601Z` (not committed).

### Tune (prompts change against these)

2026-10-24T05:17:02+00:00 · `de67523+dirty` · 8 cases × 3 runs · live text · listings.json (400 listings)

| | This run | Previous |
|---|---|---|
| **Cases passing every run** | **0 / 8** | — |
| Runs passing | 12% | — |
| Checks on time (in the turn they belong to) | 55/69 (80%) | — |
| Preferences recorded | 36/45 (80%) | — |
| Reaction on the right listing | 2/9 (22%) | — |
| Focused listing | 6/6 (100%) | — |
| Map moved to the area | 3/3 (100%) | — |
| Listings to call | 5/6 (83%) | — |
| Viewing windows | 2/6 (33%) | — |
| Permission to book | 5/6 (83%) | — |
| Renter's extra questions | 2/3 (67%) | — |
| No booking allowed before the renter said so | 0/3 (0%) | — |
| No calls when none were asked for | 9/9 (100%) | — |
| Replies in the renter's language | 1/3 (33%) | — |
| Ended with end_session after the goodbye | 13/24 (54%) | — |
| Turns over two sentences | 49 of 132 turns | — |
| Said it changed something, with no tool call | 10 | — |
| Tool replies "which one?" (ref didn't resolve) | 7 | — |
| Agent turn, median / p90 (text, tools included) | 0.8 s / 1.2 s | — |
| How runs ended | end_session 13, script_done 11 | — |

| Case | Runs passing | Failing most often |
|---|---|---|
| `ambiguous-street-then-rank` | 0/3 | `final.reaction.rank:1` ×3 |
| `budget-beds-goodbye` | 0/3 | `final.ended` ×3 |
| `call-named-no-book-parking` | 0/3 | `final.request.availability` ×3; `final.request.may_book` ×1; `final.request.question.parking` ×1 |
| `call-top3-saturday-book` | 0/3 | `final.may_book.not_before_permission` ×3; `final.request.listings` ×1; `final.request.availability` ×1 |
| `change-mind-budget` | 0/3 | `final.ended` ×3 |
| `farsi-switch` | 0/3 | `final.prefs.pets` ×3; `final.prefs.max_monthly` ×2; `final.prefs.min_beds` ×2 |
| `feedback-light-and-like` | 1/3 | `final.reaction.rank:2` ×2; `final.reaction.rank:3` ×2; `final.ended` ×2 |
| `focus-rank-street-area` | 2/3 | `final.ended` ×1 |

Traces: `evals/runs/20261024T051601Z` (not committed).

## Not measured yet

- Extraction (E-02) and ranking (E-03).
- Listing calls against landlord personas (E-04): needs the Caller (A-05). It will use the same driver, with a second Higgs session playing the landlord.
- Anything audio: time to first audio, barge-in, speech-to-text errors. Text mode can't see them; they need the audio sim transport (A-03).
- Claims in languages other than English: the "said it changed something" check reads English only.
