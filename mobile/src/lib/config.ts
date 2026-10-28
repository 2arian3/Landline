import Constants from "expo-constants";

type Extra = { apiUrl?: string; clerkPublishableKey?: string; landlinePhone?: string };

const extra = (Constants.expoConfig?.extra ?? {}) as Extra;

/** The API the app calls directly, with no trailing slash. */
export const API_URL = (extra.apiUrl ?? "").replace(/\/+$/, "");
/** Clerk's publishable key; empty until mobile/.env sets it. */
export const CLERK_PUBLISHABLE_KEY = extra.clerkPublishableKey ?? "";
/** The number a renter calls Landline on, in E.164 (deploy/production.env TWILIO_PHONE_NUMBER). */
export const LANDLINE_PHONE = extra.landlinePhone ?? "";
