import type { Metadata } from "next";
import { ConfirmViewing } from "@/components/ConfirmViewing";

export const metadata: Metadata = {
  title: "Confirm a viewing · Landline",
  description: "Confirm the viewing Landline booked, or say you can't make it.",
  robots: { index: false, follow: false }, // a private link from a text message
};

// O-08d: the link Landline texts the listing agent after a booking (O-08b).
export default async function ConfirmViewingPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <ConfirmViewing token={token} />;
}
