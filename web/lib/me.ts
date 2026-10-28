/**
 * A renter's own history. Signed in with Clerk (U-11b), GET /api/me with the session token: the API takes the
 * phone from the verified token. Without Clerk (local dev, CI), POST /api/me with the typed number, as before.
 * Transcripts and recordings stay on the admin routes.
 */
import type { UserDetail } from "./admin";
import { api } from "./live";
import { type MeRefusal, refusal } from "./meRefusal";

export type { MeRefusal };

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

async function answer(send: () => Promise<Response>, signal?: AbortSignal): Promise<UserDetail | MeRefusal> {
  try {
    const res = await send();
    const body = res.status === 404 ? ((await res.json().catch(() => null)) as { detail?: string } | null) : null;
    return refusal(res.status, body) ?? ((await res.json()) as UserDetail);
  } catch (e) {
    if (signal?.aborted) throw e;
    return "unreachable";
  }
}

export const isMeRefusal = (x: unknown): x is MeRefusal => typeof x === "string";
