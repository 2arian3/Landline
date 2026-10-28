/**
 * A renter's own history. Signed in with Clerk (U-11b), GET /api/me with the session token: the API takes the
 * phone from the verified token. Without Clerk (local dev, CI), POST /api/me with the typed number, as before.
 * Transcripts and recordings stay on the admin routes.
 */
import type { UserDetail } from "./admin";
import { api } from "./live";
import { type MeRefusal, refusal } from "./meRefusal";

export type { MeRefusal };

/** A listing Landline called for the signed-in renter (GET /api/me/listings, U-12c). */
export interface MyListing {
  listing_id: string;
  address: string;
  unit_label?: string | null;
  monthly_cost?: number | null; // what it costs a month in all, from the catalogue
  session_id: string;
  call_id: string;
  called_at: string;
  ended: "talked" | "voicemail" | "no_answer";
  facts: HeardFact[];
  red_flags: string[];
  viewing?: { starts_at: string; slot_text: string; status: string } | null;
}

/** A fact a call heard, with its source: the agent's own words. */
export interface HeardFact {
  field: string;
  value: string;
  quote: string;
  at?: string | null;
}

export async function loadMe(phone: string, signal?: AbortSignal): Promise<UserDetail | MeRefusal> {
  return answer(
    () =>
      fetch(api("/api/me"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone }),
        cache: "no-store",
        signal,
      }),
    signal,
  );
}

/** The signed-in renter's history, with a Clerk session token from `getToken()`. */
export async function loadSignedIn(token: string, signal?: AbortSignal): Promise<UserDetail | MeRefusal> {
  return answer(
    () => fetch(api("/api/me"), { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal }),
    signal,
  );
}

/** Every listing Landline called for the signed-in renter, newest first. */
export async function loadMyListings(token: string, signal?: AbortSignal): Promise<MyListing[] | MeRefusal> {
  return answer(
    () =>
      fetch(api("/api/me/listings"), { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal }),
    signal,
  );
}

async function answer<T = UserDetail>(send: () => Promise<Response>, signal?: AbortSignal): Promise<T | MeRefusal> {
  try {
    const res = await send();
    const body = res.status === 404 ? ((await res.json().catch(() => null)) as { detail?: string } | null) : null;
    return refusal(res.status, body) ?? ((await res.json()) as T);
  } catch (e) {
    if (signal?.aborted) throw e;
    return "unreachable";
  }
}

export const isMeRefusal = (x: unknown): x is MeRefusal => typeof x === "string";
