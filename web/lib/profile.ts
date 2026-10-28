/**
 * The renter's profile (U-12a): GET and PATCH /api/me/profile, signed in with Clerk like GET /api/me. The
 * phone comes from the session token, never from the page.
 */
import { api } from "./live";
import { type MeRefusal, refusal } from "./meRefusal";
import { type Edit, type MyProfile, type Problems, problemsFrom422 } from "./searchForm";

export async function loadProfile(token: string, signal?: AbortSignal): Promise<MyProfile | MeRefusal> {
  try {
    const res = await fetch(api("/api/me/profile"), {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal,
    });
    const body = await res.json().catch(() => null);
    return refusal(res.status, body) ?? (body as MyProfile);
  } catch (e) {
    if (signal?.aborted) throw e;
    return "unreachable";
  }
}

/** What a save came back with: the saved profile, the fields the API refused, or why it couldn't be asked. */
export type SaveResult = { profile: MyProfile } | { problems: Problems } | { refused: MeRefusal };

export async function saveProfile(token: string, change: Edit): Promise<SaveResult> {
  try {
    const res = await fetch(api("/api/me/profile"), {
      method: "PATCH",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(change),
      cache: "no-store",
    });
    const body = await res.json().catch(() => null);
    if (res.status === 422) return { problems: problemsFrom422(body) };
    const refused = refusal(res.status, body);
    return refused ? { refused } : { profile: body as MyProfile };
  } catch {
    return { refused: "unreachable" };
  }
}
