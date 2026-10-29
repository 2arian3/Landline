"use client";

import { SignIn, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { duration, phoneLabel, type CallRow, type ChosenPlace, type SessionRow, type UserDetail, when } from "@/lib/admin";
import { formatWhen, viewingWhen } from "@/lib/time";
import { bedsLabel, heardLine, money } from "@/lib/format";
import { WENT, factLine } from "@/lib/heard";
import { isMeRefusal, loadMe, loadMyListings, loadSignedIn, type MeRefusal, type MyListing } from "@/lib/me";
import { Icon, Mark } from "./Icon";
import { Money } from "./Money";
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
  booked: { label: "Viewing booked", color: "var(--color-real)" },
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

// Solid, not glass: everything on /me is read, and there's no map behind it.
const card = "rounded-[var(--radius-md)] bg-surface";
const hairline = { boxShadow: "0 0 0 1px var(--color-hairline)" };

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

  useEffect(() => {
    setUser(null);
    setRefusal(null);
    setListings(null);
    if (!isLoaded || !isSignedIn) return;
    const ctl = new AbortController();
    void (async () => {
      let got: UserDetail | MeRefusal;
      let mine: MyListing[] | MeRefusal;
      try {
        const token = await getToken();
        if (ctl.signal.aborted) return; // signed out or switched number meanwhile
        if (!token) {
          setRefusal("signin");
          return;
        }
        [got, mine] = await Promise.all([loadSignedIn(token, ctl.signal), loadMyListings(token, ctl.signal)]);
      } catch {
        if (!ctl.signal.aborted) setRefusal("unreachable"); // Clerk couldn't give a token
        return; // or the lookup was cancelled
      }
      if (isMeRefusal(got)) setRefusal(got);
      else setUser(got);
      setListings(mine);
    })();
    return () => ctl.abort();
    // getToken comes from the session; a new user id is a new session
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoaded, isSignedIn, userId]);

  if (!isLoaded || (isSignedIn && !user && !refusal)) return <Loading />;
  if (!isSignedIn) return <PhoneSignIn />;
  if (!user) return <Refused refusal={refusal ?? "unreachable"} onSignOut={() => void signOut()} />;
  return (
    <History
      user={user}
      listings={listings}
      search={<MySearch getToken={() => getToken()} />}
      leave="Sign out"
      onLeave={() => void signOut()}
    />
  );
}

function Loading() {
  return (
    <div className="fixed inset-0 grid place-items-center bg-ground">
      <p className="t-callout c-2">Loading…</p>
    </div>
  );
}

/** Clerk's phone sign-in, in the page's own card: a number, then the code texted to it. */
function PhoneSignIn() {
  return (
    <div className="fixed inset-0 grid place-items-center overflow-y-auto bg-ground px-4 py-6">
      <div className={`${card} w-full max-w-sm p-5`} style={hairline}>
        <Link href="/" className="wordmark w-fit">
          <Mark />
          Landline
        </Link>
        <h1 className="t-title mt-5">Your calls</h1>
        <p className="t-callout c-2 mt-1">
          Sign in with the number you call Landline from. We text it a code, so only you see its calls.
        </p>
        <div className="mt-4">
          {/* Back to /me once signed in: Clerk otherwise goes to the home page. */}
          <SignIn routing="hash" withSignUp forceRedirectUrl="/me" signUpForceRedirectUrl="/me" appearance={CLERK_LOOK} />
        </div>
      </div>
    </div>
  );
}

/** Clerk's form in Lucent's look: no card of its own, ink as the accent, the system type, Lucent's fields
 * (12 px corners) and pill buttons, every one 44 px tall. */
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
    fontSize: "15px",
    borderRadius: "12px",
  },
  // Style objects, not class names: Clerk's own CSS outranks Tailwind's layered utilities.
  elements: {
    rootBox: { width: "100%" },
    cardBox: { width: "100%", maxWidth: "100%", boxShadow: "none", border: "none", borderRadius: 0 },
    card: { width: "100%", padding: "2px", boxShadow: "none", border: "none", background: "transparent" },
    // Our card says what this is; Clerk's own title would repeat it. Its number-and-edit line on the code
    // step stays.
    headerTitle: { display: "none" },
    headerSubtitle: { display: "none" },
    footer: { background: "transparent" },
    // A thumb's height on a phone: Clerk's own are 35 px.
    formFieldInput: { minHeight: "44px" },
    formButtonPrimary: {
      minHeight: "44px",
      borderRadius: "999px",
      fontSize: "17px",
      fontWeight: 600,
      letterSpacing: "-0.01em",
      textTransform: "none",
    },
  },
};

