"use client";

import { SignIn, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { duration, phoneLabel, type CallRow, type ChosenPlace, type SessionRow, type UserDetail, when } from "@/lib/admin";
import { bedsLabel, heardLine, money } from "@/lib/format";
import { WENT, factLine } from "@/lib/heard";
import { isMeRefusal, loadMe, loadMyListings, loadSignedIn, type MeRefusal, type MyListing } from "@/lib/me";
import { LANDLINE_PHONE } from "@/lib/phone";
import { loadProfile } from "@/lib/profile";
import type { MyProfile } from "@/lib/searchForm";
import { UNHEARD, statusOf } from "@/lib/status";
import type { Card } from "@/lib/types";
import { formatWhen } from "@/lib/time";
import { Icon, type IconName, Mark } from "./Icon";
import { MySearch } from "./MySearch";

const PHONE_KEY = "landline-me-phone"; // this tab only: sessionStorage, never the URL
// Set on Vercel (X-03a): /me signs in with an SMS code. Unset (local dev, CI): the typed number, as before.
const CLERK = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

const REFUSED: Record<MeRefusal, string> = {
  phone: "That isn't a phone number. Use the number you call from, like 416 555 0123.",
  unknown: "No profile is saved for that number yet. Call Landline from it once, then come back.",
  off: "Call history isn't available on this server.",
  signin: "Your sign-in has ended. Sign in again.",
  unreachable: "Can't reach Landline right now.",
};

const PHASE: Record<string, string> = {
  open: "In the brief",
  calling: "Calling places",
  done: "Calls done",
  closed: "Ended, nothing planned",
};
const CHANNEL: Record<string, string> = { phone: "Phone call", browser: "Browser", judge: "Judge mode" };
const ACTION: Record<string, { label: string; color?: string }> = {
  booked: { label: "Viewing booked" },
  called: { label: "Called" },
  planned: { label: "Picked to call" },
  liked: { label: "Liked" },
  viewed: { label: "Didn't like" },
  rejected: { label: "Turned down", color: "var(--color-dead)" },
};
const ENDED: Record<string, string> = {
  hangup: "hung up",
  end_session: "said goodbye",
  no_answer: "no answer",
  higgs_error: "voice error",
  deadline: "hit the time limit",
  done: "finished",
};
const STILL_THERE = /^(yes|yep|yeah|true|still available|available)$/i;
const HOW: Record<string, IconName> = { talked: "message-circle", voicemail: "voicemail", no_answer: "phone" };
/** The glyph beside a fact a call heard, by what it's about (as in the place's detail). */
const FACT_ICON: Record<string, IconName> = {
  available: "circle-check",
  available_from: "calendar",
  rent: "receipt",
  fees: "receipt",
  parking: "square-parking",
  parking_cost: "square-parking",
  pets: "paw-print",
  laundry: "washing-machine",
  utilities_included: "plug",
  sqft: "scan",
};

// Solid, not glass: everything on /me is read, and there's no map behind it.
const box = "flex flex-col gap-2.5 rounded-[var(--radius-md)] bg-surface p-5 shadow-[0_0_0_1px_var(--color-hairline)]";
// The sign-in pages' dot wall (W1, W2), the ground the card sits on.
const wall =
  "bg-ground [background-image:radial-gradient(rgba(55,53,47,.16)_1px,transparent_1.4px)] [background-size:18px_18px]";
const display = "font-[family-name:var(--font-display)] text-[34px] font-bold leading-[40px] tracking-[-0.022em]";
const lead = "text-[17px] leading-[25px] text-ink-secondary";

/** /me: sign in with the phone you call from (an SMS code proves it's yours), then its calls and places. */
export function Account() {
  return CLERK ? <SignedInAccount /> : <TypedAccount />;
}

/** Signed in with Clerk (U-11b): the history of the number the session proves, from GET /api/me. */
function SignedInAccount() {
  const { isLoaded, isSignedIn, userId, getToken, signOut } = useAuth();
  const [user, setUser] = useState<UserDetail | null>(null);
  const [refusal, setRefusal] = useState<MeRefusal | null>(null);
  const [listings, setListings] = useState<MyListing[] | MeRefusal | null>(null);
  const [profile, setProfile] = useState<MyProfile | MeRefusal | null>(null);

  useEffect(() => {
    setUser(null);
    setRefusal(null);
    setListings(null);
    setProfile(null);
    if (!isLoaded || !isSignedIn) return;
    const ctl = new AbortController();
    void (async () => {
      let got: UserDetail | MeRefusal;
      let mine: MyListing[] | MeRefusal;
      let saved: MyProfile | MeRefusal;
      try {
        const token = await getToken();
        if (ctl.signal.aborted) return; // signed out or switched number meanwhile
        if (!token) {
          setRefusal("signin");
          return;
        }
        [got, mine, saved] = await Promise.all([
          loadSignedIn(token, ctl.signal),
          loadMyListings(token, ctl.signal),
          loadProfile(token, ctl.signal),
        ]);
      } catch {
        if (!ctl.signal.aborted) setRefusal("unreachable"); // Clerk couldn't give a token
        return; // or the lookup was cancelled
      }
      if (isMeRefusal(got)) setRefusal(got);
      else setUser(got);
      setListings(mine);
      setProfile(saved);
    })();
    return () => ctl.abort();
    // getToken comes from the session; a new user id is a new session
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoaded, isSignedIn, userId]);

  if (!isLoaded || (isSignedIn && !user && !refusal)) return <Loading />;
  if (!isSignedIn) return <PhoneSignIn />;
  if (refusal === "unknown") return <NoCallsYet onSignOut={() => void signOut()} />;
  if (!user) return <Refused refusal={refusal ?? "unreachable"} onSignOut={() => void signOut()} />;
  return (
    <MePage
      user={user}
      listings={listings}
      profile={profile}
      search={<MySearch saved={profile} getToken={() => getToken()} onSaved={setProfile} />}
      leave="Sign out"
      onLeave={() => void signOut()}
    />
  );
}

function Loading() {
  return (
    <div className={`fixed inset-0 grid place-items-center ${wall}`}>
      <p className="t-callout c-2" role="status">
        Loading…
      </p>
    </div>
  );
}

/** The dot wall the sign-in, the refusals and "No calls yet" sit on (W1, W2): wordmark and Map, one card. */
function Wall({ children, below }: { children: ReactNode; below?: ReactNode }) {
  return (
    <div className={`fixed inset-0 overflow-y-auto ${wall}`}>
      <header className="flex h-[76px] items-center justify-between px-4 md:px-10">
        <Link href="/" className="wordmark min-h-11">
          <Mark />
          Landline
        </Link>
        <Link href="/map" className="btn btn-quiet hover:bg-[var(--color-accent-tint)]">
          Map
        </Link>
      </header>
      <main className="mx-auto w-full max-w-[472px] px-4 pb-10 pt-6 md:pt-[74px]">
        <div className="rounded-[28px] bg-surface px-6 pb-7 pt-8 shadow-[0_0_0_1px_var(--color-hairline),0_12px_32px_rgba(15,15,15,.06)] md:px-9 md:pt-9">
          {children}
        </div>
        {below && <div className="mt-6 px-2 text-center">{below}</div>}
      </main>
    </div>
  );
}

/** Two bars: the number, then the code. */
function Steps({ step }: { step: 1 | 2 }) {
  return (
    <div className="mb-[22px] flex gap-1.5" aria-hidden>
      {[1, 2].map((n) => (
        <i
          key={n}
          className="h-1 flex-1 rounded-sm"
          style={{ background: n <= step ? "var(--color-ink)" : "var(--color-hairline)" }}
        />
      ))}
    </div>
  );
}

/** Clerk's phone sign-in on the dot wall (W1, W2): a number, then the code texted to it. Clerk's hash route
 * says which step it's on. */
function PhoneSignIn() {
  const [step, setStep] = useState<1 | 2>(1);
  useEffect(() => {
    const read = () => setStep(/factor|verify|code/.test(window.location.hash) ? 2 : 1);
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  return (
    <Wall
      below={
        <p className="t-callout c-2">
          No calls yet? Call{" "}
          <a href={LANDLINE_PHONE.href} className="tnum font-semibold text-ink underline-offset-2 hover:underline">
            {LANDLINE_PHONE.label}
          </a>{" "}
          and say what you&rsquo;re looking for. Your calls show up here.
        </p>
      }
    >
      <Steps step={step} />
      <h1 className={display}>{step === 1 ? "Your calls" : "Enter the code"}</h1>
      <p className={`${lead} mt-2.5`}>
        {step === 1
          ? "Sign in with the number you call Landline from. We text it a code, so only you see its calls."
          : "We texted a code to your number. It signs you in as soon as the last digit is in."}
      </p>
      <div className="mt-6">
        {/* Back to /me once signed in: Clerk otherwise goes to the home page. */}
        <SignIn routing="hash" withSignUp forceRedirectUrl="/me" signUpForceRedirectUrl="/me" appearance={CLERK_LOOK} />
      </div>
    </Wall>
  );
}

/**
 * Clerk's form in the kit's look (Forms): label above, a 52 px pill field, the code in 58 px boxes, a
 * full-width pill to send it, and "Secured by Clerk" small and centred under it. Ink for the one primary
 * action; the development strip off.
 */
const CLERK_LOOK = {
  // The development instance's orange strip reads at 3.0:1 and means nothing to a renter. "Secured by Clerk"
  // stays. (Clerk 7 calls this `options`; it was `layout` before.)
  options: { unsafe_disableDevelopmentModeWarnings: true },
  variables: {
    colorPrimary: "#191918",
    colorText: "#191918",
    colorTextSecondary: "#6b6a66",
    colorBackground: "#ffffff",
    colorInputBackground: "#ffffff",
    fontFamily: "inherit",
    fontSize: "17px",
    borderRadius: "12px",
  },
  // Style objects, not class names: Clerk's own CSS outranks Tailwind's layered utilities.
  elements: {
    rootBox: { width: "100%" },
    cardBox: { width: "100%", maxWidth: "100%", boxShadow: "none", border: "none", borderRadius: 0 },
    card: { width: "100%", padding: "2px", boxShadow: "none", border: "none", background: "transparent", gap: "20px" },
    // Our card says what this is; Clerk's own title would repeat it. Its number-and-edit line on the code
    // step stays.
    headerTitle: { display: "none" },
    headerSubtitle: { display: "none" },
    footer: { background: "transparent" },
    formFieldLabel: { fontSize: "13px", fontWeight: 500, color: "#6b6a66" },
    // A thumb's height on a phone, and more: the kit's fields are 52 px pills.
    formFieldInput: {
      minHeight: "52px",
      borderRadius: "999px",
      paddingLeft: "18px",
      paddingRight: "18px",
      fontSize: "17px",
    },
    phoneInputBox: { minHeight: "52px", borderRadius: "999px" },
    // The code in six boxes across the card (Forms), not Clerk's 32 px circles. Clerk holds the row at 232 px,
    // so the row and each box are let out ("&&" outranks Clerk's own rule).
    otpCodeField: { width: "100%" },
    otpCodeFieldInputs: { "&&": { width: "100%", maxWidth: "none", gap: "8px", justifyContent: "space-between" } },
    otpCodeFieldInput: {
      "&&": {
        flex: "1 1 0",
        width: "auto",
        minWidth: 0,
        maxWidth: "none",
        height: "58px",
        minHeight: "58px",
        aspectRatio: "auto",
        borderRadius: "16px",
        fontSize: "24px",
        fontWeight: 600,
      },
    },
    formButtonPrimary: {
      minHeight: "52px",
      borderRadius: "999px",
      fontSize: "17px",
      fontWeight: 600,
      letterSpacing: "-0.01em",
      textTransform: "none",
      // Flat ink, as the kit's primary button: Clerk draws its bevel on ::after.
      backgroundColor: "#191918",
      backgroundImage: "none",
      "&&::after": { display: "none" },
    },
  },
};

function Refused({ refusal, onSignOut }: { refusal: MeRefusal; onSignOut: () => void }) {
  return (
    <Wall>
      <h1 className={display}>Your calls</h1>
      <p className={`${lead} mt-2.5`}>{REFUSED[refusal]}</p>
      <button type="button" onClick={onSignOut} className="btn btn-primary btn-block mt-6">
        Sign out
      </button>
    </Wall>
  );
}

/** A number that's never called (the API's 404): nothing's wrong, there's just nothing here yet. */
function NoCallsYet({ onSignOut }: { onSignOut: () => void }) {
  return (
    <Wall>
      <h1 className={display}>No calls yet</h1>
      <p className={`${lead} mt-2.5`}>
        Call Landline from this number and say what you&rsquo;re looking for. Your calls, and what the places told
        Landline, show up here.
      </p>
      <a href={LANDLINE_PHONE.href} className="btn btn-primary btn-block mt-6">
        <Icon name="phone" size={20} />
        Call Landline
      </a>
      <p className="t-caption c-2 tnum mt-2 text-center">{LANDLINE_PHONE.label}</p>
      <button
        type="button"
        onClick={onSignOut}
        className="btn btn-quiet mx-auto mt-3 flex hover:bg-[var(--color-accent-tint)]"
      >
        Sign out
      </button>
    </Wall>
  );
}

/** Without Clerk (local dev, CI): enter the number you call from, as the API still accepts there. */
function TypedAccount() {
  const [ready, setReady] = useState(false);
  const [phone, setPhone] = useState<string | null>(null);
  const [user, setUser] = useState<UserDetail | null>(null);
  const [refusal, setRefusal] = useState<MeRefusal | null>(null);

  const open = useCallback(async (number: string, signal?: AbortSignal) => {
    let got: UserDetail | MeRefusal;
    try {
      got = await loadMe(number, signal);
    } catch {
      return; // the lookup was cancelled
    }
    if (isMeRefusal(got)) {
      setUser(null);
      setRefusal(got);
      if (got === "unknown" || got === "phone") forget(setPhone);
      return;
    }
    setRefusal(null);
    setUser(got);
    setPhone(got.phone);
    try {
      sessionStorage.setItem(PHONE_KEY, got.phone);
    } catch {
      // kept in memory for this visit
    }
  }, []);

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = sessionStorage.getItem(PHONE_KEY);
    } catch {
      // storage blocked: ask every time
    }
    setReady(true);
    if (!stored) return;
    const ctl = new AbortController();
    setPhone(stored);
    void open(stored, ctl.signal);
    return () => ctl.abort();
  }, [open]);

  if (!ready) return null;
  if (phone && !user && !refusal) return <Loading />;
  if (!user) {
    return (
      <Login
        refusal={refusal}
        onPhone={(n) => {
          setRefusal(null);
          setPhone(n);
          void open(n);
        }}
      />
    );
  }

  return <MePage user={user} leave="Different number" onLeave={() => forget(setPhone, setUser)} />;
}

