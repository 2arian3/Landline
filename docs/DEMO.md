# Demo and submission

## What Boson requires

From the hackathon page:

- **Project information:** name, track, a one-sentence description, and a write-up of up to 200 words (problem, audience, how it works).
- **Demo video:** 2–3 minutes showing the project running, on a public link with no access request. A live public demo URL is optional, but "if the judges can't open it, they can't score it."
- **Repository:** public GitHub repo with source, setup instructions, and example inputs and outputs. It must be pinned to a commit SHA or tagged release; anything pushed later won't be seen. Remove API keys.
- **How you built it** (≤ 200 words): each Higgs model or API used, its role, and what's novel or hard.
- **Team:** each contributor's GitHub handle and what they built, plus known limitations.

Scoring: Use of Higgs Audio 30 · User Experience 25 · Innovation 25 · Technical Execution 20. Primary track: **03 Agents That Act**.

## The 2:30 video

| Time | On screen and in audio | Shows |
|---|---|---|
| 0:00 | One line: Toronto listings say things that aren't true, and newcomers get it worst | The problem |
| 0:10 | A renter calls, mixing Farsi and English. The SMS link opens. Hundreds of dots fade to six numbered pins while they talk | Higgs Realtime, language switching, live map |
| 0:40 | "Natural light matters most." The pins renumber before the sentence ends. They tap a pin, and the agent says "That's the corner unit on the 14th floor" | Voice and screen in sync |
| 0:55 | "That one's too dark." The card shows *You: too dark*, and darker units sink | Feedback changes ranking |
| 1:05 | "Call the top three." Three pins pulse while the agent keeps talking. We listen in: one landlord switches to Mandarin, one adds $180 parking, one asks for an e-transfer deposit (flagged) | Parallel Higgs calls, the board, scam check |
| 1:40 | A landlord asks the move-in date; the agent says "let me check with my client", and the renter answers on their own line | Agents that consult each other |
| 1:55 | Results land on the pins; the agent sums up in Farsi; the renter books Saturday; SMS confirmation | End to end |
| 2:10 | Hang up, call again: "Welcome back, Sara. Two new places match since this morning." | Memory by phone number |
| 2:20 | Numbers: extraction accuracy, zero ungrounded facts, time to first audio, listings and sources | Technical execution |

Record the phone parts on real phones, with teammates as landlords. Use sim landlords only for the listen-in shot if needed, and say so.

## Rehearsal checklist (Sat 12:30–15:00)

- [ ] `FORCE_BUSINESS_HOURS=1` on the VM, or business hours in Toronto time
- [ ] `DEMO_AGENT_PHONES` holds teammates only; the allowlist test passes
- [ ] Higgs concurrency ≥ 5 confirmed; enough credits for rehearsal and judging
- [ ] A switch from `TRANSPORT=voice` to `sim` is one env change and one restart
- [ ] A backup recording plays offline on a laptop
- [ ] `/health` green over cellular; no tunnels
- [ ] Judge mode works from a clean browser on another network
- [ ] `gitleaks detect` clean; `.env` not in git history
- [ ] Repo public; tag `submission` on the final SHA; the SHA pasted into the form
- [ ] VM and Vercel stay up until judging ends
