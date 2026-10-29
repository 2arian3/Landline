import { notFound } from "next/navigation";
import { LiveBoard } from "@/components/LiveBoard";
import { isMinted } from "@/lib/sessionPath";

// Server-side, the API's own address (the browser goes through /api or NEXT_PUBLIC_API_URL).
const API = (process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

/**
 * Whether the API says this link matches no call: only an id in the minted shape can (any other is a demo
 * session), and only a 404 counts. A slow or unreachable API renders the board, which says so itself.
 */
async function noSuchCall(sid: string): Promise<boolean> {
  if (!isMinted(sid)) return false;
  try {
    const res = await fetch(`${API}/api/sessions/${encodeURIComponent(sid)}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(2500),
    });
    return res.status === 404;
  } catch {
    return false;
  }
}

// The link texted to a renter at the start of a call.
export default async function SessionPage({ params }: { params: Promise<{ sid: string }> }) {
  const { sid } = await params;
  if (await noSuchCall(sid)) notFound();
  return <LiveBoard sessionId={sid} />;
}
