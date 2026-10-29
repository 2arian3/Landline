# Demo

## Demo Day: the live run of show

Build North Demo Day, Thu 29 Oct, live demos 18:00–19:30. One renter calls Landline on stage, and two real phones ring as listing agents. In three minutes the room sees a voice agent that searches, calls, books, remembers and sends the invites, with every fact traced to who said it.

Status of each beat: **verified live** means it worked on a real call or a live test. **First run at 15:00** means it's merged and tested in code but has never run on a real phone. **Don't show** means it isn't built or isn't reliable.

### What the judges should leave with

- **It acts.** It phones real people in parallel, books inside the renter's times, and sends both calendar invites once the agent confirms.
- **It can be trusted.** Every fact on the board carries the agent's exact words. It flags a scam deposit and tells the renter to skip it.
- **It remembers.** The next call to a listing starts from what an earlier call heard, so agents are asked to confirm, not asked cold.

### Cast and stage

| Role | Who | Phone | On screen |
|---|---|---|---|
| Presenter | Arian | none | Talks over the board. Owns the hook, the numbers and the close |
| Renter | A teammate (Danial confirms, X-03e) | A third phone, **not** …3369 or …3462, on speaker. Signed in on `/me` with aryan.boukani2@gmail.com | The laptop on the projector opens the board link the renter is texted |
| Agent A | Teammate | One allowed number, on speaker | Mirrored when tapping the confirm link |
| Agent B | Teammate | The other allowed number, with carrier voicemail switched on | Only shown ringing |
| Screen | Mahyar | none | Board full screen, the renter's inbox in a second tab, the backup video ready offline |

The renter calls Landline at `+1 289-512-2338`. Listing calls only run from 09:00 to 20:00, so the 18:00 slot is inside the window.

### Before the doors open

**By 12:00**
- Final ship, tagged `demo-day`. No deploys after that: a restart between the renter's plan and the listing calls loses the memory fetched for the call.
- Pause the refresh: `sudo systemctl stop landline-refresh.timer`. Start it again after the event.
- Check Twilio's balance and send one test SMS.

**By the 15:00 rehearsal**
- **Lock the brief.** Say the exact stage brief once and note which listing comes first. That's the one Agent A plays.
- **Seed the memory.** Make one call to that listing in which the agent says "Parking is $150 a month." The stage call then confirms it. It needs a call within the last 7 days, and the fee must be recorded as parking.
- **Clean renter phone.** No earlier Landline calls from it. Email set on `/me`.
- Run the 3-minute script twice with no failures, over the hotspot.

Rehearse the beats marked "first run at 15:00" first: the confirm link, the invites and the call-back have never run on a real phone yet. If one fails at 15:00, cut it from the stage script rather than fix it in the afternoon.

### The 3-minute script

| Time | Said on stage | What the room sees | Shows | If it fails |
|---|---|---|---|---|
| 0:00 | **Presenter:** "Renting in Toronto means calling agents who never call back, about listings that leave out what matters. About one rental condo in a hundred is empty (CMHC, Dec 2025). Landline is a voice agent that works for the renter. Let's find a place." | The board, idle, over the map of Toronto | The problem | None needed |
| 0:15 | **Renter, on speaker:** "Hi. Two bedrooms under twenty-six hundred. I work at Cohere, I have a dog, and I drive." | The board link arrives by text and opens on the projector. Pins re-sort with "min by TTC to Cohere" on the cards | Live map, commute ranking. Verified live | Board doesn't move: use "Near King and John" as the place |
| 0:35 | **Renter:** "Is there a Shoppers near the first one?" | The agent may say "Checking the map…" first, then the name and the minutes. A text arrives with the Google Maps link | Google Maps MCP. Verified live | Skip it. Say "it answers area questions from Google Maps" |
| 0:50 | **Renter:** "Call the top two. I'm free Saturday between one and five, and you can book." | The agent reads the plan back. The renter says "Thanks, bye" and hangs up | The brief becomes a plan. Verified live | No plan: "Call the first one and book Saturday afternoon" |
| 1:05 | **Phones ring.** Agent A answers on speaker; Agent B lets it ring. Caller: "Hi, this is Landline. I'm an AI assistant calling for a renter about … This call is recorded." | Both pins start calling at the same moment. The detail view shows the live captions | Parallel real calls. Verified live | No ring in 15 s: switch to the backup video |
| 1:20 | **Caller:** "Yesterday they said parking is $150 a month. Is that still right?" **Agent A:** "Yes, still 150. Dogs are fine. Saturday at two works." | Facts land on the card, each with the agent's words. The card shows the viewing, waiting for the agent | Agent memory (SurrealDB), booking. Verified live | No confirm line: say "on a second call it confirms what it heard" |
| 1:45 | **Presenter:** "The second agent didn't pick up. Landline left a voicemail with a number to call back, and that call goes straight to the agent working this listing." | Agent B's phone, voicemail left. The pin shows no answer | Voicemail and call-back. First run at 15:00 | Don't place the call-back live in the 3-minute version |
| 2:05 | **Agent A** taps the confirm link in the text, types an email, presses Confirm | The card flips to confirmed without a reload. Both calendar invites arrive, and the renter's tab shows it. The renter gets "Confirmed: …" | It acts end to end. First run at 15:00 | Link fails: "the agent confirms from a link, and both invites go out" |
| 2:30 | **Presenter:** "The renter's text has what the calls found. Every fact says who said it, word for word. That's what makes it safe to act on." | The results text, and a card's facts with their quotes | Trust and provenance. Verified live | Show the card's facts instead |
| 2:45 | **Presenter:** "In testing: 19 real listing calls, 49 facts, every one with the agent's exact words. Eleven to twenty-eight cents a search. Landline is the renter's agent. We're Team AAMEL." | The board with the booked, confirmed card | Numbers, close | None needed |

