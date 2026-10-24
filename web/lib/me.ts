/**
 * A renter's own history (POST /api/me): the phone number they call from is the login.
 * Transcripts and recordings stay on the admin routes.
 */
import type { UserDetail } from "./admin";
import { api } from "./live";

/** Why the history didn't come back. */
export type MeRefusal = "phone" | "unknown" | "off" | "unreachable";

export async function loadMe(phone: string, signal?: AbortSignal): Promise<UserDetail | MeRefusal> {
  try {
    const res = await fetch(api("/api/me"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone }),
      cache: "no-store",
      signal,
    });
    if (res.status === 422) return "phone";
    if (res.status === 404) {
      const body = (await res.json().catch(() => null)) as { detail?: string } | null;
      return body?.detail === "unknown number" ? "unknown" : "off";
    }
    if (!res.ok) return "unreachable";
    return (await res.json()) as UserDetail;
  } catch (e) {
    if (signal?.aborted) throw e;
    return "unreachable";
  }
}

export const isMeRefusal = (x: unknown): x is MeRefusal => typeof x === "string";
