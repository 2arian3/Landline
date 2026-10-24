"use client";

import { useEffect, useState } from "react";
import { Captions } from "./Captions";
import { Feedback, type Reaction } from "./Feedback";
import { MARK, type Mark, STATUS } from "./ListingCard";
import { Money } from "./Money";
import { bathsLabel, bedsLabel, sizeLabel, sourceName, unitLabel } from "@/lib/format";
import { api } from "@/lib/live";
import { callNotes } from "@/lib/outcome";
import type { CallView, Card, ListingDetail as Detail, Provenance } from "@/lib/types";

export type { Reaction };

/** How each provenance source reads to a renter. Plain listing data needs no label: the section says so. */
const SOURCE_LABEL: Record<string, string | null> = {
  scraped: null,
  llm: "From the description",
  city: "City record",
  call: "Agent heard",
  derived: "Worked out",
  synthetic: "Estimated",
};

/**
 * One listing in full. Order is by what the renter must not miss: anything
 * off first, then their own say, then what a person said on the phone (with
 * their words), then what the listing claims, each with where it came from.
 */
export function ListingDetail({
  id,
  card,
  mark,
  call,
  results,
  onBack,
  onReact,
}: {
  id: string;
  card?: Card;
  mark?: Mark;
  /** The place's latest call, whose captions show while it runs. */
  call?: CallView;
  /** The session's results text once its calls are done; this place's sentences show here. */
  results?: string | null;
  onBack: () => void;
  onReact?: (reaction: Reaction, reason: string | null) => void;
}) {
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
  const status = mark ? MARK[mark] : card ? STATUS[card.status] : undefined;
  const rows: [field: string, label: string, value: string | null][] = detail
    ? [
        ["beds", "Bedrooms", bedsLabel(detail.beds)],
        ["baths", "Bathrooms", bathsLabel(detail.baths)],
        ["sqft", "Size", sizeLabel(detail.sqft)],
        ["unit_type", "Type", unitLabel(detail.unit_type)],
        ["floor", "Floor", detail.floor != null ? String(detail.floor) : null],
        ["furnished", "Furnished", detail.furnished == null ? null : detail.furnished ? "Yes" : "No"],
        ["utilities_included", "Utilities", detail.utilities_included.length ? "Included" : null],
        ["parking", "Parking", known(detail.parking)],
        ["pets", "Pets", known(detail.pets)],
        ["laundry", "Laundry", known(detail.laundry)],
      ]
    : [];
  const shown = rows.filter(([, , v]) => v);
  const address = detail?.address ?? card?.address;
  const place = [detail?.building_name, card?.neighbourhood].find((p) => p && p !== address) ?? null;
  const flags = card?.red_flags ?? [];
  const notes = callNotes(card, results);
  const asked = notes.questions.length > 0;

  return (
    <article className="flex min-h-full flex-col gap-6 pb-8">
      <div className="flex items-center justify-between gap-3 px-4 pt-3">
        <button
          type="button"
          onClick={onBack}
          className="-ml-2 rounded-[var(--radius-control)] px-2 py-1 text-support text-ink-2 hover:bg-press"
        >
          ← All places
        </button>
        {status && (
          <p className="flex items-center gap-1.5 text-meta font-label" style={{ color: status.color }}>
            <span
              aria-hidden
              className={`h-1.5 w-1.5 rounded-full ${card?.status === "calling" || mark === "ringing" ? "live-dot" : ""}`}
              style={{ background: status.color }}
            />
            {status.word}
          </p>
        )}
      </div>

      {detail && detail.photos.length > 0 && (
        <figure className="m-0 -mt-2">
          <div className="flex snap-x snap-mandatory gap-2 overflow-x-auto px-4 [scrollbar-width:none]">
            {detail.photos.map((p, i) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={p}
                src={p}
                alt={i === 0 ? `Photo of ${detail.address}` : ""}
                loading={i < 2 ? "eager" : "lazy"}
                className="aspect-[4/3] w-[82%] shrink-0 snap-start overflow-hidden rounded-[var(--radius-photo)] bg-press object-cover text-[0px] sm:w-[70%]"
              />
            ))}
          </div>
          <figcaption className="mt-1.5 px-4 text-meta text-ink-3">Photos: {src}</figcaption>
        </figure>
      )}

      <header className="px-4">
        {card || detail ? (
          // The card is the session's truth: it carries what calls changed (+$180 parking).
          <Money rent={(card ?? detail)!.rent} monthly={(card ?? detail)!.monthly_cost} lead />
        ) : null}
        <h2 className="mt-2 text-title font-strong">{detail?.address ?? card?.address ?? " "}</h2>
        {place && <p className="mt-0.5 text-support text-ink-2">{place}</p>}
        {card?.reasons.length ? <p className="mt-2 text-support text-ink-2">{card.reasons.join(" · ")}</p> : null}
        {card?.watch_out && <p className="mt-1 text-support text-dead">{card.watch_out}</p>}
      </header>

      {/* A booked viewing is what the renter acts on next, so it leads. */}
      {notes.viewing && (
        <section className="px-4" aria-labelledby="viewing-heading">
          <h3 id="viewing-heading" className="text-meta font-label text-real">
            Viewing booked
          </h3>
          <p className="mt-1 text-fact">{notes.viewing}</p>
          <p className="text-meta text-ink-3">Booked on the call, inside the times you gave</p>
        </section>
      )}

      {flags.length > 0 && (
        <section className="px-4" aria-labelledby="flags-heading">
          <h3 id="flags-heading" className="text-meta font-label text-dead">
            {flags.length === 1 ? "Red flag" : `${flags.length} red flags`}
          </h3>
          <ul className="mt-2 space-y-2">
            {flags.map((f) => (
              <li key={f} className="flex gap-2 text-support text-ink">
                <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-dead" />
                {f}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-meta text-ink-3">Landline won&rsquo;t book a viewing here.</p>
        </section>
      )}

      {onReact && (
        <section className="px-4" aria-labelledby="take-heading">
          <h3 id="take-heading" className="text-meta font-label text-ink-3">
            Your take
          </h3>
          <div className="mt-2">
            <Feedback current={card?.reaction} onReact={onReact} />
          </div>
        </section>
      )}

      {/* With only the booking to say, the section above already said it. */}
      {(card?.facts.length || asked || !notes.viewing || call) && (
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
            !asked && <p className="mt-1.5 text-support text-ink-2">{noFacts(card?.status, mark)}</p>
          )}
          {notes.questions.map((q) => (
            <div key={q} className="mt-3">
              <p className="text-fact">The agent asked, &ldquo;{q}&rdquo;</p>
              <p className="text-meta text-ink-3">Landline said it would check with you</p>
            </div>
          ))}
          {call && <Captions key={call.call_id} call={call} />}
        </section>
      )}

      <section className="px-4" aria-labelledby="listing-heading">
        <h3 id="listing-heading" className="text-meta font-label text-ink-3">
          What the listing says
        </h3>
        {failed && <p className="mt-1.5 text-support text-ink-2">Couldn&rsquo;t load the details. They&rsquo;ll be back shortly.</p>}
        {!detail && !failed && (
          <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-3" aria-hidden>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-10 rounded bg-press" />
            ))}
          </div>
        )}
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-3">
          {shown.map(([field, label, value]) => (
            <div key={field} className="min-w-0">
              <dt className="text-meta text-ink-3">{label}</dt>
              <dd className="m-0 truncate text-fact">{value}</dd>
              <Source p={detail?.provenance?.[field]} />
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

/** A field's source when it isn't the listing itself, with the quote it came from on tap. */
function Source({ p }: { p?: Provenance }) {
  const label = p ? SOURCE_LABEL[p.source] : null;
  if (!p || !label) return null;
  const year = p.source === "city" && p.at ? ` (${new Date(p.at).getFullYear()})` : "";
  if (!p.quote) return <p className="text-meta text-ink-3">{label + year}</p>;
  return (
    <details className="group text-meta text-ink-3">
      <summary className="cursor-pointer list-none underline decoration-dotted underline-offset-2">
        {label + year}
      </summary>
      <p className="mt-1 text-ink-2">&ldquo;{p.quote}&rdquo;</p>
    </details>
  );
}

function noFacts(status?: Card["status"], mark?: Mark): string {
  if (mark === "will_call") return "Landline calls them once you hang up. What they say lands here, with their words.";
  if (mark === "queued") return "Waiting for its turn on the phone.";
  if (mark === "ringing") return "Ringing now.";
  if (status === "calling") return "Landline is on the phone with them now. What they say lands here, with their words.";
  if (status === "no_answer") return "Nobody picked up yet.";
  return "Nobody has called about this one yet. Say “call this one” and what the listing agent says lands here, with their words.";
}

function known(v: string): string | null {
  if (!v || v === "unknown") return null;
  return v.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}
