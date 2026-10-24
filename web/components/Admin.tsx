"use client";

import { useCallback, useEffect, useState } from "react";
import {
  adminGet,
  type CallDetail,
  type CallRow,
  type ChosenPlace,
  duration,
  isRefusal,
  phoneLabel,
  type Refusal,
  type SessionRow,
  type UserDetail,
  type UserRow,
  when,
} from "@/lib/admin";
import { bedsLabel, heardLine, money } from "@/lib/format";

// The renters list is the app's side panel: the same width as PANEL_WIDTH in Shell.tsx, written out so
// Tailwind sees the class.
const TOKEN_KEY = "landline-admin-token"; // this tab only: sessionStorage, never the URL

const REFUSED: Record<Refusal, string> = {
  token: "That token didn't work.",
  off: "The admin panel is off on this server. Set ADMIN_TOKEN on the API to turn it on.",
  missing: "That renter isn't there any more.",
  unreachable: "Can't reach the API right now.",
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
const SPEAKER: Record<string, string> = { agent: "Landline", renter: "Renter", landlord: "Listing agent" };
const ENDED: Record<string, string> = {
  hangup: "hung up",
  end_session: "said goodbye",
  no_answer: "no answer",
  higgs_error: "voice error",
  deadline: "hit the time limit",
};

const card = "rounded-[var(--radius-card)] bg-surface";
const hairline = { boxShadow: "0 0 0 1px var(--color-hair)" };

/** /admin: every renter who has called, and everything Landline stored about them. */
export function Admin() {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  useEffect(() => {
    try {
      setToken(sessionStorage.getItem(TOKEN_KEY));
    } catch {
      // storage blocked: ask every time
    }
    const fromHash = /^#u(\d+)$/.exec(window.location.hash);
    if (fromHash) setSelected(Number(fromHash[1]));
    setReady(true);
  }, []);

  const load = useCallback(async (t: string) => {
    const got = await adminGet<UserRow[]>("/api/admin/users", t);
    if (isRefusal(got)) {
      setRefusal(got);
      if (got === "token") forget(setToken);
      return;
    }
    setRefusal(null);
    setUsers(got);
  }, []);

  useEffect(() => {
    if (token) void load(token);
  }, [token, load]);

  const open = (id: number | null) => {
    setSelected(id);
    history.replaceState(null, "", id == null ? window.location.pathname : `#u${id}`);
  };

  if (!ready) return null;
  if (!token) {
    return (
      <TokenGate
        refusal={refusal}
        onToken={(t) => {
          try {
            sessionStorage.setItem(TOKEN_KEY, t);
          } catch {
            // kept in memory for this visit
          }
          setRefusal(null);
          setToken(t);
        }}
      />
    );
  }

  const list = (
    <UserList
      users={users}
      refusal={refusal}
      selected={selected}
      onOpen={open}
      onRefresh={() => void load(token)}
      onLock={() => {
        forget(setToken);
        setUsers(null);
        open(null);
      }}
    />
  );

  return (
    <div className="fixed inset-0 flex overflow-hidden bg-ground">
      <aside
        className={`${selected == null ? "flex" : "hidden"} w-full flex-col split:flex split:w-[clamp(340px,42vw,440px)] split:shrink-0`}
        style={{ boxShadow: "1px 0 0 var(--color-line)" }}
      >
        {list}
      </aside>
      <main className={`${selected == null ? "hidden" : "flex"} min-w-0 flex-1 flex-col split:flex`}>
        {selected == null ? (
          <p className="m-auto max-w-xs px-6 text-center text-support text-ink-3">
            Pick a renter to see their calls, what they said and the places they chose.
          </p>
        ) : (
          <UserView key={selected} userId={selected} token={token} onBack={() => open(null)} />
        )}
      </main>
    </div>
  );
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function forget(setToken: (t: string | null) => void) {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // nothing stored
  }
  setToken(null);
}

function TokenGate({ refusal, onToken }: { refusal: Refusal | null; onToken: (t: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="fixed inset-0 grid place-items-center overflow-y-auto bg-ground px-4">
      <form
        className={`${card} w-full max-w-sm p-5`}
        style={hairline}
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onToken(value.trim());
        }}
      >
        <p className="text-fact font-strong tracking-[-0.01em]">Landline</p>
        <h1 className="mt-3 text-title font-strong">Admin</h1>
        <p className="mt-1 text-support text-ink-2">
          Renters, their calls and transcripts. Enter the admin token; it stays in this tab only.
        </p>
        <label htmlFor="admin-token" className="mt-4 block text-meta font-label text-ink-3">
          Admin token
        </label>
        <input
          id="admin-token"
          type="password"
          autoComplete="off"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="mt-1.5 block min-h-11 w-full rounded-[var(--radius-control)] bg-ground px-3 text-support text-ink outline-none focus-visible:outline-2 focus-visible:outline-ink"
          style={hairline}
        />
        {refusal && <p className="mt-2 text-support text-dead">{REFUSED[refusal]}</p>}
        <button
          type="submit"
          disabled={!value.trim()}
          className="mt-4 min-h-11 w-full rounded-[var(--radius-control)] bg-ink px-4 text-support font-label text-white disabled:opacity-40"
        >
          Open
        </button>
      </form>
    </div>
  );
}

