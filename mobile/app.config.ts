import type { ConfigContext, ExpoConfig } from "expo/config";

// The app's only settings, and none is a secret (nothing secret goes in mobile/): Clerk's publishable key,
// the API's address and the number renters call. Set them in mobile/.env (see .env.example). With no key, the
// app says so.
export default ({ config }: ConfigContext): ExpoConfig => ({
  ...(config as ExpoConfig),
  extra: {
    ...config.extra,
    apiUrl: process.env.EXPO_PUBLIC_API_URL || "https://landline.134.87.8.230.sslip.io",
    clerkPublishableKey: process.env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY || "",
    landlinePhone: process.env.EXPO_PUBLIC_LANDLINE_PHONE || "+12895122338",   // production's renter line
  },
});
