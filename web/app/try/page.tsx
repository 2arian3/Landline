import type { Metadata } from "next";
import { JudgeMode } from "@/components/JudgeMode";

export const metadata: Metadata = {
  title: "Try Landline",
  description: "Talk to Landline in your browser. The map follows the conversation; the listing agents are simulated.",
};

// Judge mode (U-06): browser audio, simulated listing agents, never the phone network.
export default function TryPage() {
  return <JudgeMode />;
}
