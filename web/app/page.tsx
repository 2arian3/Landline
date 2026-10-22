import { LiveBoard } from "@/components/LiveBoard";

// The demo session, so a judge opening the root URL sees the live board straight away.
export default function Home() {
  return <LiveBoard sessionId="demo" />;
}
