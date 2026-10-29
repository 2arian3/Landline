/**
 * The listing agent's confirm page (O-08d): GET /api/viewings/{token}, then POST …/confirm with {email} or
 * POST …/decline. The token in the texted link is the only key.
 */
import { api } from "./live";
import { type Answered, answered } from "./confirmAnswer";

export type ViewingInvite = {
  status: "pending" | "confirmed" | "declined";
  starts_at: string;
  when: string; // "Saturday, Oct 31 at 2 pm", Toronto time, written by the API
  address: string;
  renter_first_name?: string | null; // only when the renter shared their name
};

export async function loadViewing(token: string, signal?: AbortSignal): Promise<ViewingInvite | "unknown" | "unreachable"> {
  try {
    const res = await fetch(api(`/api/viewings/${encodeURIComponent(token)}`), { cache: "no-store", signal });
    if (res.status === 404) return "unknown";
    if (!res.ok) return "unreachable";
    return (await res.json()) as ViewingInvite;
  } catch (e) {
    if (signal?.aborted) throw e;
    return "unreachable";
  }
}

export async function answerViewing(token: string, action: "confirm" | "decline", email?: string): Promise<Answered> {
  try {
    const res = await fetch(api(`/api/viewings/${encodeURIComponent(token)}/${action}`), {
      method: "POST",
      headers: action === "confirm" ? { "Content-Type": "application/json" } : undefined,
      body: action === "confirm" ? JSON.stringify({ email }) : undefined,
      cache: "no-store",
    });
    return answered(res.status, await res.json().catch(() => null));
  } catch {
    return { kind: "unreachable" };
  }
}
