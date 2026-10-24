---
name: agent-evals
description: How Landline measures its agents, covering extraction accuracy, ranking quality, call behaviour with simulated landlords, latency, and the report the submission cites. Use when working in evals/, adding a fixture or landlord persona, tuning a prompt, or when someone asks whether a change made things better.
---

# Agent evals

Prompts change only when an eval shows they got better. Every number in the submission write-up comes from `evals/reports/latest.md`.

## Layout

```
evals/
  sim/driver.py                     # one conversation: the real agent on Higgs (text) vs an Opponent
  sim/cases.py score_concierge.py   # Concierge case format and scorer
  sim/replay.py                     # plays a recorded run back in place of Higgs (make test)
  run_concierge.py report.py        # make eval; the report
  fixtures/listings.json            # frozen catalogue, so a new scrape can't move the boards
  fixtures/concierge/*.json         # tune cases
  fixtures/holdout/concierge/*.json # holdout cases: the reported numbers
  fixtures/recordings/*.jsonl       # recorded Higgs events for the replay test
  reports/concierge.json latest.md  # committed; latest.md is rebuilt from the JSON
  runs/<stamp>/                     # traces, not committed
  # still to come: fixtures/transcripts (E-02), fixtures/renters (E-03), fixtures/personas (E-04)
```

## Concierge sim (E-01)

The real Concierge (prompt and tools) runs on Higgs in text mode against a `ScriptedRenter`. Tool calls go through `voice/bridge.py` `run_tool` on an in-memory `SessionRuntime`, the same code path as a phone call. A case passes when its `final` checks hold on the session state at the end. `expect` checks on a line are reported as "on time". The format is in `evals/sim/cases.py`. Listing refs are symbolic so a case tests the Concierge, not the ranker: `rank:N` and `top:N` against the board the renter saw before line `at`, and `addr:words`. A request's listings resolve against the board `call_listings` saw. `reaction` may be a list when either reading is fair.

```bash
make eval                                                          # tune + holdout, 3 runs each
uv run python -m evals.run_concierge --set tune --no-report        # tuning: leaves the report alone
uv run python -m evals.run_concierge --set tune --case farsi-switch --runs 1
uv run python -m evals.run_concierge --replay                      # recordings only, no key
```

A trace (`evals/runs/<stamp>/<set>/<case>-<n>.json`) holds the score and everything that happened: each line and reply, each tool call with its arguments, its reply and the board it saw, the state after each line, and every Higgs event both ways. When a check fails, read the tool replies first. "Which one do you mean?" and "didn't catch that" mean the agent thinks it acted and nothing changed.

The Caller sim (E-04, not built yet) is meant to reuse the driver, with a landlord-persona opponent (a second Higgs text session, no tools) in place of the scripted renter.

## Fixture format (transcripts)

```json
{"id": "fees-two-spots", "listing": {"address": "322 Dupont St", "rent": 3290},
 "turns": [{"speaker": "agent", "text": "..."}, {"speaker": "landlord", "text": "...", "lang": "en"}],
 "gold": {"available": true, "fees": [{"name": "parking", "amount": 180, "period": "monthly", "mandatory": false}],
          "pets": "cats_only"}}
```

Cover at least these cases: two parking spots at a per-spot price, a one-time deposit next to a monthly fee, "no pets allowed", "cats only, no dogs", a landlord who switches to Mandarin mid-call, an agent sentence that sounds like a fact ("listed at $2,690 with parking, right?"), a read-back the landlord corrects, two scam calls (deposit before viewing, owner abroad), and a no-answer.

## Metrics

| Eval | Metric | Bar |
|---|---|---|
| Concierge (sim) | cases passing every run; runs passing; each check family; on-time checks; hygiene (long turns, claims with no tool, ids read out) | holdout runs ≥ 0.8; booking allowed before the renter agreed = 0; silent or greeting goodbyes = 0 |
| Extraction | precision and recall per field; **ungrounded values shown** | ungrounded = 0; precision ≥ 0.9 |
| Ranking | hit@3 on renter briefs; monotonicity; stability; feedback turns to converge | hit@3 ≥ 0.8 |
| Calls (sim) | checklist coverage; read-back rate on numbers; call length; time to first audio; recovery after barge-in | coverage ≥ 0.9 |
| Dispatcher | hang-up → first dial time; plan completion time; bookings outside the renter's windows | bookings outside windows = 0 |

## Landlord personas (sim)

Each persona has a voice, a language, a manner and hidden facts: honest; upsells parking; a Mandarin speaker who switches mid-call; a scammer who wants an e-transfer before viewing; one who never answers (tests AMD and voicemail). At least one asks something the `CallPlan` can't answer (tests `note_question`), and at least one offers a viewing time outside the renter's windows (tests that `book_viewing` refuses it). Personas speak first when the call connects, like a real person picking up.

## Rules

- Never tune a prompt against a fixture and then report that same fixture as the result. Keep `fixtures/holdout/` for the final numbers. Tune with `--no-report`, so the report keeps comparing against the last full run. If a holdout fixture itself is wrong, fix it and say so in the PR.
- Report regressions: the report shows the previous run's numbers next to the current ones.
- **Score what the person hears and what gets saved, not which tools ran.** Two prompt fixes passed a tool-shaped check while every call ended in silence, and then on "Hello! How can I help you today?". `final.ended.goodbye` exists because of that.
- **Three runs per case moves about ±15 points.** The same code scored 67% and 83% on tune in two runs in a row. Use `--runs 5` or more before trusting a smaller change.
- Run the report with a clean working tree. The report records the git SHA, with `+dirty` when there are uncommitted changes.

## Agent problems found so far

See `docs/EVAL_FINDINGS.md` for evidence and status. Known weak spots to write cases for:
- Rank refs said as numbers.
- Plan fields sent to the wrong tool.
- `may_book` set before the renter agrees.
- Goodbyes without `end_session`, or `end_session` with no goodbye.
- Language switches that stop tool calls (Farsi).
- Addresses two cards share.
- "Looks great" taken as a focus instead of a like.
- A returning caller's greeting that invents memories.
