import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Instrument_Sans } from "next/font/google";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

// The full variable font, so the in-between weights (540, 580) render exactly.
const instrument = Instrument_Sans({ subsets: ["latin"], variable: "--font-instrument" });

// One job only: running call clocks.
const plex = IBM_Plex_Mono({ subsets: ["latin"], variable: "--font-plex", weight: ["400", "500"] });

export const metadata: Metadata = {
  title: "Landline",
  description: "Toronto rentals, checked by phone. Talk to it, and the map follows the conversation.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#f6f4f0",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${instrument.variable} ${plex.variable}`} style={{ colorScheme: "light" }}>
      <body>{children}</body>
    </html>
  );
}
