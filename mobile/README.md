# Landline mobile (Expo)

The renter's side of Landline after the call, on their phone (U-13). Sign in with the phone number you call
Landline from. Home then has a Call Landline button (the phone's own dialer) and the latest results: the
places Landline called in your last session. A number Landline doesn't know yet sees the button and how to
start, not an error. My listings has every place it called, with what it costs a month, how the call went,
the viewing it booked, what to watch out for and each fact the agent gave, with the agent's words and when
they said them, as on the web's `/me`. My search edits the search Landline keeps (budget,
bedrooms, areas, pets, must-haves, move-in date, email), what it may tell listing agents, and whether the
next call starts from it: the same form, checks and words as `/me`'s, saved only where something changed. The
move-in date is typed as YYYY-MM-DD, since a date picker would need a package. A push when the calls finish
comes next (U-13c). It's a thin client: it calls the same API as the web, directly.

Expo SDK 58 with Expo Router, and Clerk (`@clerk/expo`) for phone sign-in with an SMS code. The app uses
the same Clerk app as the web (U-11), so the API checks the same session tokens (`GET /api/me`, U-11a, and
`GET /api/me/listings`, U-12c, and `GET` and `PATCH /api/me/profile`, U-12a). Pull down on Home or My
listings to reload it.

## Run it

```bash
cd mobile
npm install
cp .env.example .env    # then paste the Clerk publishable key (pk_test_…) into it
npx expo start          # scan the QR code with Expo Go (Android) or the Camera app (iOS)
```

- `.env` holds only public values: the Clerk publishable key and, optionally, the API's address and the
  number Call Landline dials. They default to the VM's public URL and production's Landline number. Never
  put a secret key here.
- Without a key the app opens on "Landline isn’t set up yet" instead of crashing.
- Expo Go runs only the newest SDK. If Expo Go says the project is incompatible, update Expo Go.
- `npm run typecheck` checks the TypeScript. `npm test` runs the helpers' tests with Node alone (Node 23.6
  or newer strips the types), including a check that facts read as the web's `web/lib/heard.ts` writes them.
  Add packages with `npx expo install <name>`, so they match the
  SDK (see `AGENTS.md` here).

## Sign in

Clerk's development instance has phone sign-in with SMS codes turned on. A number Clerk hasn't seen gets
the same code and is signed up. Clerk's test numbers, +1 (XXX) 555-01XX with the code 424242, need no
SMS: a backup if texts don't arrive at the venue.

## Build it

Expo Go is enough for sign-in. A push (U-13c) needs a development build on the demo phone:
`npx eas-cli build --profile development --platform android` (an Expo account, and an `eas.json` that
U-13c adds), then install it from the link EAS gives.

## The demo phone

On stage (Thu 29 Oct), the app runs on a teammate's phone, signed in with the number that calls Landline
in the demo. Sign in before the demo over the venue Wi-Fi or a hotspot, and keep the app open: the session
stays across restarts (Clerk's token cache, in the phone's secure store).
