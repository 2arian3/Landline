import { LiveBoard } from "@/components/LiveBoard";

// The link texted to a renter at the start of a call. A link that matches no call is the API's 404, which the
// board turns into this route's not-found page (useLiveSession's `missing`), so nothing waits on the API here.
export default async function SessionPage({ params }: { params: Promise<{ sid: string }> }) {
  const { sid } = await params;
  return <LiveBoard sessionId={sid} />;
}
