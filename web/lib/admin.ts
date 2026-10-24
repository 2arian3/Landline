/**
 * The admin panel's API (server/landline/api/admin.py): every renter, their sessions, calls and
 * transcripts. Kept out of lib/types.ts, which holds the frozen contracts the live board reads.
 * Every request carries the admin token as a bearer header, never in the URL.
 */
import { api } from "./live";
import type { Preferences } from "./types";

export type UserRow = {
  user_id: number;
  phone: string;
  name: string | null;
  language: string | null;
  created_at: string;
  last_seen_at: string | null;
  sessions: number;
  calls: number;
  booked: number;
  last_phase: string | null;
  summary: string | null;
};

export type Place = {
  listing_id: string;
  address: string;
  unit_label: string | null;
  monthly_cost: number | null;
  beds: number | null;
  url: string | null;
};

export type ChosenPlace = Place & { actions: string[]; reason: string | null; last_at: string | null };

export type Interaction = {
  listing_id: string;
  address: string;
  action: string;
  reason: string | null;
  attribute: string | null;
  at: string;
  session_id: string | null;
};

export type CallRow = {
  call_id: string;
  role: "renter" | "listing";
  transport: string;
  listing_id: string | null;
  address: string | null;
  state: string;
  end_reason: string | null;
  created_at: string;
  answered_at: string | null;
  ended_at: string | null;
  turns: number;
};

export type SessionRow = {
  session_id: string;
  channel: string;
  phase: string;
  started_at: string;
  ended_at: string | null;
  preferences: Preferences | null;
  plan: Place[];
  results: string | null;
  agent_says: string | null;
  calls: CallRow[];
};

export type UserDetail = UserRow & {
  consent_memory: boolean;
  preferences: Preferences | null;
  profile_updated_at: string | null;
  places: ChosenPlace[];
  interactions: Interaction[];
  history: SessionRow[];
};

export type Turn = { idx: number; speaker: "agent" | "landlord" | "renter"; text: string; t_ms: number | null };
export type Fact = { field: string; value: unknown; quote: string; confirmed: boolean; created_at: string };
export type CallDetail = CallRow & { session_id: string | null; transcript: Turn[]; facts: Fact[] };

/** Why a request didn't return data: the token is wrong, the panel is off, or the API can't be reached. */
export type Refusal = "token" | "off" | "missing" | "unreachable";

/** GET an admin path, written out in full at each call (`/api/admin/users`) so the endpoint check can read it. */
export async function adminGet<T>(path: string, token: string, signal?: AbortSignal): Promise<T | Refusal> {
  try {
    const res = await fetch(api(path), {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal,
    });
    if (res.status === 401) return "token";
    if (res.status === 404) return path === "/api/admin/users" ? "off" : "missing";
    if (!res.ok) return "unreachable";
    return (await res.json()) as T;
  } catch (e) {
    if (signal?.aborted) throw e;
    return "unreachable";
  }
}

export const isRefusal = (x: unknown): x is Refusal => typeof x === "string";

const WHEN = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/Toronto",
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
});

/** A moment in Toronto time, e.g. "Sat, Oct 24, 1:12 a.m.". Never relative: the page can't keep it true. */
export const when = (iso: string | null) => (iso ? WHEN.format(new Date(iso)) : null);

/** +14165550123 as +1 416 555 0123. */
export function phoneLabel(e164: string): string {
  const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164);
  return m ? `+1 ${m[1]} ${m[2]} ${m[3]}` : e164;
}

/** How long a call lasted, from its stored start and end. */
export function duration(from: string, to: string | null): string | null {
  if (!to) return null;
  const s = Math.max(0, Math.round((new Date(to).getTime() - new Date(from).getTime()) / 1000));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}