function forget(setPhone: (p: string | null) => void, setUser?: (u: UserDetail | null) => void) {
  try {
    sessionStorage.removeItem(PHONE_KEY);
  } catch {
    // nothing stored
  }
  setPhone(null);
  setUser?.(null);
}

function Login({ refusal, onPhone }: { refusal: MeRefusal | null; onPhone: (phone: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <Wall>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onPhone(value.trim());
        }}
      >
        <h1 className={display}>Your calls</h1>
        <p className={`${lead} mt-2.5`}>
          Enter the number you call Landline from. This shows the profile, places and calls saved for it.
        </p>
        <div className="field mt-6">
          <label htmlFor="me-phone" className="field-label">
            Your number
          </label>
          <div className={`input focus-within:shadow-[0_0_0_2px_var(--color-ink)] ${refusal ? "is-error" : ""}`}>
            <span className="pre">+1</span>
            <input
              id="me-phone"
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              autoFocus
              placeholder="416 555 0123"
              aria-describedby="me-phone-hint"
              aria-invalid={!!refusal}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="tnum min-w-0 flex-1 bg-transparent outline-none placeholder:text-ink-secondary"
            />
          </div>
          <p id="me-phone-hint" className={`hint ${refusal ? "is-error" : ""}`}>
            {refusal ? REFUSED[refusal] : "The one you call Landline from"}
          </p>
        </div>
        <button type="submit" disabled={!value.trim()} className="btn btn-primary btn-block mt-5 disabled:opacity-45">
          Show my calls
        </button>
      </form>
    </Wall>
  );
}

