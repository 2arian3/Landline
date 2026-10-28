# The pitch (X-03h): a draft for Arian to edit

A spoken script for Build North's Demo Day (Thu 29 Oct, live demos 18:00–19:30), built around #248's run of show. Every number comes from [PITCH_NUMBERS.md](PITCH_NUMBERS.md) and is from the team's own testing, so the script says "in testing".

**Two versions:**
- **Full:** about 5:30. It needs features that aren't live yet; see the table below.
- **Live today:** about 4:20. It uses only what works now. Use it unless the missing pieces are merged and rehearsed by Thu 12:00.

The run of show alone is about 4 minutes, because a live listing call takes about a minute once answered. So "3 minutes around the demo" doesn't fit with the demo inside it. **Confirm the slot length with Build North.** If it's 3 minutes, use the live-today version and cut the steps marked ✂.

**Roles:** the **Presenter** (Arian) speaks to the room. The **Renter** is a teammate (or a judge) calling Landline on speaker. **Agent A** and **Agent B** hold the two allowlisted phones that play listing agents (X-03e decides who). All three phones are mirrored on screen (X-03g). If a live step fails, say the fallback line and play that step from the backup video (X-03g), recorded at the Wednesday rehearsal.

## What's live (Wed 28 Oct)

| Step | Needs | Live? |
|---|---|---|
| A phone call to Landline, the brief, the live map re-sorting by budget, beds, pets and distance to a named place | Higgs Realtime, Twilio | yes |
| The board link texted when the call connects | Twilio SMS | yes |
| Listing calls to the team's phones, the facts with quotes, a booking inside the renter's times, the results text | Twilio, Higgs; 09:00–20:00 Toronto time | yes (19 placed in testing) |
| The booked viewing on the card | O-08e (#268) | yes, as "booked" |
| Sign-in on /me and "My listings" (Clerk) | U-11b, U-12c; Vercel's Clerk key (X-03a) | built; needs the key on Vercel |
| The Expo app (Clerk sign-in, Home, My listings) | U-13a/b; the app's Clerk key | built; needs the key |
| "I work at King and Bay" re-sorts by **TTC minutes** | D-10a/c; `GOOGLE_MAPS_API_KEY` | built; key not set |
| Walking minutes to stores on the listing | D-10b/d; the Maps key and a pipeline run | built; key not set |
| "Is there a Shoppers near the first one?" | O-07a/b | not built |
| The Caller confirms what an earlier call heard | O-09a (#270) + O-09b; the SurrealDB keys | O-09a built, O-09b not built |
| An agent's confirm link, both calendar invites, the card flipping to "confirmed" | O-08a (#262), O-08b/c/d, A-13a; the Google OAuth keys | O-08b, c, d and A-13a not built |
| A voicemail, then the agent calls back and reaches the Caller | A-13a–c | not built |
| A push in the app when the calls finish | U-13c | not built |
| "What did they say about parking at the first one?" on a second call | A-14 | not built |

## Full version (about 5:30)

| Time | Who | Says / does | Cue | If it fails |
|---|---|---|---|---|
| 0:00–0:20 | Presenter | "Finding a place in Toronto means calling listing agents. About one rental condo in a hundred is empty here, so the good ones go fast. And every listing leaves out what you need: is it still available, what does parking really cost, can you bring your dog. You find out one call at a time, if they pick up." | | |
| 0:20–0:40 | Presenter | "Landline is a voice agent that works for the renter, not the brokerage. You call it and say what you want. A map re-sorts while you talk. You pick the places and hang up, and Landline phones those agents for you, at the same time. It asks what the listing doesn't say, books a viewing inside your times, and texts you what it heard, in the agent's own words. Let's call it." | | |
| 0:40–1:45 | Renter | Calls the Landline number on speaker. "Two bedrooms under $2,600. I work at King and Bay, and I have a dog." | [phone rings; Landline greets: AI, recorded] [board link text arrives] [board re-sorts: "min by TTC to King and Bay" reasons] | "The venue line is fighting us. Here's the same call from this afternoon." Video 1 |
| ✂ 1:45–2:05 | Renter | "Is there a Shoppers near the first one?" | [map flies to the store] (O-07) | Skip it, and say "it can answer area questions too" |
| 2:05–2:25 | Renter | "Call the top two. I'm free Saturday from one to five, and you can book." | [Landline reads back the two addresses and the times] [renter hangs up] | Video 1, end |
| 2:25–3:35 | Agent A, Agent B | Both phones ring on stage. **A** answers: Landline says it's an AI assistant calling for a renter. It confirms the $150 parking fee an earlier call heard (O-09b), asks about the dog, and books Saturday at 2. **B** lets it go to voicemail. | [two phones ring] [A's answers appear on the card with their quotes] [B: voicemail] | "Listing calls only run 9 to 8 Toronto time, and this one didn't connect. Here's the pair from this afternoon." Video 2 |
| ✂ 3:35–3:55 | Agent B | Calls back the number that left the voicemail; the Caller for that listing picks up (A-13). | [B's card updates] | Skip it |
| 3:55–4:15 | Agent A, Renter | A taps the confirm link in the text. The card flips to confirmed, and both calendar invites arrive (O-08). The renter's phone shows the results text and the push in the app (U-13c), with every fact showing who said it. | [card: confirmed] [invites] [results text on screen] | "The invite is the one piece still waking up. The booking is real; here's the text." Show the results text |
| ✂ 4:15–4:35 | Renter | Calls back: "What did they say about parking at the first one?" (A-14) | [Landline answers from the call's facts] | Skip it |
| 4:35–5:00 | Presenter | "In testing this week, on the team's own phones and no real renters yet, Landline placed 19 listing calls, and 15 reached a person. It put 49 facts on the cards. Every one carries the agent's exact words, and every quote checks out word for word against the call's transcript. On our held-out test calls, its fact checker showed nothing nobody said." | [slide: the numbers] | |
| 5:00–5:15 | Presenter | "A search costs 11 to 19 cents to run, and most of that is the phone call, not the AI. Renters can pay per search, or the employers and universities moving people to Toronto can pay for theirs." | | |
| 5:15–5:30 | Presenter | "We're Arian, Danial and Mahyar. Arian built the data and ranking, Danial the voice agents and calls, Mahyar the map and the app. Landline. It makes the calls for you." | | |

## Live-today version (about 4:20)

Same opener and close; the demo keeps only what works now.

| Time | Who | Says / does | Cue | If it fails |
|---|---|---|---|---|
| 0:00–0:20 | Presenter | The opener, as above. | | |
| 0:20–0:35 | Presenter | "Landline is a voice agent that works for the renter. You call it, a map re-sorts while you talk, and when you hang up it phones the listing agents for you, at the same time, books a viewing, and texts you what they said, in their own words. Let's call it." | | |
| 0:35–1:50 | Renter | Calls on speaker. "Two bedrooms under $2,600, near King and Bay. I have a dog." | [Landline greets: AI, recorded] [board link text] [board re-sorts: budget, bedrooms, "km from King and Bay"] | "Here's the same call from this afternoon." Video 1 |
| 1:50–2:10 | Renter | "Call the top two. I'm free Saturday from one to five, and you can book." Hangs up after the read-back. | [read-back of the two addresses and times] | Video 1, end |
| 2:10–3:20 | Agent A, Agent B | Both phones ring. **A** answers: "I'm an AI assistant calling for a renter…". A says it's available, parking is $150 a month, dogs are fine, Saturday at 2 works. Landline reads the numbers back and books it. **B** lets it ring to voicemail; Landline leaves a short message. | [two phones ring] [A's facts appear on the card with quotes] [B's card: no answer] | "Calls run 9 to 8 Toronto time; here's the pair from this afternoon." Video 2 |
| 3:20–3:35 | Renter | Holds up the phone: the results text. "Booked Saturday at 2 pm… parking is $150 a month…" | [results text on screen] [card A: booked] | Show the text from this afternoon's run |
| ✂ 3:35–3:50 | Presenter | Only if Vercel's Clerk key is on: signs in on /me with the renter's phone and opens "My listings". "Every fact shows who said it." | [/me, My listings] | Skip it |
| 3:50–4:05 | Presenter | The numbers, as above. | [slide] | |
| 4:05–4:20 | Presenter | Who pays, and the team, as above. | | |

## Prize lines (only where true on the day)

- **Clerk** (U-11, U-13a): "Renters sign in with the phone number they call from, by SMS code, on the web and in the app." Say it only if the Clerk keys are on Vercel and in the app.
- **Expo** (U-13a/b): "The app shows your listings and what each call heard, and calls Landline in one tap." Leave out the push, which isn't built (U-13c).
- **SurrealDB Agent Memory** (O-09): only if O-09b is built and the keys are set. "Landline remembers what agents said across renters, so the next call confirms instead of asking cold." O-09a alone only stores the facts, so don't claim the confirm without O-09b.
- **Eon** (E-06): not built. Leave it out.

## Sources

- "About one rental condo in a hundred is empty": CMHC, [Inside Canada's 2025 rental market](https://www.cmhc-schl.gc.ca/observer/2025/inside-canada-2025-rental-market-what-changed) (Dec 11, 2025): the GTA's "condominium apartment vacancy rate remains low at 1%", and purpose-built vacancy "climbed to 3%". For condos, say "about one in a hundred", not "only 1%"; the purpose-built rate is higher.
- Rental scams: no sourced number used. The Canadian Anti-Fraud Centre's [Top 10 frauds in 2025](https://antifraudcentre-centreantifraude.ca/features-vedette/2026/02/top-fraud-2025-fraudes-plus-courantes-eng.htm) has no rental category, and the 2023 rental figure seen in the press (715 reports, $820,000 lost) couldn't be checked against the CAFC directly. Don't cite a scam stat on stage unless someone verifies one.
- Everything else: [PITCH_NUMBERS.md](PITCH_NUMBERS.md), all of it in testing.
  - 19 listing calls placed, 15 reaching a person.
  - 49 facts with quotes, all found in the transcripts.
  - $0.11–$0.19 a search; the phone and SMS are about $0.09–$0.16 of that, Higgs about $0.02–$0.03.
  - The held-out fact checker: 0 of 17 facts without a quote, Oct 28, `ca81888`.
- The agent and voice evals (Concierge, Caller, voice) are from Oct 24 and stale against today's code (PITCH_NUMBERS.md). The script doesn't quote them; quote them only with their date.