function Refused({ refusal, onSignOut }: { refusal: MeRefusal; onSignOut: () => void }) {
  return (
    <div className="fixed inset-0 grid place-items-center bg-ground px-4">
      <div className={`${card} w-full max-w-sm p-5`} style={hairline}>
        <h1 className="t-title">Your calls</h1>
        <p className="t-callout c-2 mt-2">{REFUSED[refusal]}</p>
        <button type="button" onClick={onSignOut} className="btn btn-primary mt-4 w-full">
          Sign out
        </button>
      </div>
    </div>
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

  return <History user={user} leave="Different number" onLeave={() => forget(setPhone, setUser)} />;
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
    <div className="fixed inset-0 grid place-items-center overflow-y-auto bg-ground px-4">
      <form
        className={`${card} w-full max-w-sm p-5`}
        style={hairline}
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onPhone(value.trim());
        }}
      >
        <Link href="/" className="wordmark w-fit">
          <Mark />
          Landline
        </Link>
        <h1 className="t-title mt-5">Your calls</h1>
        <p className="t-callout c-2 mt-1">
          Enter the number you call Landline from. This shows the profile, places and calls saved for it.
        </p>
        <label htmlFor="me-phone" className="t-caption c-2 mt-4 block">
          Your number
        </label>
        <input
          id="me-phone"
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          autoFocus
          placeholder="416 555 0123"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="t-callout mt-1.5 block min-h-11 w-full rounded-[var(--radius-sm)] bg-surface px-3 text-ink placeholder:text-ink-secondary"
          style={hairline}
        />
        {refusal && <p className="t-callout mt-2 text-dead">{REFUSED[refusal]}</p>}
        <button type="submit" disabled={!value.trim()} className="btn btn-primary mt-4 w-full disabled:opacity-45">
          Show my calls
        </button>
      </form>
    </div>
  );
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
// Every time on /me as the board writes it: "Tue, Oct 27 · 5:10 PM", Toronto (lib/time.ts).
const at = (iso: string | null | undefined) => formatWhen(iso) ?? when(iso ?? null);

/** A viewing in the board's words (lib/status.ts): who it waits on, or how the agent answered. */
function viewingWords(v: { starts_at: string; slot_text: string; status: string }): string {
  const t = viewingWhen(v);
  if (v.status === "confirmed") return `Viewing confirmed \u00b7 ${t}`;
  if (v.status === "declined") return `The agent can\u2019t do ${t}`;
  return `Viewing ${t} \u00b7 waiting for the agent to confirm`;
}