type Tab = "listings" | "search" | "history";
const TAB_WORDS: Record<Tab, string> = { listings: "My listings", search: "My search", history: "History" };

// Every time on /me as the board writes it: "Tue, Oct 27 · 5:10 PM", Toronto (lib/time.ts).
const at = (iso: string | null | undefined) => formatWhen(iso) ?? when(iso ?? null);

/**
 * The renter's page (W3, W4): a top bar with the wordmark, the tabs, Map and Sign out; a greeting; then My
 * listings (with what they want, Call Landline and where invites go beside it), My search, or History.
 */
function MePage({
  user,
  listings,
  profile,
  search,
  leave,
  onLeave,
}: {
  user: UserDetail;
  listings?: MyListing[] | MeRefusal | null; // signed in only: the route needs a Clerk session
  profile?: MyProfile | MeRefusal | null; // signed in only
  search?: ReactNode; // signed in only: the "My search" form
  leave: string;
  onLeave: () => void;
}) {
  const tabs: Tab[] =
    listings !== undefined ? ["listings", ...(search ? (["search"] as Tab[]) : []), "history"] : ["history"];
  const [tab, setTab] = useState<Tab>(tabs[0]);
  const saved = profile && !isMeRefusal(profile) ? profile : null;
  const facts = [
    phoneLabel(user.phone),
    user.language ? `speaks ${user.language}` : null,
    `first call ${at(user.created_at)}`,
    `last call ${at(user.last_seen_at ?? user.created_at)}`,
  ].filter(Boolean);

  return (
    <div className="fixed inset-0 overflow-y-auto bg-ground">
      <header className="me-top sticky top-0 z-10 h-auto flex-wrap gap-y-2 px-4 py-2 backdrop-blur md:h-[72px] md:flex-nowrap md:px-10 md:py-0">
        <Link href="/" className="wordmark min-h-11">
          <Mark />
          Landline
        </Link>
        {tabs.length > 1 && (
          <nav className="order-3 w-full md:order-none md:w-auto" aria-label="Your pages">
            <div className="tabs w-full md:w-auto" role="tablist">
              {tabs.map((t) => (
                <a
                  key={t}
                  href={`#${t}`}
                  role="tab"
                  aria-selected={tab === t}
                  aria-controls="me-panel"
                  className={`justify-center ${tab === t ? "is-on" : ""}`}
                  onClick={(e) => {
                    e.preventDefault();
                    setTab(t);
                  }}
                >
                  {TAB_WORDS[t]}
                </a>
              ))}
            </div>
          </nav>
        )}
        <div className="-mr-3 flex items-center md:mr-0 md:gap-1">
          <Link href="/map" className="btn btn-quiet hover:bg-[var(--color-accent-tint)]">
            Map
          </Link>
          <button type="button" onClick={onLeave} className="btn btn-quiet hover:bg-[var(--color-accent-tint)]">
            {leave}
          </button>
        </div>
      </header>

      <main
        id="me-panel"
        role="tabpanel"
        aria-label={TAB_WORDS[tab]}
        className="mx-auto w-full max-w-[1040px] px-4 pb-12 pt-6 md:pt-10"
      >
        {tab === "search" ? (
          <>
            <h1 className={display}>My search</h1>
            <p className="t-callout c-2 mt-1">
              What Landline knows you want. Your calls fill it in; you can change it here.
            </p>
            <div className="mt-6">{search}</div>
          </>
        ) : (
          <>
            <div className="flex flex-col gap-1">
              <h1 className={display}>{user.name ? `Hi, ${user.name.split(/\s+/)[0]}` : "Your calls"}</h1>
              <p className="t-callout c-2 tnum">{facts.join(" · ")}</p>
            </div>
            {tab === "listings" ? (
              <div className="mt-7 grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_340px]">
                <section aria-labelledby="listings-heading">
                  <h2 id="listings-heading" className="sec-title">
                    My listings
                  </h2>
                  <Listings listings={listings ?? null} />
                </section>
                <aside className="flex flex-col gap-4">
                  <Wants
                    profile={saved}
                    fallback={user.preferences ? heardLine(user.preferences) : []}
                    onEdit={search ? () => setTab("search") : undefined}
                  />
                  <section className={box} aria-labelledby="call-heading">
                    <h2 id="call-heading" className="t-headline">
                      Call Landline
                    </h2>
                    <p className="t-callout c-2">
                      Say what you&rsquo;re looking for, and Landline calls the places for you.
                    </p>
                    <a href={LANDLINE_PHONE.href} className="t-title tnum inline-flex min-h-11 w-fit items-center underline-offset-4 hover:underline">
                      {LANDLINE_PHONE.label}
                    </a>
                  </section>
                  {saved && (
                    <section className={box} aria-labelledby="invites-heading">
                      <h2 id="invites-heading" className="t-headline">
                        Viewing invites
                      </h2>
                      <p className="t-callout c-2">
                        {saved.email
                          ? `Go to ${saved.email} once an agent confirms.`
                          : "Add your email in My search, and invites go there once an agent confirms."}
                      </p>
                    </section>
                  )}
                </aside>
              </div>
            ) : (
              <History user={user} />
            )}
          </>
        )}
      </main>
    </div>
  );
}

