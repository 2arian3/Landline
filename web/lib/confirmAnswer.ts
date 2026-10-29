/** What the confirm page (O-08d) makes of the API's answers, kept apart so it can be tested on its own. */

export type Decided = "confirmed" | "declined";

/** What a confirm or a decline came to. */
export type Answered =
  | { kind: "done"; status: Decided; inviteSent: boolean } // this answer decided it
  | { kind: "already"; status: string } // decided before, and how
  | { kind: "bad_email" }
  | { kind: "unknown" }
  | { kind: "unreachable" };

/** The same check the API makes (orchestrator/viewings.py valid_email), so a typo is caught before sending. */
export const isEmail = (text: string): boolean => {
  const t = text.trim();
  return t.length <= 254 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(t);
};

/** POST …/confirm or …/decline's status and body, as what happened. */
export function answered(status: number, body: unknown): Answered {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (status >= 200 && status < 300) {
    const b = body as { status?: string; invite_sent?: boolean } | null;
    const s = b?.status;
    return s === "confirmed" || s === "declined"
      ? { kind: "done", status: s, inviteSent: b?.invite_sent === true }
      : { kind: "unreachable" };
  }
  if (status === 409) {
    const s = (detail as { status?: string } | null)?.status;
    return { kind: "already", status: s ?? "decided" };
  }
  if (status === 422) return { kind: "bad_email" };
  if (status === 404) return { kind: "unknown" };
  return { kind: "unreachable" };
}
