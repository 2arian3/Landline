"use client";

import { useEffect, useState } from "react";
import { postJSON } from "@/lib/live";
import type { Preferences } from "@/lib/types";

/**
 * Shown with ?dev=1. Stands in for the voice agent's update_preferences and
 * call_listings calls until the Concierge exists, so the live loop (POST → SSE
 * → re-rank → map) and every call status can be exercised from a browser. The
 * returning-renter switch stands in for a known phone number (O-05).
 */
export function DemoControls({
  sessionId,
  prefs,
  calling,
  returning,
  userName,
}: {
  sessionId: string;
  prefs: Preferences;
  calling: boolean;
  returning: boolean;
  userName: string | null;
}) {
  const [budget, setBudget] = useState(prefs.max_monthly ?? 2800);
  const [beds, setBeds] = useState(prefs.min_beds ?? 1);
  const [busy, setBusy] = useState(false);
  const [dialing, setDialing] = useState(false);
  // The renter from docs/DEMO.md's second call. The switch shows the tap at once; the server wins next version.
  const [name, setName] = useState(userName ?? "Sara");
  const [back, setBack] = useState(returning);
  useEffect(() => setBack(returning), [returning]);

  function welcome(on: boolean) {
    setBack(on);
    void postJSON(`/api/sessions/${encodeURIComponent(sessionId)}/returning`, { returning: on, name });
  }

  async function apply() {
    setBusy(true);
    await postJSON(`/api/sessions/${encodeURIComponent(sessionId)}/preferences`, {
      ...prefs,
      max_monthly: budget,
      min_beds: beds,
    });
    setBusy(false);
  }

  async function callTop() {
    setDialing(true);
    await postJSON(`/api/sessions/${encodeURIComponent(sessionId)}/calls`, {});
    setDialing(false);
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
      <button
        type="button"
        onClick={() => void callTop()}
        disabled={calling || dialing}
        className="mt-2 w-full rounded-[var(--radius-control)] bg-surface py-2 text-support font-label text-ink hover:bg-press disabled:opacity-60"
        style={{ boxShadow: "0 0 0 1px var(--color-line)" }}
      >
        Call the top 3
      </button>
      <p className="mt-1.5 text-meta text-ink-3">Three scripted agents answer. No phone rings.</p>
      <div className="mt-3 flex items-center gap-3">
        <label className="flex shrink-0 items-center gap-2 text-meta text-ink-2" htmlFor="demo-returning">
          <input
            id="demo-returning"
            type="checkbox"
            checked={back}
            onChange={(e) => welcome(e.target.checked)}
            className="h-4 w-4 accent-[var(--color-ink)]"
          />
          Returning renter
        </label>
        <input
          aria-label="Returning renter's name"
          value={name}
          maxLength={40}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => back && welcome(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              e.currentTarget.blur();
            }
          }}
          className="min-w-0 flex-1 rounded-[10px] bg-press px-2 py-1.5 text-support text-ink"
        />
      </div>
    </form>
  );
}