function Listings({ listings }: { listings: MyListing[] | MeRefusal | null }) {
  if (listings === null) return <p className="meta mt-1">Loading…</p>;
  if (isMeRefusal(listings)) return <p className="t-callout c-2 mt-1">Can&rsquo;t load your listings right now.</p>;
  if (!listings.length) {
    return <p className="t-callout c-2 mt-1">Landline hasn&rsquo;t called any places for you yet.</p>;
  }
  return (
    <>
      <p className="meta mt-1">
        {listings.length} {listings.length === 1 ? "call" : "calls"}, newest first, with what each agent said
      </p>
      <ul className="mt-3.5 flex flex-col gap-3.5">
        {listings.map((x) => (
          <li key={x.call_id}>
            <ListingItem listing={x} />
          </li>
        ))}
      </ul>
    </>
  );
}

/** What the renter wants, as chips (W3's aside), with Edit into My search and whether the next call uses it. */
function Wants({ profile, fallback, onEdit }: { profile: MyProfile | null; fallback: string[]; onEdit?: () => void }) {
  const chips = profile ? wantChips(profile.preferences) : fallback.map(capital);
  return (
    <section className={box} aria-labelledby="wants-heading">
      <div className="flex items-center justify-between gap-3">
        <h2 id="wants-heading" className="t-headline">
          What you want
        </h2>
        {onEdit && (
          <button
            type="button"
            onClick={onEdit}
            className="btn btn-quiet btn-small -mr-3 hover:bg-[var(--color-accent-tint)]"
          >
            Edit
          </button>
        )}
      </div>
      {chips.length ? (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <span
              key={c}
              className="inline-flex min-h-[30px] items-center rounded-full bg-[var(--color-accent-tint)] px-[11px] text-[14px] font-medium"
            >
              {c}
            </span>
          ))}
        </div>
      ) : (
        <p className="t-callout c-2">Nothing saved yet. Your calls fill it in.</p>
      )}
      {profile?.use_saved_search && (
        <p className="flex items-center gap-2 text-[15px] font-medium leading-[21px]">
          <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-ink" />
          Your next call starts from this search
        </p>
      )}
    </section>
  );
}

