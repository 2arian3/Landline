import type { Metadata, Viewport } from "next";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

// No web font: Lucent sets the board in the system's own (SF Pro on Apple devices, then Helvetica Neue and
// Arial), named in app/globals.css (--font-text, --font-display), so nothing is fetched before the first paint.

export const metadata: Metadata = {
  title: "Landline",
  description: "Toronto rentals, checked by phone. Talk to it, and the map follows the conversation.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#fbfbfa",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" style={{ colorScheme: "light" }}>
      <body>{children}</body>
    </html>
  );
}
