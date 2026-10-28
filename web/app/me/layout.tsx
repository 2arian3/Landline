import { ClerkProvider } from "@clerk/nextjs";
import type { ReactNode } from "react";

/**
 * /me signs in with Clerk (U-11b) when the publishable key is set. Without it (local dev, CI) the page keeps
 * the typed-number login the API still accepts there. Only /me loads Clerk, so the map stays as light as it was.
 */
export default function MeLayout({ children }: { children: ReactNode }) {
  const key = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  return key ? <ClerkProvider publishableKey={key}>{children}</ClerkProvider> : <>{children}</>;
}
