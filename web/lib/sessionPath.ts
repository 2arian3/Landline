/**
 * The API path for a session's snapshot. A `?dev=1` page asks for the dev demo, whose new session opens with
 * its scripted brief; any other page's new session shows nothing heard until the renter says something.
 */
export function sessionPath(sessionId: string, search: string): string {
  const path = `/api/sessions/${encodeURIComponent(sessionId)}`;
  return new URLSearchParams(search).get("dev") === "1" ? `${path}?dev=1` : path;
}

/**
 * A session id in the shape code mints for a call (`s_` + 10 url-safe characters). The API answers 404 for
 * one that matches no live or stored session, which /s/[sid] shows as "We couldn't find this call"; any other
 * id is a demo session, made on first sight.
 */
export function isMinted(sessionId: string): boolean {
  return /^s_[A-Za-z0-9_-]{10}$/.test(sessionId);
}
