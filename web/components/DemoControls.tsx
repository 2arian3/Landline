"use client";

import { useState } from "react";
import { postJSON } from "@/lib/live";
import type { Preferences } from "@/lib/types";

/**
 * Shown with ?dev=1. Stands in for the voice agent's update_preferences call
 * until the Concierge exists, so the live loop (POST → SSE → re-rank → map)
 * can be exercised from a browser.
 */
export function DemoControls({ sessionId, prefs }: { sessionId: string; prefs: Preferences }) {
  const [budget, setBudget] = useState(prefs.max_monthly ?? 2800);
  const [beds, setBeds] = useState(prefs.min_beds ?? 1);
  const [busy, setBusy] = useState(false);

  async function apply() {
    setBusy(true);
    await postJSON(`/api/sessions/${encodeURIComponent(sessionId)}/preferences`, {
      ...prefs,
      max_monthly: budget,
      min_beds: beds,
    });
    setBusy(false);
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void apply();
      }}
      className="rounded-[var(--radius-card)] bg-surface p-3"
      style={{ boxShadow: "0 0 0 1px var(--color-line)" }}
    >
      <p className="text-meta text-ink-3">Demo controls · stand in for the voice agent</p>
      <div className="mt-2 grid grid-cols-[1fr_auto] items-end gap-3">
        <label className="min-w-0 text-meta text-ink-2" htmlFor="demo-budget">
          Budget <span className="tnum text-ink">${budget.toLocaleString("en-CA")}</span>
          <input
            id="demo-budget"
            type="range"
            min={1200}
            max={5000}
            step={50}
            value={budget}
            onChange={(e) => setBudget(Number(e.target.value))}
            className="mt-1 block w-full accent-[var(--color-ink)]"
          />
        </label>
        <label className="text-meta text-ink-2" htmlFor="demo-beds">
          Beds
          <select
            id="demo-beds"
            value={beds}
            onChange={(e) => setBeds(Number(e.target.value))}
            className="mt-1 block rounded-[10px] bg-press px-2 py-1.5 text-support text-ink"
          >
            {[0, 1, 2, 3].map((n) => (
              <option key={n} value={n}>
                {n === 0 ? "Studio+" : `${n}+`}
              </option>
            ))}
          </select>
        </label>
      </div>
      <button
        type="submit"
        disabled={busy}
        className="mt-3 w-full rounded-[var(--radius-control)] bg-ink py-2 text-support font-label text-white disabled:opacity-60"
      >
        {busy ? "Updating…" : "Say it"}
      </button>
    </form>
  );
}
