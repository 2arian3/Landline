import { LiveBoard } from "@/components/LiveBoard";

// The link texted to a renter at the start of a call. While the call is live, Landline's face lip-syncs it in
// the corner (A-11), silent unless turned up: the renter hears the call on the phone.
export default async function SessionPage({ params }: { params: Promise<{ sid: string }> }) {
  const { sid } = await params;
  return <LiveBoard sessionId={sid} avatar />;
}