function wantChips(p: MyProfile["preferences"]): string[] {
  const out: string[] = [];
  if (p.max_monthly) out.push(`Up to ${money(p.max_monthly)} a month`);
  if (p.min_beds != null) out.push(p.min_beds === 0 ? "Studio or bigger" : `${p.min_beds}+ bedrooms`);
  if (p.areas?.length) out.push(p.areas.join(", "));
  for (const pet of p.pets ?? []) out.push(`With a ${pet}`);
  for (const m of p.must_have ?? []) out.push(capital(m));
  if (p.move_in) out.push(`Moving ${moveDay(p.move_in)}`);
  return out;
}

/** "2026-12-01" as "Dec 1": a calendar day, so no time zone can move it. */
function moveDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/**
 * A viewing in the board's own words (lib/status.ts `statusOf`): "Viewing Sat, Oct 31 · 2:00 PM" with "Waiting
 * for the agent to confirm" under it in ink, "Viewing confirmed · …" in green, or the agent's no in red.
 */
function viewing(x: MyListing) {
  const v = x.viewing!;
  const card = { status: "booked", red_flags: [], viewing: { ...v, status: VIEWING_STATUS[v.status] ?? "booked" } };
  const s = statusOf(card as unknown as Card);
  const icon = s.kind === "confirmed" ? "calendar-check" : s.kind === "declined" ? "calendar-x" : "calendar";
  return { tone: `is-${s.tone}`, icon: icon as IconName, words: s.words, sub: s.sub };
}
const VIEWING_STATUS: Record<string, "booked" | "confirmed" | "declined"> = {
  booked: "booked",
  confirmed: "confirmed",
  declined: "declined",
};