function UserList({
  users,
  refusal,
  selected,
  onOpen,
  onRefresh,
  onLock,
}: {
  users: UserRow[] | null;
  refusal: Refusal | null;
  selected: number | null;
  onOpen: (id: number) => void;
  onRefresh: () => void;
  onLock: () => void;
}) {
  return (
    <>
      <header className="px-4 pb-3 pt-5" style={{ paddingTop: "max(20px, env(safe-area-inset-top))" }}>
        <div className="flex items-center justify-between gap-3">
          <p className="text-fact font-strong tracking-[-0.01em]">Landline</p>
          <div className="flex items-center gap-1">
            <button type="button" onClick={onRefresh} className="min-h-11 rounded-[var(--radius-control)] px-2.5 text-support text-ink-2 hover:bg-press">
              Refresh
            </button>
            <button type="button" onClick={onLock} className="min-h-11 rounded-[var(--radius-control)] px-2.5 text-support text-ink-2 hover:bg-press">
              Lock
            </button>
          </div>
        </div>
        <h1 className="mt-1 text-title font-strong">Renters</h1>
        <p className="mt-0.5 text-meta text-ink-3">
          {users ? `${users.length} renter${users.length === 1 ? "" : "s"}, most recent first` : "Loading…"}
        </p>
        {refusal && <p className="mt-2 text-support text-dead">{REFUSED[refusal]}</p>}
      </header>
      <ul className="flex-1 space-y-3 overflow-y-auto px-4 pb-6">
        {users?.length === 0 && (
          <li className="text-support text-ink-2">Nobody has called yet. A renter shows up here after their first phone call.</li>
        )}
        {users?.map((u) => {
          const active = u.user_id === selected;
          return (
            <li key={u.user_id}>
              <button
                type="button"
                onClick={() => onOpen(u.user_id)}
                aria-current={active ? "true" : undefined}
                className={`${card} block w-full p-4 text-left transition-shadow`}
                style={{ boxShadow: active ? "0 0 0 2px var(--color-ink)" : hairline.boxShadow }}
              >
                <p className="flex items-baseline justify-between gap-3">
                  <span className="tnum text-fact font-strong">{phoneLabel(u.phone)}</span>
                  {u.last_phase && <span className="shrink-0 text-meta text-ink-3">{PHASE[u.last_phase] ?? u.last_phase}</span>}
                </p>
                {u.name && <p className="text-support text-ink-2">{u.name}</p>}
                <p className="mt-1 text-meta text-ink-3">
                  Last call {when(u.last_seen_at ?? u.created_at)} · {u.sessions} session{u.sessions === 1 ? "" : "s"} ·{" "}
                  {u.calls} call{u.calls === 1 ? "" : "s"}
                  {u.booked ? ` · ${u.booked} booked` : ""}
                </p>
                {u.summary && <p className="mt-2 line-clamp-2 text-support text-ink-2">{u.summary}</p>}
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function UserView({ userId, token, onBack }: { userId: number; token: string; onBack: () => void }) {
  const [user, setUser] = useState<UserDetail | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    adminGet<UserDetail>(`/api/admin/users/${userId}`, token, ctl.signal)
      .then((got) => (isRefusal(got) ? setRefusal(got) : setUser(got)))
      .catch(() => {});
    return () => ctl.abort();
  }, [userId, token]);

  const wants = user?.preferences ? heardLine(user.preferences) : [];

  return (
    <div className="flex-1 overflow-y-auto">
      <article className="mx-auto flex max-w-3xl flex-col gap-7 px-4 pb-10" style={{ paddingTop: "max(12px, env(safe-area-inset-top))" }}>
        <button
          type="button"
          onClick={onBack}
          className="-ml-2 mt-1 inline-flex min-h-11 items-center self-start rounded-[var(--radius-control)] px-2 text-support text-ink-2 hover:bg-press split:hidden"
        >
          ← All renters
        </button>
        {refusal && <p className="pt-4 text-support text-dead">{REFUSED[refusal]}</p>}
        {!user && !refusal && <p className="pt-4 text-support text-ink-3">Loading…</p>}
        {user && (
          <>
            <header className="split:pt-6">
              <h2 className="tnum text-title font-strong">{phoneLabel(user.phone)}</h2>
              <p className="mt-0.5 text-support text-ink-2">
                {[user.name, user.language && `speaks ${user.language}`].filter(Boolean).join(" · ") || "No name given"}
              </p>
              <p className="mt-1 text-meta text-ink-3">
                First call {when(user.created_at)} · last call {when(user.last_seen_at ?? user.created_at)}
              </p>
            </header>

            <Section title="What they want">
              {wants.length ? (
                <p className="text-fact">{capital(wants.join(" · "))}</p>
              ) : (
                <p className="text-support text-ink-2">Nothing saved yet.</p>
              )}
              {user.summary && <p className="mt-2 text-support text-ink-2">{user.summary}</p>}
              <p className="mt-1 text-meta text-ink-3">The summary is rebuilt from what they said, at the end of each call.</p>
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

            <Section title="Calls and sessions">
              {user.history.length ? (
                <ol className="space-y-4">
                  {user.history.map((s) => (
                    <SessionItem key={s.session_id} session={s} token={token} />
                  ))}
                </ol>
              ) : (
                <p className="text-support text-ink-2">No sessions stored.</p>
              )}
            </Section>

            {user.interactions.length > 0 && (
              <Section title="Everything they did, newest first">
                <ol className="space-y-2">
                  {user.interactions.map((i, n) => (
                    <li key={`${i.listing_id}-${i.action}-${n}`} className="text-support text-ink">
                      <span className="text-ink-3">{when(i.at)}</span> · {ACTION[i.action]?.label ?? i.action} {i.address}
                      {i.reason ? ` (${i.reason})` : ""}
                    </li>
                  ))}
                </ol>
              </Section>
            )}
          </>
        )}
      </article>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-meta font-label text-ink-3">{title}</h3>
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
        {place.url && (
          <>
            {" · "}
            <a href={place.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
              Listing
            </a>
          </>
        )}
      </p>
    </li>
  );
}

function SessionItem({ session, token }: { session: SessionRow; token: string }) {
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
            <CallItem key={c.call_id} call={c} token={token} />
          ))}
        </ul>
      )}
    </li>
  );
}

