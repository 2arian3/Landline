import { LiveBoard } from "@/components/LiveBoard";

// The link texted to a renter at the start of a call.
export default async function SessionPage({ params }: { params: Promise<{ sid: string }> }) {
  const { sid } = await params;
  return <LiveBoard sessionId={sid} />;
}