function History({
  user,
  listings,
  search,
  leave,
  onLeave,
}: {
  user: UserDetail;
  listings?: MyListing[] | MeRefusal | null; // signed in only: the route needs a Clerk session
  search?: ReactNode; // signed in only: the "My search" form takes the read-only "What you want"
  leave: string;
  onLeave: () => void;
}) {
  const wants = user.preferences ? heardLine(user.preferences) : [];
  return (
    <div className="fixed inset-0 overflow-y-auto bg-ground">
      <article
        className="mx-auto flex max-w-3xl flex-col gap-7 px-4 pb-10"
        style={{ paddingTop: "max(12px, env(safe-area-inset-top))" }}
      >
        <div className="flex items-center justify-between gap-3">
          <Link href="/map" className="back -ml-2 hover:bg-[var(--color-accent-tint)]">
            <Icon name="chevron-left" size={20} />
            Map
          </Link>
          <button type="button" onClick={onLeave} className="btn btn-quiet -mr-3 hover:bg-[var(--color-accent-tint)]">
            {leave}
          </button>
        </div>

        <header>
          <h1 className="t-title tnum">{phoneLabel(user.phone)}</h1>
          <p className="t-callout c-2 mt-0.5">
            {[user.name, user.language && `speaks ${user.language}`].filter(Boolean).join(" · ") || "No name saved"}
          </p>
          <p className="t-caption c-2 mt-1">
            First call {at(user.created_at)} · last call {at(user.last_seen_at ?? user.created_at)}
          </p>
        </header>

        {search ? (
          <Section title="My search">
            <div className={`${card} p-4`} style={hairline}>
              {search}
            </div>
          </Section>
        ) : (
          <Section title="What you want">
            {wants.length ? (
              <p className="t-body">{capital(wants.join(" · "))}</p>
            ) : (
              <p className="t-callout c-2">Nothing saved yet.</p>
            )}
            {user.summary && <p className="t-callout c-2 mt-2">{user.summary}</p>}
          </Section>
        )}

        {listings !== undefined && (
          <Section title="My listings">
            {listings === null ? (
              <p className="t-callout c-2">Loading…</p>
            ) : isMeRefusal(listings) ? (
              <p className="t-callout c-2">Can&rsquo;t load your listings right now.</p>
            ) : listings.length ? (
              <ul className="space-y-3">
                {listings.map((x) => (
                  <ListingItem key={x.call_id} listing={x} />
                ))}
              </ul>
            ) : (
              <p className="t-callout c-2">Landline hasn&rsquo;t called any places for you yet.</p>
            )}
          </Section>
        )}

        <Section title="Places">
          {user.places.length ? (
            <ul className="space-y-3">
              {user.places.map((p) => (
                <PlaceItem key={p.listing_id} place={p} />
              ))}
            </ul>
          ) : (
            <p className="t-callout c-2">No places liked, turned down or called yet.</p>
          )}
        </Section>

        <Section title="Calls">
          {user.history.length ? (
            <ol className="space-y-4">
              {user.history.map((s) => (
                <SessionItem key={s.session_id} session={s} />
              ))}
            </ol>
          ) : (
            <p className="t-callout c-2">No calls stored yet.</p>
          )}
        </Section>
      </article>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="t-headline">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

/**
 * One listing Landline called: what it costs a month, first and biggest as on the board's cards, then how the
 * call went, the viewing, red flags, and each fact with its source.
 */
function ListingItem({ listing: x }: { listing: MyListing }) {
  const went = WENT[x.ended] ?? x.ended;
  const unit = x.unit_label ? <span className="font-normal c-2">, unit {x.unit_label}</span> : null;
  return (
    <li className={`${card} p-4`} style={hairline}>
      {x.monthly_cost != null ? (
        <>
          <div className="flex items-start justify-between gap-3">
            <Money rent={x.monthly_cost} monthly={x.monthly_cost} />
            <p className="t-caption c-2 mt-1 shrink-0">{went}</p>
          </div>
          <p className="t-headline mt-1 min-w-0">
            {x.address}
            {unit}
          </p>
        </>
      ) : (
        <div className="flex items-baseline justify-between gap-3">
          <p className="t-headline min-w-0">
            {x.address}
            {unit}
          </p>
          <p className="t-caption c-2 shrink-0">{went}</p>
        </div>
      )}
      <p className="t-caption c-2">Called {at(x.called_at)}</p>
      {x.viewing && (
        <p className={`status mt-2 ${x.viewing.status === "declined" ? "is-dead" : x.viewing.status === "confirmed" ? "is-real" : "is-ink"}`}>
          <Icon
            name={x.viewing.status === "declined" ? "calendar-x" : x.viewing.status === "confirmed" ? "calendar-check" : "calendar"}
            size={18}
          />
          <span>{viewingWords(x.viewing)}</span>
        </p>
      )}
      {x.red_flags.map((f) => (
        <p key={f} className="status is-dead mt-2">
          <Icon name="triangle-alert" size={18} />
          <span>Watch out: {f}</span>
        </p>
      ))}
      {x.facts.length > 0 && (
        <ul className="mt-2 space-y-1">
          {x.facts.map((f, n) => (
            <li key={`${f.field}-${n}`}>
              <details>
                <summary className="t-callout cursor-pointer marker:text-ink-secondary">
                  {factLine(f.field, f.value)} <span className="t-caption c-2 whitespace-nowrap">· Agent heard</span>
                </summary>
                <p className="t-caption c-2 mt-0.5 pl-4">
                  &ldquo;{f.quote}&rdquo;{f.at ? ` · ${at(f.at)}` : ""}
                </p>
              </details>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function PlaceItem({ place }: { place: ChosenPlace }) {
  return (
    <li className={`${card} p-4`} style={hairline}>
      <div className="flex items-baseline justify-between gap-3">
        <p className="t-headline min-w-0">
          {place.address}
          {place.unit_label ? <span className="font-normal c-2">, unit {place.unit_label}</span> : null}
        </p>
        {place.monthly_cost != null && <p className="t-headline tnum shrink-0">{money(place.monthly_cost)}</p>}
      </div>
      <p className="t-callout mt-1">
        {place.actions.map((a, n) => (
          <span key={a} style={{ color: ACTION[a]?.color ?? "var(--color-ink-secondary)" }}>
            {n > 0 && <span className="c-2"> · </span>}
            {ACTION[a]?.label ?? a}
          </span>
        ))}
        {place.reason && <span className="c-2"> ({place.reason})</span>}
      </p>
      <p className="t-caption c-2 mt-0.5">
        {[place.beds != null ? bedsLabel(place.beds) : null, at(place.last_at)].filter(Boolean).join(" · ")}
      </p>
    </li>
  );
}

function SessionItem({ session }: { session: SessionRow }) {
  const wants = session.preferences ? heardLine(session.preferences) : [];
  return (
    <li className={`${card} p-4`} style={hairline}>
      <p className="flex items-baseline justify-between gap-3">
        <span className="t-headline">{CHANNEL[session.channel] ?? session.channel}</span>
        <span className="t-caption c-2 shrink-0">{PHASE[session.phase] ?? session.phase}</span>
      </p>
      <p className="t-caption c-2">
        {at(session.started_at)}
        {session.ended_at ? ` to ${at(session.ended_at)}` : ""}
      </p>
      {wants.length > 0 && <p className="t-callout c-2 mt-2">Asked for {wants.join(", ")}</p>}
      {session.plan.length > 0 && (
        <p className="t-callout c-2 mt-1">Picked to call: {session.plan.map((p) => p.address).join(", ")}</p>
      )}
      {session.results && (
        <div className="mt-3">
          <p className="t-caption c-2">What the calls found</p>
          <p className="t-callout mt-1">{session.results}</p>
        </div>
      )}
      {session.calls.length > 0 && (
        <ul className="mt-3 space-y-2">
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
