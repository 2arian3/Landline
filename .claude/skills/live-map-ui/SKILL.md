---
name: live-map-ui
description: Design and engineering rules for Landline's web app, covering the live map, listing cards, detail drawer, judge mode, real-time updates over SSE, motion, responsiveness and accessibility. Use before changing anything in web/, choosing colours, type or components, or wiring live updates.
---

# Live map UI

**The idea:** a friendly, calm map of places that someone actually checked. The renter is on the phone; the screen should confirm what they hear, never compete with it. It should feel modern and quick, and it shouldn't look like a generated dashboard.

## Stack

Next.js 15 (App Router), React 19, Tailwind 4, MapLibre GL JS with OpenFreeMap vector tiles (no API key; confirm the style URL at setup), `motion/react` for card motion (its springs are in `web/lib/motion.ts`), `@number-flow/react` for prices. No component kit that brings its own look. Ask before adding a dependency.

**The look is Lucent** (the design canvas "Landline demo polish", its `landline.css`). Its tokens and component classes live in `web/app/globals.css` (`.card`, `.pin`, `.pill`, `.legend`, `.glass` and the rest, in `@layer components`); its glyphs in `web/components/Icon.tsx` (Lucide's geometry, inline SVG). Use those names; don't invent a parallel set.

## Layout

- **Phone and tablet upright (< 1024 px):** map fills the screen under a glass capsule; the list is a bottom sheet with three snap points (peek, half, full), glass at peek and half, solid at full. The detail view is a full-height sheet.
- **Desktop (≥ 1024 px, or a phone on its side):** a full-bleed map with a glass panel floating 16 px in from the right, 440 px wide (narrower on a small screen) and **560 px from 1600 px** (a projector), where its type steps up one size. The map's fit padding, buttons, attribution and legend all keep clear of it. The detail view replaces the list inside the panel.
- The renter's focus (tap or voice) is always visible: its pin is raised and its card is highlighted.

## The map

- A muted basemap with labels kept, roads quiet, water and parks visible. The pins carry the colour.
- All catalogue listings show as small dots; the ones that stop matching fade out (opacity animation, not removal) as the renter talks.
- Shortlisted listings are numbered pins (rank). One function, `statusOf()` in `web/lib/status.ts`, gives every place its shape, badge, glyph and words, so the pin, the card's rank, the detail and the results can't disagree. Status is a shape plus a word, never colour alone (`data-s` on `.pin`):
  - ranked: ink face, no badge, no status line;
  - will call: phone badge; queued: a dotted ring;
  - ringing or on the phone: amber face with a pulsing ring (the one loop on the page, still under reduced motion);
  - checked by phone: green face, check badge;
  - viewing booked: green face, calendar badge, the card in ink "waiting for the agent"; confirmed: the check badge fills green; declined: hollow red, x badge;
  - something's off: hollow red, "!" badge; didn't get through: grey face, dashed ring; already rented: hollow grey, address struck.
- The renter's named place ("near King and Bay") is an anchor, not a pin. A legend (split layout) lists only the statuses on the map.
- The board's phase pill takes its words from `web/lib/pill.ts`.
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

## Look (Lucent)

- Colour: warm-neutral ink (`ink` #191918, `ink-secondary`) on a near-white ground (`ground` #fbfbfa, `surface`), the ink as the one accent, and three status hues (`real`, `live`, `dead`, plus `quiet` grey) that pass 4.5:1 and mean status only. Red is a problem with the place; green waits for proof. No Tailwind default palette as the brand (indigo, violet, sky).
- Type: the system's own (SF Pro on Apple devices, then Helvetica Neue and Arial), on Lucent's ramp (price-lead, price, title, headline, body, callout, caption), tabular numbers for money, no UI text under 13 px (the map's attribution and pin badges are the only smaller marks), sentence case everywhere.
- Surfaces: white cards with a hairline, 20 px corners (Lucent's `radius-md`); controls 12, the panel 28, pills round. **Glass only where something floats over the map** (the panel, the phone's capsule and sheet, the legend, the map's buttons and attribution), always with its solid fallback for Reduce Transparency and browsers without backdrop blur. Content inside it sits on solid white.
- Motion: Lucent's springs (`web/lib/motion.ts`): rank for re-ranks, lens for chips and badges, detail for a card opening. Exits are faster than entrances. Everything has a reduced-motion form.
- Copy: short, plain, specific. "Calling 3 places" not "Our AI is working its magic". No exclamation marks, no emoji in the UI.

## Banned

Gradients and glows, glass on content, coloured left stripes on cards, tinted pill badges for status, all-caps labels, invented progress ("Dialing…", ETAs) that the server didn't report, relative times the page can't back up, percent-match numbers. **One written exception:** a live call's ring on its pin may loop (it holds still under reduced motion); nothing else loops.

## Before you merge a UI change

1. Screenshots at 375, 768, 1440 and 1920 px (the projector) with every card status visible.
2. Lighthouse mobile performance ≥ 85 (waived: Mahyar's call on Fri 23 Oct at 22:35, because changing how the map starts is too risky before the demo; mobile is 46 after PR #8); map pan stays smooth on a mid-range phone.
3. Keyboard: every pin and card is reachable and focus is visible; the map has a list equivalent.
4. Read the banned list against your diff.

Merging doesn't put it on Vercel. Arian ships `release`, and Vercel deploys only a `release` tip that he authored (`deploy/README.md`, **Commit author**), so ask him to ship rather than re-running `deploy-web.yml` on a tip that isn't his.