/**
 * One listing Landline called (Forms, W3): what it costs a month first, how the call went, the place, when it
 * was called, the viewing, anything off, and each fact with the agent's own words on tap.
 */
function ListingItem({ listing: x }: { listing: MyListing }) {
  const went = WENT[x.ended] ?? x.ended;
  const v = x.viewing ? viewing(x) : null;
  const unheard = x.ended !== "talked" && !x.facts.length ? UNHEARD[x.ended]?.line : null;
  return (
    <article className="acard">
      <div className="acard-top">
        {x.monthly_cost != null ? (
          <p className="price t-price">
            <span aria-hidden>
              {money(x.monthly_cost)} <span className="per">/mo</span>
            </span>
            <span className="sr-only">{money(x.monthly_cost)} a month</span>
          </p>
        ) : (
          <span />
        )}
        <span className="how">
          <Icon name={HOW[x.ended] ?? "phone"} size={16} />
          {went}
        </span>
      </div>
      <p className="addr">
        {x.address}
        {x.unit_label ? <span className="unit">, unit {x.unit_label}</span> : null}
      </p>
      <p className="meta tnum">Called {at(x.called_at)}</p>
      {v && (
        <p className={`status ${v.tone}`}>
          <Icon name={v.icon} size={18} />
          <span>
            {v.words}
            {v.sub && <span className="sub block">{v.sub}</span>}
          </span>
        </p>
      )}
      {unheard && <p className="t-callout c-2 mt-1.5 pb-2.5">{unheard} Nothing was heard yet.</p>}
      {x.red_flags.map((f) => (
        <p key={f} className="flag">
          <Icon name="triangle-alert" size={18} />
          <span>Watch out: {f}</span>
        </p>
      ))}
      {x.facts.length > 0 ? (
        <div className="afacts">
          {x.facts.map((f, n) => (
            <HeardFact key={`${f.field}-${n}`} fact={f} />
          ))}
        </div>
      ) : (
        !unheard && <div className="pb-2.5" />
      )}
    </article>
  );
}

