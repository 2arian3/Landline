"use client";

import { useEffect, useRef } from "react";
import { Icon } from "./Icon";

const PHONE = { label: "+1 (289) 512-2338", href: "tel:+12895122338" };

/** Set once the pop-up is closed, so it doesn't come back in this browser. */
const CLOSED = "landline-call-popup-closed";

/**
 * On /try, the first time a browser opens it: the same agent also answers a real phone number. Closing it
 * (the button, Escape, or a tap outside) keeps it closed in this browser. Solid, not glass: on a phone it
 * sits over the sheet's own text, and glass on glass would let that text through.
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
      className="m-auto w-[min(400px,calc(100%-32px))] max-w-none rounded-[var(--radius-lg)] bg-surface p-0 text-ink shadow-[var(--shadow-lift)] backdrop:bg-[rgb(25_25_24/0.32)] motion-safe:animate-[rise-in_var(--dur-enter)_var(--ease-settle)_both] min-[1600px]:w-[460px]"
    >
      <div className="proj p-6">
        <h2 id="call-popup-title" className="t-title">
          Test it with a phone call
        </h2>
        <p className="t-callout c-2 mt-2">
          Call our agent and tell it what you&rsquo;re looking for. It texts you a link to your live map while you
          talk.
        </p>
        <p className="t-price-lead mt-4">{PHONE.label}</p>
        <p className="t-caption c-2 mt-2">
          By phone, Landline calls only the team&rsquo;s own demo numbers, never real listing agents.
        </p>
        <div className="mt-6 flex flex-col gap-2">
          <a href={PHONE.href} className="btn btn-primary">
            <Icon name="phone" size={20} />
            Call now
          </a>
          <button type="button" onClick={() => ref.current?.close()} className="btn btn-secondary">
            Talk in the browser instead
          </button>
        </div>
      </div>
    </dialog>
  );
}
