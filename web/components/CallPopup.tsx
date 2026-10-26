"use client";

import { useEffect, useRef } from "react";

const PHONE = { label: "+1 (289) 512-2338", href: "tel:+12895122338" };

/** Set once the pop-up is closed, so it doesn't come back in this browser. */
const CLOSED = "landline-call-popup-closed";

const floating = { boxShadow: "0 0 0 1px var(--color-line), 0 8px 28px rgba(26,24,21,.10)" };

/**
 * On /try, the first time a browser opens it: the same agent also answers a real phone number. Closing it
 * (the button, Escape, or a tap outside) keeps it closed in this browser.
 */
export function CallPopup() {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    let closed = false;
    try {
      closed = localStorage.getItem(CLOSED) === "1";
    } catch {
      // storage blocked: show it, and it comes back next visit
    }
    if (!closed && !ref.current?.open) ref.current?.showModal();
  }, []);

  function remember() {
    try {
      localStorage.setItem(CLOSED, "1");
    } catch {
      // see above
    }
  }

  return (
    <dialog
      ref={ref}
      aria-labelledby="call-popup-title"
      onClose={remember}
      // A tap on the dimmed page around the card lands on the dialog itself.
      onClick={(e) => e.target === e.currentTarget && e.currentTarget.close()}
      className="m-auto w-[min(400px,calc(100%-32px))] rounded-[var(--radius-card)] bg-surface p-0 text-ink backdrop:bg-ink/40"
      style={floating}
    >
      <div className="p-5">
        <h2 id="call-popup-title" className="text-title font-strong">
          Test it with a phone call
        </h2>
        <p className="mt-2 text-support text-ink-2">
          Call our agent and tell it what you&rsquo;re looking for. It texts you a link to your live map while you
          talk.
        </p>
        <p className="tnum mt-4 text-price-lead font-strong">{PHONE.label}</p>
        <p className="mt-3 text-meta text-ink-3">
          By phone, Landline calls only the team&rsquo;s own demo numbers, never real listing agents.
        </p>
        <div className="mt-5 flex flex-col gap-2">
          <a
            href={PHONE.href}
            className="inline-flex h-12 items-center justify-center rounded-[var(--radius-control)] bg-ink px-5 text-support font-label text-white"
          >
            Call now
          </a>
          <button
            type="button"
            onClick={() => ref.current?.close()}
            className="h-12 rounded-[var(--radius-control)] bg-surface px-5 text-support font-label text-ink hover:bg-press"
            style={{ boxShadow: "0 0 0 1px var(--color-line)" }}
          >
            Talk in the browser instead
          </button>
        </div>
      </div>
    </dialog>
  );
}
