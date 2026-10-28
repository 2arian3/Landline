import { API_URL } from "./config";
import { type Edit, type MyProfile, type Problems, problemsFrom422 } from "./search";

/** The part of GET /api/me's UserDetail the app shows (server/landline/api/admin.py). */
export type Me = {
  user_id: number;
  phone: string;
  name: string | null;
  sessions: number;
  calls: number;
  booked: number;
};

/** Why the app couldn't load the renter, in words for the screen. */
export class ApiError extends Error {}

/** A save the API refused field by field (a 422): what's wrong with each. */
export class Refused extends ApiError {
  constructor(readonly problems: Problems) {
    super("Check the fields marked above.");
  }
}

/** One fact a listing call heard, with the listing agent's words (GET /api/me/listings, U-12c). */
export type HeardFact = { field: string; value: string; quote: string; at?: string | null };

/** One listing Landline called for the renter: how the call went, what it heard and the viewing. */
export type MyListing = {
  listing_id: string;
  address: string;
  unit_label?: string | null;
  session_id: string;
  call_id: string;
  called_at: string;
  ended: "talked" | "voicemail" | "no_answer";
  facts: HeardFact[];
  red_flags: string[];
  viewing?: { starts_at: string; slot_text: string; status: string } | null;
};

/** The signed-in renter (U-11a): the API reads their phone number from the Clerk session token only. */
export async function getMe(token: string | null): Promise<Me> {
  return (await call("/api/me", token)) as Me;
}

/** Every listing Landline called for the signed-in renter (U-12c). */
export async function getListings(token: string | null): Promise<MyListing[]> {
  return (await call("/api/me/listings", token)) as MyListing[];
}

/** The renter's saved search and what Landline may share (U-12a). */
export async function getProfile(token: string | null): Promise<MyProfile> {
  return (await call("/api/me/profile", token)) as MyProfile;
}

/** Saves only what changed; the API checks each field again, and a refused one throws `Refused`. */
export async function saveProfile(token: string | null, change: Edit): Promise<MyProfile> {
  return (await call("/api/me/profile", token, change)) as MyProfile;
}

/** A GET, or a PATCH of `change`, with the renter's Clerk token; any refusal throws in plain words. */
async function call(path: string, token: string | null, change?: Edit): Promise<unknown> {
  if (!token) throw new ApiError("Your sign-in expired. Sign in again.");
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, change === undefined ? { headers } : {
      method: "PATCH",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(change),
    });
  } catch {
    throw new ApiError("Can't reach Landline right now. Check your connection and try again.");
  }
  if (res.status === 422) throw new Refused(problemsFrom422(await res.json().catch(() => null)));
  if (res.status === 401) throw new ApiError("Landline couldn't check your sign-in. Sign out and in again.");
  if (res.status === 404) {
    // The API says which: a number it doesn't know yet, or sign-in not switched on (no CLERK_SECRET_KEY).
    const detail = await res.json().then((b: { detail?: string }) => b.detail ?? "", () => "");
    throw new ApiError(detail === "unknown number"
      ? "Landline doesn't know this number yet. Call Landline first."
      : detail === "history is off" ? "Landline's history isn't switched on yet."
        : "Sign-in isn't switched on for Landline yet.");
  }
  if (!res.ok) throw new ApiError(`Landline answered ${res.status}. Try again in a moment.`);
  return res.json();
}
