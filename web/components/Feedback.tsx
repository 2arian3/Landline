"use client";

import { useState } from "react";

export type Reaction = "like" | "dislike" | "reject";

/** The reasons the ranking understands (docs/SCORING.md, "Feedback moves weights"). */
const REASONS = ["Too dark", "Too small", "Too far", "Too expensive"] as const;

/**
 * The tap version of what the renter can also just say ("that one's too dark").
 * Like, or not for me with a reason; the reason is what moves the ranking.
 */
export function Feedback({
  current,
  onReact,
}: {
  current: string | null | undefined;
  onReact: (reaction: Reaction, reason: string | null) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [editing, setEditing] = useState(false);

  if (current && !editing) {
    return (
      <div className="flex items-center justify-between gap-3">
        <p className="text-support text-ink">
          <span className="font-label">You:</span> {current.charAt(0).toLowerCase() + current.slice(1)}
        </p>
        <button
          type="button"
          onClick={() => {
            setEditing(true);
            setAsking(false);
          }}
          className="-mr-2 rounded-[var(--radius-control)] px-2 py-1 text-support text-ink-2 hover:bg-press"
        >
          Change
        </button>
      </div>
    );
  }

  const done = (reaction: Reaction, reason: string | null) => {
    onReact(reaction, reason);
    setAsking(false);
    setEditing(false);
  };

  return (
    <div>
      {!asking ? (
        <div className="grid grid-cols-2 gap-2">
          <Choice onClick={() => done("like", null)}>Like it</Choice>
          <Choice onClick={() => setAsking(true)}>Not for me</Choice>
        </div>
      ) : (
        <fieldset className="m-0 border-0 p-0">
          <legend className="text-meta text-ink-3">What&rsquo;s wrong with it? This changes what ranks first.</legend>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {REASONS.map((r) => (
              <Choice key={r} onClick={() => done("reject", r.toLowerCase())}>
                {r}
              </Choice>
            ))}
          </div>
          <div className="mt-2 flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={() => done("reject", null)}
              className="-ml-2 rounded-[var(--radius-control)] px-2 py-1 text-support text-ink-2 hover:bg-press"
            >
              Just not for me
            </button>
            <button
              type="button"
              onClick={() => setAsking(false)}
              className="-mr-2 rounded-[var(--radius-control)] px-2 py-1 text-support text-ink-2 hover:bg-press"
            >
              Cancel
            </button>
          </div>
        </fieldset>
      )}
      <p className="mt-2 text-meta text-ink-3">Or say it on the call: &ldquo;that one&rsquo;s too dark.&rdquo;</p>
    </div>
  );
}

function Choice({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="h-11 rounded-[var(--radius-control)] bg-surface px-3 text-support font-label text-ink transition-colors duration-150 hover:bg-press active:bg-press"
      style={{ boxShadow: "0 0 0 1px var(--color-line)" }}
    >
      {children}
    </button>
  );
}