/** A fact a call heard: what it says, "Agent heard", and on tap the agent's own words and when. */
function HeardFact({ fact: f }: { fact: MyListing["facts"][number] }) {
  const [open, setOpen] = useState(false);
  return (
    <button type="button" className="afact w-full text-left" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
      <Icon name={FACT_ICON[f.field] ?? "message-square"} size={18} />
      <span className="block min-w-0">
        <span className="v block">
          {f.field === "available" && STILL_THERE.test(f.value.trim()) ? "Still available" : factLine(f.field, f.value)}
          <span className="s"> &middot; Agent heard</span>
        </span>
        {open && (
          <span className="q block">
            &ldquo;{f.quote}&rdquo;
            {f.at ? <span className="t-caption c-2 tnum mt-0.5 block">{at(f.at)}</span> : null}
          </span>
        )}
      </span>
      <Icon name="chevron-down" size={18} className={`c-2 transition-transform ${open ? "rotate-180" : ""}`} />
    </button>
  );
}

/** History: the places the renter reacted to, and every call (no board for it yet, so the kit's cards). */
function History({ user }: { user: UserDetail }) {
  return (
    <div className="mt-7 flex flex-col gap-8">
      <section aria-labelledby="places-heading">
        <h2 id="places-heading" className="sec-title">
          Places
        </h2>
        {user.places.length ? (
          <ul className="mt-3.5 flex flex-col gap-3">
            {user.places.map((p) => (
              <PlaceItem key={p.listing_id} place={p} />
            ))}
          </ul>
        ) : (
          <p className="t-callout c-2 mt-1">No places liked, turned down or called yet.</p>
        )}
      </section>
      <section aria-labelledby="calls-heading">
        <h2 id="calls-heading" className="sec-title">
          Calls
        </h2>
        {user.history.length ? (
          <ol className="mt-3.5 flex flex-col gap-3">
            {user.history.map((s) => (
              <SessionItem key={s.session_id} session={s} />
            ))}
          </ol>
        ) : (
          <p className="t-callout c-2 mt-1">No calls stored yet.</p>
        )}
      </section>
    </div>
  );
}

