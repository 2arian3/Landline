/** Why /me's history didn't come back. `signin`: the Clerk session token was missing or no longer valid. */
export type MeRefusal = "phone" | "unknown" | "off" | "signin" | "unreachable";

/** The refusal an answer from /api/me means, or null for a history. */
export function refusal(status: number, body: { detail?: string } | null): MeRefusal | null {
  if (status === 401 || status === 403) return "signin";
  if (status === 422) return "phone";
  if (status === 404) return body?.detail === "unknown number" ? "unknown" : "off";
  return status >= 200 && status < 300 ? null : "unreachable";
}
