"use client";

import { useEffect, useState } from "react";
import { LiveBoard } from "@/components/LiveBoard";
import { Shell } from "@/components/Shell";

/**
 * The root URL opens a demo session of its own for each browser, so judges and
 * teammates trying it at the same time never move each other's map.
 */
export default function Home() {
  const [sessionId, setSessionId] = useState<string | null>(null);

  useEffect(() => {
    let id: string | null = null;
    try {
      id = localStorage.getItem("landline-demo-session");
    } catch {
      // storage blocked: a fresh session per visit is fine
    }
    if (!id) {
      id = `demo-${crypto.randomUUID().slice(0, 8)}`;
      try {
        localStorage.setItem("landline-demo-session", id);
      } catch {
        // see above
      }
    }
    setSessionId(id);
  }, []);

  if (!sessionId) return <Shell />;
  return <LiveBoard sessionId={sessionId} />;
}
