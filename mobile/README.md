# Landline mobile (Expo)

The renter's side of Landline after the call, on their phone (U-13). Sign in with the phone number you call
Landline from; the app then shows who Landline knows you as. My listings, a push when the calls finish and
My search come next (U-13b–d). It's a thin client: it calls the same API as the web, directly.

Expo SDK 58 with Expo Router, and Clerk (`@clerk/expo`) for phone sign-in with an SMS code. The app uses
the same Clerk app as the web (U-11), so the API checks the same session tokens (`GET /api/me`, U-11a).

## Run it

```bash
cd mobile
npm install
cp .env.example .env    # then paste the Clerk publishable key (pk_test_…) into it
npx expo start          # scan the QR code with Expo Go (Android) or the Camera app (iOS)
```

- `.env` holds only public values: the Clerk publishable key and, optionally, the API's address. The API
  defaults to the VM's public URL. Never put a secret key here.
- Without a key the app opens on "Landline isn’t set up yet" instead of crashing.
- Expo Go runs only the newest SDK. If Expo Go says the project is incompatible, update Expo Go.
- `npm run typecheck` checks the TypeScript. Add packages with `npx expo install <name>`, so they match the
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
