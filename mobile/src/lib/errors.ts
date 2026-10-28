import type { Problems } from "./search";

/** Why the app couldn't load the renter, in words for the screen. */
export class ApiError extends Error {}

/** A save the API refused field by field (a 422): what's wrong with each. */
export class Refused extends ApiError {
  readonly problems: Problems;

  constructor(problems: Problems) {
    super("Check the fields marked above.");
    this.problems = problems;
  }
}

/** What a renter Landline doesn't know yet reads, in place of their calls: nothing has failed. */
export const NO_CALLS_YET = "No calls yet. Call Landline and say what you want.";

/** The API doesn't know this phone number yet: the renter hasn't called Landline. Not shown as an error. */
export class UnknownNumber extends ApiError {
  constructor() {
    super(NO_CALLS_YET);
  }
}

/** A 404 from /api/me, by its detail: a number Landline doesn't know yet, or a switch that's off. */
export function notFound(detail: string): ApiError {
  if (detail === "unknown number") return new UnknownNumber();
  return new ApiError(detail === "history is off"
    ? "Landline's history isn't switched on yet."
    : "Sign-in isn't switched on for Landline yet.");
}
