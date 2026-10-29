/**
 * The API path for a session's snapshot. A `?dev=1` page asks for the dev demo, whose new session opens with
 * its scripted brief; any other page's new session shows nothing heard until the renter says something.
 */
export function sessionPath(sessionId: string, search: string): string {
  const path = `/api/sessions/${encodeURIComponent(sessionId)}`;
  return new URLSearchParams(search).get("dev") === "1" ? `${path}?dev=1` : path;
}

