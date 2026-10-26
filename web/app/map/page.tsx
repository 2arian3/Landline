import type { Metadata } from "next";
import { DemoBoard } from "@/components/DemoBoard";

export const metadata: Metadata = {
  title: "Map · Landline",
  description: "The live map of Toronto listings. Talk to it, and the map follows the conversation.",
};

// The board the root URL showed before the cover page. `?dev=1` adds the scripted demo's controls.
export default function MapPage() {
  return <DemoBoard />;
}
