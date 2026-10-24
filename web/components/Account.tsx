"use client";

import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { duration, phoneLabel, type CallRow, type ChosenPlace, type SessionRow, type UserDetail, when } from "@/lib/admin";
import { bedsLabel, heardLine, money } from "@/lib/format";
import { isMeRefusal, loadMe, type MeRefusal } from "@/lib/me";

const PHONE_KEY = "landline-me-phone"; // this tab only: sessionStorage, never the URL

const REFUSED: Record<MeRefusal, string> = {
  phone: "That isn't a phone number. Use the number you call from, like 416 555 0123.",
  unknown: "No profile is saved for that number yet. Call Landline from it once, then come back.",
  off: "Call history isn't available on this server.",
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

const card = "rounded-[var(--radius-card)] bg-surface";
const hairline = { boxShadow: "0 0 0 1px var(--color-hair)" };

/** /me: enter the number you call from, then the calls and places saved for it. */
export function Account() {
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
  if (phone && !user && !refusal) {
    return (
      <div className="fixed inset-0 grid place-items-center bg-ground">
        <p className="text-support text-ink-3">Loading…</p>
      </div>
    );
  }
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

  return <History user={user} onLeave={() => forget(setPhone, setUser)} />;
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
        <p className="text-fact font-strong tracking-[-0.01em]">
          <Link href="/" className="focus-visible:outline-2 focus-visible:outline-ink">
            Landline
          </Link>
        </p>
        <h1 className="mt-3 text-title font-strong">Your calls</h1>
        <p className="mt-1 text-support text-ink-2">
          Enter the number you call Landline from. This shows the profile, places and calls saved for it.
        </p>
        <label htmlFor="me-phone" className="mt-4 block text-meta font-label text-ink-3">
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
          className="mt-1.5 block min-h-11 w-full rounded-[var(--radius-control)] bg-ground px-3 text-support text-ink outline-none placeholder:text-ink-3 focus-visible:outline-2 focus-visible:outline-ink"
          style={hairline}
        />
        {refusal && <p className="mt-2 text-support text-dead">{REFUSED[refusal]}</p>}
        <button
          type="submit"
          disabled={!value.trim()}
          className="mt-4 min-h-11 w-full rounded-[var(--radius-control)] bg-ink px-4 text-support font-label text-white disabled:opacity-40"
        >
          Show my calls
        </button>
      </form>
    </div>
  );
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function History({ user, onLeave }: { user: UserDetail; onLeave: () => void }) {
  const wants = user.preferences ? heardLine(user.preferences) : [];
  return (
    <div className="fixed inset-0 overflow-y-auto bg-ground">
      <article
        className="mx-auto flex max-w-3xl flex-col gap-7 px-4 pb-10"
        style={{ paddingTop: "max(12px, env(safe-area-inset-top))" }}
      >
        <div className="flex items-center justify-between gap-3">
          <Link
            href="/"
            className="-ml-2 inline-flex min-h-11 items-center rounded-[var(--radius-control)] px-2 text-support text-ink-2 hover:bg-press focus-visible:outline-2 focus-visible:outline-ink"
          >
            ← Map
          </Link>
          <button
            type="button"
            onClick={onLeave}
            className="inline-flex min-h-11 items-center rounded-[var(--radius-control)] px-2 text-support text-ink-2 hover:bg-press focus-visible:outline-2 focus-visible:outline-ink"
          >
            Different number
          </button>
        </div>

        <header>
          <h1 className="tnum text-title font-strong">{phoneLabel(user.phone)}</h1>
          <p className="mt-0.5 text-support text-ink-2">
            {[user.name, user.language && `speaks ${user.language}`].filter(Boolean).join(" · ") || "No name saved"}
          </p>
          <p className="mt-1 text-meta text-ink-3">
            First call {when(user.created_at)} · last call {when(user.last_seen_at ?? user.created_at)}
          </p>
        </header>

        <Section title="What you want">
          {wants.length ? (
            <p className="text-fact">{capital(wants.join(" · "))}</p>
          ) : (
            <p className="text-support text-ink-2">Nothing saved yet.</p>
          )}
          {user.summary && <p className="mt-2 text-support text-ink-2">{user.summary}</p>}
        </Section>

        <Section title="Places">
          {user.places.length ? (
            <ul className="space-y-3">
              {user.places.map((p) => (
                <PlaceItem key={p.listing_id} place={p} />
              ))}
            </ul>
          ) : (
            <p className="text-support text-ink-2">No places liked, turned down or called yet.</p>
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
            <p className="text-support text-ink-2">No calls stored yet.</p>
          )}
        </Section>
      </article>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="text-meta font-label text-ink-3">{title}</h2>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function PlaceItem({ place }: { place: ChosenPlace }) {
  return (
    <li className={`${card} p-4`} style={hairline}>
      <div className="flex items-baseline justify-between gap-3">
        <p className="min-w-0 text-fact font-strong">
          {place.address}
          {place.unit_label ? <span className="font-normal text-ink-2">, unit {place.unit_label}</span> : null}
        </p>
        {place.monthly_cost != null && <p className="tnum shrink-0 text-fact">{money(place.monthly_cost)}</p>}
      </div>
      <p className="mt-1 text-support">
        {place.actions.map((a, n) => (
          <span key={a} style={{ color: ACTION[a]?.color ?? "var(--color-ink-2)" }}>
            {n > 0 && <span className="text-ink-3"> · </span>}
            {ACTION[a]?.label ?? a}
          </span>
        ))}
        {place.reason && <span className="text-ink-2"> ({place.reason})</span>}
      </p>
      <p className="mt-0.5 text-meta text-ink-3">
        {[place.beds != null ? bedsLabel(place.beds) : null, when(place.last_at)].filter(Boolean).join(" · ")}
      </p>
    </li>
  );
}

function SessionItem({ session }: { session: SessionRow }) {
  const wants = session.preferences ? heardLine(session.preferences) : [];
  return (
    <li className={`${card} p-4`} style={hairline}>
      <p className="flex items-baseline justify-between gap-3">
        <span className="text-fact font-strong">{CHANNEL[session.channel] ?? session.channel}</span>
        <span className="shrink-0 text-meta text-ink-3">{PHASE[session.phase] ?? session.phase}</span>
      </p>
      <p className="text-meta text-ink-3">
        {when(session.started_at)}
        {session.ended_at ? ` to ${when(session.ended_at)}` : ""}
      </p>
      {wants.length > 0 && <p className="mt-2 text-support text-ink-2">Asked for {wants.join(", ")}</p>}
      {session.plan.length > 0 && (
        <p className="mt-1 text-support text-ink-2">Picked to call: {session.plan.map((p) => p.address).join(", ")}</p>
      )}
      {session.results && (
        <div className="mt-3">
          <p className="text-meta font-label text-ink-3">What the calls found</p>
          <p className="mt-1 text-support text-ink">{session.results}</p>
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
    <li className="rounded-[var(--radius-control)] bg-ground px-3 py-2">
      <p className="text-support font-label text-ink">{who}</p>
      <p className="text-meta text-ink-3">{how}</p>
    </li>
  );
}
