"use client";

import { useEffect, useState } from "react";
import { Money } from "./Money";
import { bathsLabel, bedsLabel, sizeLabel, sourceName, unitLabel } from "@/lib/format";
import { api } from "@/lib/live";
import type { Card, ListingDetail as Detail } from "@/lib/types";

/**
 * One listing in full. Every fact carries where it came from: today that's
 * the listing itself; once calls run, what a person said appears above it
 * with their words.
 */
export function ListingDetail({ id, card, onBack }: { id: string; card?: Card; onBack: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const ctl = new AbortController();
    setDetail(null);
    setFailed(false);
    fetch(api(`/api/listings/${encodeURIComponent(id)}`), { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setDetail)
      .catch(() => !ctl.signal.aborted && setFailed(true));
    return () => ctl.abort();
  }, [id]);

  const src = detail ? sourceName(detail.source) : card ? sourceName(card.source) : "the listing";
  const rows: [string, string | null][] = detail
    ? [
        ["Bedrooms", bedsLabel(detail.beds)],
        ["Bathrooms", bathsLabel(detail.baths)],
        ["Size", sizeLabel(detail.sqft)],
        ["Type", unitLabel(detail.unit_type)],
        ["Furnished", detail.furnished == null ? null : detail.furnished ? "Yes" : "No"],
        ["Utilities", detail.utilities_included.length ? "Included" : null],
        ["Parking", known(detail.parking)],
        ["Pets", known(detail.pets)],
        ["Laundry", known(detail.laundry)],
      ]
    : [];

  return (
    <article className="flex min-h-full flex-col gap-5 pb-8">
      <div className="px-4 pt-3">
        <button
          type="button"
          onClick={onBack}
          className="-ml-2 rounded-[var(--radius-control)] px-2 py-1 text-support text-ink-2 hover:bg-press"
        >
          ← All places
        </button>
      </div>

      {detail && detail.photos.length > 0 && (
        <figure className="m-0">
          <div className="flex snap-x snap-mandatory gap-2 overflow-x-auto px-4 [scrollbar-width:none]">
            {detail.photos.map((p, i) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={p}
                src={p}
                alt={i === 0 ? `Photo of ${detail.address}` : ""}
                loading={i < 2 ? "eager" : "lazy"}
                className="aspect-[4/3] w-[82%] shrink-0 snap-start rounded-[var(--radius-photo)] bg-press object-cover sm:w-[70%]"
              />
            ))}
          </div>
          <figcaption className="mt-1.5 px-4 text-meta text-ink-3">Photos: {src}</figcaption>
        </figure>
      )}

      <header className="px-4">
        {card || detail ? (
          <Money rent={(detail ?? card)!.rent} monthly={(detail ?? card)!.monthly_cost} lead />
        ) : null}
        <h2 className="mt-2 text-title font-strong">{detail?.address ?? card?.address ?? " "}</h2>
        {detail?.building_name && detail.building_name !== detail.address && (
          <p className="mt-0.5 text-support text-ink-2">{detail.building_name}</p>
        )}
        {card?.reasons.length ? (
          <p className="mt-2 text-support text-ink-2">{card.reasons.join(" · ")}</p>
        ) : null}
      </header>

      <section className="px-4" aria-labelledby="phone-heading">
        <h3 id="phone-heading" className="text-meta font-label text-ink-3">
          Checked by phone
        </h3>
        {card?.facts.length ? (
          <ul className="mt-2 space-y-3">
            {card.facts.map((f) => (
              <li key={f.field + f.quote}>
                <p className="text-fact">{f.value}</p>
                <p className="text-meta text-ink-3">
                  “{f.quote}” · {f.speaker}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1.5 text-support text-ink-2">
            Nobody has called about this one yet. Ask Landline to call, and what the listing agent says lands
            here, with their words.
          </p>
        )}
      </section>

      <section className="px-4" aria-labelledby="listing-heading">
        <h3 id="listing-heading" className="text-meta font-label text-ink-3">
          What the listing says
        </h3>
        {failed && <p className="mt-1.5 text-support text-ink-2">Couldn’t load the details. They’ll be back shortly.</p>}
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-3">
          {rows
            .filter(([, v]) => v)
            .map(([k, v]) => (
              <div key={k} className="min-w-0">
                <dt className="text-meta text-ink-3">{k}</dt>
                <dd className="m-0 truncate text-fact">{v}</dd>
              </div>
            ))}
        </dl>
        {detail && detail.amenities.length > 0 && (
          <p className="mt-3 text-support text-ink-2">Building: {detail.amenities.slice(0, 8).join(", ")}</p>
        )}
        {detail && (
          <p className="mt-4 text-meta text-ink-3">
            From {src}, last seen {new Date(detail.last_seen).toLocaleDateString("en-CA", { month: "short", day: "numeric" })}.{" "}
            <a href={detail.url} target="_blank" rel="noreferrer" className="text-ink-2 underline underline-offset-2">
              See the listing on {src} ↗
            </a>
          </p>
        )}
      </section>
    </article>
  );
}

function known(v: string): string | null {
  if (!v || v === "unknown") return null;
  return v.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}