function PlaceItem({ place }: { place: ChosenPlace }) {
  return (
    <li className="acard pb-[18px]">
      <div className="acard-top">
        <p className="addr min-w-0">
          {place.address}
          {place.unit_label ? <span className="unit">, unit {place.unit_label}</span> : null}
        </p>
        {place.monthly_cost != null && <p className="price t-price shrink-0">{money(place.monthly_cost)}</p>}
      </div>
      <p className="t-callout">
        {place.actions.map((a, n) => (
          <span key={a} style={{ color: ACTION[a]?.color ?? "var(--color-ink-secondary)" }}>
            {n > 0 && <span className="c-2"> · </span>}
            {ACTION[a]?.label ?? a}
          </span>
        ))}
        {place.reason && <span className="c-2"> ({place.reason})</span>}
      </p>
      <p className="meta">
        {[place.beds != null ? bedsLabel(place.beds) : null, at(place.last_at)].filter(Boolean).join(" · ")}
      </p>
    </li>
  );
}

function SessionItem({ session }: { session: SessionRow }) {
  const wants = session.preferences ? heardLine(session.preferences) : [];
  return (
    <li className="acard pb-[18px]">
      <p className="acard-top">
        <span className="addr">{CHANNEL[session.channel] ?? session.channel}</span>
        <span className="how">{PHASE[session.phase] ?? session.phase}</span>
      </p>
      <p className="meta tnum">
        {at(session.started_at)}
        {session.ended_at ? ` to ${at(session.ended_at)}` : ""}
      </p>
      {wants.length > 0 && <p className="t-callout c-2 mt-1.5">Asked for {wants.join(", ")}</p>}
      {session.plan.length > 0 && (
        <p className="t-callout c-2">Picked to call: {session.plan.map((p) => p.address).join(", ")}</p>
      )}
      {session.results && (
        <div className="mt-2">
          <p className="t-caption c-2">What the calls found</p>
          <p className="t-callout mt-0.5">{session.results}</p>
        </div>
      )}
      {session.calls.length > 0 && (
        <ul className="mt-2 flex flex-col gap-2">
          {session.calls.map((c) => (
            <CallLine key={c.call_id} call={c} />
          ))}
        </ul>
      )}
    </li>
  );
}

function CallLine({ call }: { call: CallRow }) {
  const who = call.role === "renter" ? "Your call with Landline" : `Call to ${call.address ?? "a listing"}`;
  const how = [
    duration(call.answered_at ?? call.created_at, call.ended_at),
    call.end_reason ? ENDED[call.end_reason] ?? call.end_reason.replaceAll("_", " ") : call.state,
    call.turns ? `${call.turns} line${call.turns === 1 ? "" : "s"}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <li className="rounded-[var(--radius-sm)] bg-surface-raised px-3 py-2">
      <p className="t-callout w6">{who}</p>
      <p className="t-caption c-2">{how}</p>
    </li>
  );
}
