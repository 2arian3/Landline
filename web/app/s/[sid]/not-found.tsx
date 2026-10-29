import type { Metadata } from "next";
import Link from "next/link";
import { LANDLINE_PHONE } from "@/lib/phone";

export const metadata: Metadata = {
  title: "Call not found · Landline",
  robots: { index: false, follow: false },
};

/**
 * /s/<id> for an id that matches no call (PR9, the States board): an old or cut-short link said so plainly,
 * where it used to open an empty board, with the two ways forward. No map behind it, so a solid surface, not
 * glass.
 */
export default function NoSuchCall() {
  return (
    <main
      className="fixed inset-0 grid place-items-center overflow-y-auto bg-ground px-4"
      style={{ paddingTop: "max(24px, env(safe-area-inset-top))", paddingBottom: 24 }}
    >
      <div
        className="w-full max-w-[400px] bg-surface p-6"
        style={{ borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-glass)" }}
      >
        <p className="wordmark">
          <span className="mark" aria-hidden>
            <svg viewBox="0 0 32 32">
              <path d="M16 5.5c-4.1 0-7.5 3.3-7.5 7.4 0 5.3 7.5 13.6 7.5 13.6s7.5-8.3 7.5-13.6c0-4.1-3.4-7.4-7.5-7.4zm0 10.2a2.8 2.8 0 1 1 0-5.6 2.8 2.8 0 0 1 0 5.6z" />
            </svg>
          </span>
          Landline
        </p>
        <h1 className="t-headline mt-6">We couldn&rsquo;t find this call</h1>
        <p className="t-callout c-2 mt-1">
          The link may be cut short, or from an old call. Open it again from Landline&rsquo;s text, or start a new
          call.
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <a href={LANDLINE_PHONE.href} className="btn btn-primary flex-1">
            Call Landline
          </a>
          <Link href="/me" className="btn btn-secondary flex-1">
            Your calls
          </Link>
        </div>
      </div>
    </main>
  );
}
