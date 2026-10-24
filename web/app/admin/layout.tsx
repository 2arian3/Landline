import type { Metadata } from "next";

// The admin panel stays out of search results; nothing in the public app links to it.
export const metadata: Metadata = {
  title: "Landline admin",
  robots: { index: false, follow: false },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return children;
}