### If the slot is five minutes

Add these after 2:30, in this order, and drop any that failed at 15:00.

| Beat | Said on stage | What the room sees | Shows |
|---|---|---|---|
| Call-back | Agent B calls Landline's number back: "Hi, you left me a message about the unit." | It reaches the Caller for that listing, which asks its questions | Calls that come back. First run at 15:00 |
| Second call | Renter calls again: "What happened with the first one?" | The agent answers from the stored calls: the parking, the dog and the confirmed viewing | Memory by phone number. First run at 15:00 |
| /me and the app | "Renters sign in with the phone they call from." | Phone sign-in on `/me`, then My listings with each fact's source. The same on the Expo app, which has a Call Landline button | Clerk and Expo. First run at 15:00 |
| Scam check | Agent B, on a second listing: "Send the deposit by e-transfer before you see it." | The card is flagged, and the text says "so I'd skip it" | Protects the renter. Verified in the evals |
| Another language | Renter switches to Spanish or Mandarin mid-call | It answers in that language, and the results text is translated | Multilingual. Spanish and Mandarin only |

### On stage

**Do**
- Use short sentences, and let the agent finish before the next one.
- Say the place exactly: "Cohere", "U of T", "TD Centre". For a bank or café, add the street.
- Keep the same brief as the rehearsal, so the same listing comes first.
- Keep a phone on speaker close to the mic.
- If a beat fails, say what it does in one line and move on. The backup video covers it.

**Don't**
- Don't switch streets mid-call or say "near TD Bank" alone. It will ask which one.
- Don't call from …3369 or …3462 as the renter. Those numbers reach the Caller.
- Don't speak Farsi to it. It understands, but may answer in English.
- Don't promise a text after the call-back. That's not built yet (A-13c).
- Don't deploy after 12:00, and don't restart the API before the demo.

### Numbers to quote

| Say | Number | Where it comes from |
|---|---|---|
| Real listing calls, in testing | 19 placed, 15 reached a person | docs/PITCH_NUMBERS.md, production as of Wed 28 Oct |
| Facts with the agent's exact words | 49 of 49, each found word for word in its call | docs/PITCH_NUMBERS.md |
| Fact extraction | 100% precision, 91.3% recall, 0 facts without a source | extraction eval, held-out set, after #273 |
| Cost per search | $0.11 to $0.28 for one to three listing calls | docs/PITCH_NUMBERS.md, list prices |
| Listings with real walking times | 1,090 Toronto listings, from Google Maps | production, Wed 28 Oct |
| Pickup to the Caller's first word | about 1.5 seconds | production call logs |

Who pays: renters, or employers and universities moving people to Toronto. For tough questions, see [PITCH_QA.md](PITCH_QA.md).

## The scripted demo and backup video (`?dev=1`)

The scripted demo at `/?dev=1` (`server/landline/api/demo.py`) follows this flow without dialling anyone. It's also the plan for the backup video.

### The 2:30 video

| Time | On screen and in audio | Shows |
|---|---|---|
| 0:00 | One line: Toronto listings say things that aren't true, and newcomers get it worst | The problem |
| 0:10 | A renter calls, mixing Farsi and English. The SMS link opens. Hundreds of dots fade to six numbered pins while they talk | Gemini Live or Higgs Realtime, language switching, live map |
| 0:40 | "Natural light matters most." The pins renumber before the sentence ends. They tap a pin, and the agent says "That's the corner unit on the 14th floor" | Voice and screen in sync |
| 0:55 | "That one's too dark." The card shows *You: too dark*, and darker units sink | Feedback changes ranking |
| 1:05 | "Call the top three. I'm free Saturday afternoon, book one if you can." The agent reads the three addresses back: "I'll call them as soon as we hang up and text you." The renter hangs up | The brief becomes a plan |
| 1:15 | Three pins pulse. We listen in: one landlord switches to Mandarin, one adds $180 parking, one asks for an e-transfer deposit (flagged). The Caller answers the move-in date from the brief, and for a question it can't answer says "I'll check with my client" | Parallel live calls, each on its own; scam check |
| 1:45 | Results land on the pins. Markham is booked for Saturday at 2, inside the renter's window. An SMS arrives in Farsi with the results | Agents that act on their own |
| 2:00 | The renter calls again: "Welcome back, Sara. I booked 412 Markham for Saturday at two. Dupont is really $3,470 with parking, and 88 Erskine asked for a deposit before viewing, so I'd skip it." | Results and memory by phone number |
| 2:20 | Numbers: extraction accuracy, zero ungrounded facts, time to first audio, listings and sources | How well it works |

Record the phone parts on real phones, with teammates as landlords. Use sim landlords only for the listen-in shot if needed, and say so.

### Rehearsal checklist

- [ ] `FORCE_BUSINESS_HOURS=1` on the VM, or business hours in Toronto time
- [ ] `DEMO_AGENT_PHONES` holds teammates only; the allowlist test passes
- [ ] Voice concurrency ≥ 5 confirmed (`HIGGS_MAX_SESSIONS`, from the provider spike); enough credits for rehearsals and demos
- [ ] A switch from `TRANSPORT=voice` to `sim` is one env change and one restart
- [ ] A backup recording plays offline on a laptop
- [ ] `/health` green over cellular; no tunnels
- [ ] Judge mode works from a clean browser on another network
- [ ] `gitleaks detect` clean; `.env` not in git history
- [ ] VM and Vercel stay up through the demo
