import { API_URL } from "./config";

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

/** The signed-in renter (U-11a): the API reads their phone number from the Clerk session token only. */
export async function getMe(token: string | null): Promise<Me> {
  if (!token) throw new ApiError("Your sign-in expired. Sign in again.");
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/me`, { headers: { Authorization: `Bearer ${token}` } });
  } catch {
    throw new ApiError("Can't reach Landline right now. Check your connection and try again.");
  }
  if (res.status === 401) throw new ApiError("Your sign-in expired. Sign in again.");
  if (res.status === 404) throw new ApiError("Landline doesn't know this number yet. Call Landline first.");
  if (!res.ok) throw new ApiError(`Landline answered ${res.status}. Try again in a moment.`);
  return (await res.json()) as Me;
}
