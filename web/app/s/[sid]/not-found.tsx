import type { Metadata } from "next";
import Link from "next/link";
import { LANDLINE_PHONE } from "@/lib/phone";

export const metadata: Metadata = {
  title: "Call not found · Landline",
  robots: { index: false, follow: false },
};

const hairline = { boxShadow: "0 0 0 1px var(--color-hair)" };
const button =
  "inline-flex min-h-11 flex-1 items-center justify-center rounded-[var(--radius-control)] px-4 text-support font-label focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink";

/**
 * /s/<id> for an id that matches no call (PR9, the States board): an old or cut-short link said so plainly,
 * where it used to open an empty board, with the two ways forward.
 */
export default function NoSuchCall() {
  return (
    <main
      className="fixed inset-0 grid place-items-center overflow-y-auto bg-ground px-4"
      style={{ paddingTop: "max(24px, env(safe-area-inset-top))", paddingBottom: 24 }}
    >
      <div className="w-full max-w-sm rounded-[var(--radius-card)] bg-surface p-5" style={hairline}>
        <p className="mb-4 text-fact font-strong tracking-[-0.01em]">Landline</p>
        <h1 className="text-title font-strong">We couldn&rsquo;t find this call</h1>
        <p className="mt-2 text-support text-ink-2">
          The link may be cut short, or from an old call. Open it again from Landline&rsquo;s text, or start a new
          call.
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <a href={LANDLINE_PHONE.href} className={`${button} bg-ink text-white`}>
            Call Landline
          </a>
          <Link href="/me" className={`${button} text-ink`} style={hairline}>
            Your calls
          </Link>
        </div>
      </div>
    </main>
  );
}
