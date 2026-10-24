---
name: submission
description: Packaging Landline for the Boson AI Higgs Audio Hackathon, covering the required fields, the 200-word write-ups, the demo video, repo hygiene, pinning the SHA and keeping the demo up for judges. Use at feature freeze (Sat 12:30), when writing the README or write-ups, or when anyone asks what is left to ship.
---

# Submission

Full requirements and the video script are in `docs/DEMO.md`.

## The order of work after freeze

1. **Video first** (U-09). Record real phones; cut to 2:30; upload as public or unlisted with no access request; test the link in a private window.
2. **Numbers** (E-05) from `evals/reports/latest.md`, the only source for any figure we state.
3. **Write-ups** (U-10), each ≤ 200 words:
   - *Project:* the problem (listings that aren't true; newcomers hit hardest), who it's for (Toronto renters), how it works in three sentences.
   - *How we built it:* each Higgs model and its role (Realtime for the renter and every listing call, with function tools and mid-sentence language switching; TTS for voicemail; Avatar for the debrief if shipped), and what was hard (parallel live calls run from a frozen brief that book viewings on their own, grounded extraction, the call lifecycle).
4. **Team:** GitHub handles and what each built. Known limitations, stated plainly (snapshot data, demo phone numbers only, City data covers larger rental buildings only).
5. **Repo hygiene:** `gitleaks detect`; `.env.example` complete; README has setup steps and example input and output (a transcript and the resulting card); the repo is public.
6. **Pin it:** `git tag submission && git push origin submission`; paste the SHA into the form.
7. Keep the VM and Vercel up until judging ends; check `/health` from a phone.

## The first sentence

Open with the inversion, not "a voice assistant for apartments":

> Every voice agent in real estate works for the brokerage. Landline works for the renter: it phones the listing agents for you, in your language, and shows you on a live map which listings are actually true.

## Honesty rules

- Say what was built before the event and what during it. Ported Realest pieces are listed in `docs/PORTING.md`.
- If the video uses simulated landlords anywhere, say so on screen.
- Never state a number that isn't in the eval report.