function CallItem({ call, token }: { call: CallRow; token: string }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<CallDetail | Refusal | null>(null);

  useEffect(() => {
    if (!open || detail) return;
    const ctl = new AbortController();
    adminGet<CallDetail>(`/api/admin/calls/${encodeURIComponent(call.call_id)}`, token, ctl.signal)
      .then(setDetail)
      .catch(() => {});
    return () => ctl.abort();
  }, [open, detail, call.call_id, token]);

  const who = call.role === "renter" ? "Their call with Landline" : `Call to ${call.address ?? "a listing"}`;
  const how = [
    duration(call.answered_at ?? call.created_at, call.ended_at),
    call.end_reason ? ENDED[call.end_reason] ?? call.end_reason.replaceAll("_", " ") : call.state,
    `${call.turns} line${call.turns === 1 ? "" : "s"}`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <li className="rounded-[var(--radius-control)] bg-ground px-3 py-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center justify-between gap-3 text-left"
      >
        <span className="min-w-0">
          <span className="block text-support font-label text-ink">{who}</span>
          <span className="block text-meta text-ink-3">{how}</span>
        </span>
        <span aria-hidden className="shrink-0 text-meta text-ink-3">
          {open ? "Hide" : "Transcript"}
        </span>
      </button>
      {open && (
        <div className="pb-2">
          {detail == null && <p className="text-meta text-ink-3">Loading…</p>}
          {isRefusal(detail) && <p className="text-support text-dead">{REFUSED[detail]}</p>}
          {detail && !isRefusal(detail) && (
            <>
              {detail.transcript.length ? (
                <ol className="mt-1 space-y-1.5">
                  {detail.transcript.map((t) => (
                    <li key={t.idx} className="text-support text-ink">
                      <span className="font-label text-ink-3">{SPEAKER[t.speaker] ?? t.speaker}</span> {t.text}
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="text-support text-ink-2">No lines stored for this call.</p>
              )}
              {detail.facts.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {detail.facts.map((f, n) => (
                    <li key={n}>
                      <p className="text-support text-ink">
                        {f.field.replaceAll("_", " ")}: {String(f.value)}
                      </p>
                      <p className="text-meta text-ink-3">Agent heard: &ldquo;{f.quote}&rdquo;</p>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}
