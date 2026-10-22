"use client";

import NumberFlow from "@number-flow/react";
import { money } from "@/lib/format";

const CAD = { style: "currency", currency: "CAD", currencyDisplay: "narrowSymbol", maximumFractionDigits: 0 } as const;

/**
 * The monthly cost, and the correction when a call found extra fees. Ported
 * from Realest: the real figure stays in ink as the biggest thing on the card,
 * the listed price sits beneath it struck *with the word "Listed"*, and only
 * the small delta carries colour.
 */
export function Money({ rent, monthly, lead = false }: { rent: number; monthly: number; lead?: boolean }) {
  const corrected = monthly !== rent;
  const delta = monthly - rent;
  // `relative` contains the absolutely positioned sr-only text; without it the
  // text anchors to the app shell and makes the page scrollable by 40px.
  return (
    <div className="relative min-w-0">
      <div aria-hidden className="flex items-baseline gap-1">
        <NumberFlow
          value={monthly}
          format={CAD}
          locales="en-CA"
          className={`${lead ? "text-price-lead" : "text-price"} font-strong tnum`}
        />
        <span className="text-meta text-ink-3">/mo</span>
      </div>
      {corrected && (
        <div aria-hidden className="mt-0.5 flex items-baseline gap-2 text-meta">
          <span className="text-ink-3">
            Listed <s className="tnum">{money(rent)}</s>
          </span>
          <span className="font-label tnum" style={{ color: delta > 0 ? "var(--color-dead)" : "var(--color-real)" }}>
            {delta > 0 ? "+" : "−"}
            {money(Math.abs(delta))}
          </span>
        </div>
      )}
      <span className="sr-only">
        {corrected ? `${money(monthly)} a month, listed as ${money(rent)}.` : `${money(monthly)} a month.`}
      </span>
    </div>
  );
}
