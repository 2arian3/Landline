---
name: live-map-ui
description: Design and engineering rules for Landline's web app, covering the live map, listing cards, detail drawer, judge mode, real-time updates over SSE, motion, responsiveness and accessibility. Use before changing anything in web/, choosing colours, type or components, or wiring live updates.
---

# Live map UI

**The idea:** a friendly, calm map of places that someone actually checked. The renter is on the phone; the screen should confirm what they hear, never compete with it. It should feel modern and quick, and it shouldn't look like a generated dashboard.

## Stack

Next.js 15 (App Router), React 19, Tailwind 4, MapLibre GL JS with OpenFreeMap vector tiles (no API key; confirm the style URL at setup), Framer Motion for card motion, `@number-flow/react` for prices. No component kit that brings its own look. Ask before adding a dependency.

## Layout

- **Phone (≤ 768 px):** map fills the screen; the list is a bottom sheet with three snap points (peek, half, full). The detail view is a full-height sheet.
- **Desktop:** map on the left, a 420 px list on the right; the detail view slides over the list.
- The renter's focus (tap or voice) is always visible: its pin is raised and its card is highlighted.

## The map

- A muted basemap with labels kept, roads quiet, water and parks visible. The pins carry the colour.
- All catalogue listings show as small dots; the ones that stop matching fade out (opacity animation, not removal) as the renter talks.
- Shortlisted listings are numbered pins (rank). Status is shape plus a word in the card, never colour alone: pending, calling (a slow ring), verified (filled), suspect (outlined with a mark), gone (hollow, struck in the card), booked (star).
- On a new shortlist, `fitBounds` with padding for the sheet. On voice focus, `flyTo` the pin (≤ 600 ms). Respect `prefers-reduced-motion`: jump instead of fly.
- Update GeoJSON sources with `setData` only when the payload's `version` changes.

## Cards and detail

- Money is the biggest thing on a card: `monthly_cost`, with the listed rent struck through when a call changed it, and the reason ("+$180 parking, said by the agent").
- Reasons, not scores: "6 min to Spadina · bright, 14th floor · $310 under budget". Never a percentage, match bar or star rating.
- **Every fact shows its source** with a small label: *Listing says*, *City record (2025)*, *Agent heard*, plus the quote on tap. "Estimated" for the light score.
- The renter's reaction shows on the card: *You: too dark*.
- While a call is live, the detail view shows captions from `/api/calls/{id}/turns`, with each line labelled by speaker.

## Live updates

- SSE `GET /api/sessions/{id}/stream` sends `{version}`; fetch the snapshot when behind. Drop responses older than what's on screen.
- If SSE errors, poll every 1.5 s. **A successful but empty response never blanks a live board.** Keep the last good state and show a quiet "Reconnecting…" after 6 s without data.
- Reorder cards by transform (stable DOM order); a card moving up and one moving down never cross in the same layer.
- Taps send `POST /focus` or `/feedback` and update optimistically. The server is the truth on the next version.

## Look

- Colour: warm neutrals for ink and ground, **one** brand accent picked in U-00, and three status hues (verified, live, gone) that pass 4.5:1 for text. No Tailwind default palette as the brand (indigo, violet, sky).
- Type: one sans with a real personality for UI (not Inter, Geist or Space Grotesk), tabular numbers for money, nothing under 13 px, sentence case everywhere.
- Surfaces: white cards, 16 px radius, a hairline border; one soft shadow only on the floating sheet.
- Copy: short, plain, specific. "Calling 3 places" not "Our AI is working its magic". No exclamation marks, no emoji in the UI.

## Banned

Gradients and glows, glass on content, coloured left stripes on cards, tinted pill badges for status, all-caps labels, invented progress ("Dialing…", ETAs) that the server didn't report, relative times the page can't back up, percent-match numbers.

## Before you merge a UI change

1. Screenshots at 375 px and 1440 px with every card status visible.
2. Lighthouse mobile performance ≥ 85 (waived: Mahyar's call on Fri 23 Oct at 22:35, because changing how the map starts is too risky before the demo; mobile is 46 after PR #8); map pan stays smooth on a mid-range phone.
3. Keyboard: every pin and card is reachable and focus is visible; the map has a list equivalent.
4. Read the banned list against your diff.

Merging doesn't put it on Vercel. Arian ships `release`, and Vercel deploys only a `release` tip that he authored (`deploy/README.md`, **Commit author**), so ask him to ship rather than re-running `deploy-web.yml` on a tip that isn't his.
