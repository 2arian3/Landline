import Link from "next/link";
import { statusOf, type Status } from "@/lib/status";
import type { Card, CardViewing } from "@/lib/types";
import { Icon, Mark } from "./Icon";

/**
 * The cover page at `/`: what Landline is, and the three ways to try it. Everything it shows comes from
 * the scripted demo (server/landline/api/demo.py) and the README, so nothing here is made up: the four
 * pins are the demo's four calls as they end, and the quotes are its listing agents' words. Each status is
 * statusOf's (lib/status.ts), so the cover says it exactly as the board does.
 */

const PHONE = { label: "+1 (289) 512-2338", href: "tel:+12895122338" };
const REPO = "https://github.com/2arian3/Landline";

const STEPS = [
  {
    title: "Say what you want",
    body: "Budget, area, beds, a dog, how much light. Speak in English or your own language. The map re-ranks as you talk, and tapping a place tells Landline which one you mean.",
  },
  {
    title: "Pick places and hang up",
    body: "Landline phones every listing agent you picked at the same time. Each call opens with “I’m an AI assistant calling for a renter.” It reads numbers back and books a viewing only inside the times you gave.",
  },
  {
    title: "Get what they said",
    body: "The results come back by text and on your map. Every fact shows who said it, in their exact words.",
  },
];

/** A drawn place's status, from the server's word for it, as the board would show it. */
const drawn = (status: Card["status"], more: Partial<Pick<Card, "red_flags" | "viewing">> = {}): Status =>
  statusOf({ status, red_flags: [], ...more } as Card);

// The demo's booked viewing in the agent's words: the board shows its date and time, which a drawing can't.
const SATURDAY: CardViewing = { starts_at: "", slot_text: "Saturday at 2 pm", status: "booked" };

const RESULTS = [
  {
    rank: 1,
    status: drawn("verified"),
    heard: "Parking is $180 a month, required. The monthly cost on its card goes up by $180.",
    quote: "Parking’s a hundred and eighty a month. It comes with the unit, it’s not optional.",
    at: { x: 30, y: 28 },
  },
  {
    rank: 2,
    // The demo's red flag, as its call reports it.
    status: drawn("suspect", { red_flags: ["First and last month by e-transfer before a viewing"] }),
    heard: null,
    quote: "I’ll need first and last by e-transfer before I can show it to you.",
    at: { x: 71, y: 19 },
  },
  {
    rank: 3,
    status: drawn("booked", { viewing: SATURDAY }),
    heard: "Dogs are fine, and the viewing sits inside the renter’s Saturday afternoon.",
    quote: "Dogs are fine. There are a couple in the building already.",
    at: { x: 58, y: 45 },
  },
  {
    rank: 4,
    status: drawn("no_answer"),
    heard: "Nobody picked up. It says so, and doesn’t guess.",
    quote: null,
    at: { x: 83, y: 62 },
  },
] as const;

/** The place the cover's card opens: the booked one. */
const BOOKED = RESULTS[2];

// The call to the booked place, as its captions show it (README, "One call, start to finish").
const CALL = [
  ["Landline", "Hi, this is Landline. I’m an AI assistant calling for a renter about the place at 650 Briar Hill Avenue. Is it still available?"],
  ["Listing agent", "Yes."],
  ["Landline", "Are pets okay? My client has a dog."],
  ["Listing agent", "Dogs are fine. There are a couple in the building already."],
  ["Landline", "Could they see it Saturday afternoon?"],
  ["Listing agent", "Saturday at two works."],
  ["Landline", "Saturday at 2 pm, then. I’ll let my client know."],
] as const;

export function Cover() {
  return (
    // .proj: from 1600px (a projector) the type steps up one size, as the board's panel does.
    <div className="proj fixed inset-0 overflow-y-auto bg-ground">
      <div
        // The gutter is 16px on a phone and 24px from 640px, and never less than a notch's safe area.
        className="mx-auto max-w-6xl [--gutter:16px] sm:[--gutter:24px] min-[1600px]:max-w-[1360px]"
        style={{
          paddingTop: "max(8px, env(safe-area-inset-top))",
          paddingLeft: "max(var(--gutter), env(safe-area-inset-left))",
          paddingRight: "max(var(--gutter), env(safe-area-inset-right))",
        }}
      >
        <header className="flex min-h-16 items-center justify-between gap-3">
          <p className="wordmark">
            <Mark />
            Landline
          </p>
          <nav aria-label="Elsewhere" className="-mr-3 flex items-center">
            <Link href="/me" className="btn btn-quiet btn-small">
              Your calls
            </Link>
            <a href={REPO} className="btn btn-quiet btn-small gap-1">
              Source
              <Icon name="arrow-up-right" size={18} />
            </a>
          </nav>
        </header>

        <main>
          <Hero />
          <HowItWorks />
          <WhatComesBack />
        </main>

        <footer className="t-caption c-2 mt-20 border-t border-hairline py-8 sm:mt-28">
          <p className="max-w-2xl">
            Built by Team AAMEL (Arian Boukani, Danial Kordmodanlou, Mahyar Jaberi). About 1,080 real Toronto
            listings.
          </p>
        </footer>
      </div>
    </div>
  );
}

function Hero() {
  return (
    <section className="grid items-center gap-10 pb-6 pt-6 sm:pt-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-16 lg:pt-16">
      <div className="max-w-xl min-[1600px]:max-w-2xl">
        <p className="t-callout c-2">Toronto rentals, checked by phone</p>
        <h1 className="mt-3 text-title-1">The voice agent that works for renters, not brokerages.</h1>
        <p className="t-body c-2 mt-5">
          Call it and say what you want, in any language. A live map of Toronto listings re-ranks while you talk.
          Pick the places you like and hang up: Landline phones those listing agents in parallel, asks what listings
          never say, books a viewing and texts you what it learned.
        </p>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Link href="/try" className="btn btn-primary">
            <Icon name="audio-lines" size={20} />
            Talk in the browser
          </Link>
          <a href={PHONE.href} className="btn btn-secondary tnum">
            <Icon name="phone" size={20} />
            Call {PHONE.label}
          </a>
        </div>
        {/* A full load, not a client navigation: the board reads ?dev=1 from the address when it mounts. */}
        <a href="/map?dev=1" className="btn btn-quiet btn-small -ml-4 mt-2 gap-1 pr-2!">
          No microphone? Watch the scripted demo
          <Icon name="chevron-right" size={18} />
        </a>
        <p className="t-caption c-2 mt-1 max-w-md">
          In the browser the listing agents are simulated, so no phone rings. By phone, Landline calls only the
          team&rsquo;s own demo numbers.
        </p>
      </div>

      <DemoMap />
    </section>
  );
}

/** One of the drawn map's pins, dressed as MapView dresses the board's: the shape, the ring, the badge. */
function DrawnPin({ rank, status, x, y, focus }: { rank: number; status: Status; x: number; y: number; focus?: boolean }) {
  return (
    <span
      className={`pin absolute -translate-x-1/2 -translate-y-1/2 ${focus ? "is-focus" : ""}`}
      data-s={status.pin}
      style={{ left: `${x}%`, top: `${y}%` }}
    >
      <span className="pin-ring" />
      <span className="pin-face">
        {rank}
        {status.badge && (
          <span className="pin-badge">
            <Icon name={status.badge} />
          </span>
        )}
      </span>
    </span>
  );
}

/** A status line, as the card and the results write it. */
function StatusLine({ status }: { status: Status }) {
  return (
    <p className={`status is-${status.tone}`}>
      {status.glyph && <Icon name={status.glyph} />}
      <span>
        {status.words}
        {status.sub && <span className="sub block">{status.sub}</span>}
      </span>
    </p>
  );
}

/** The scripted demo's board once its four calls end, drawn rather than loaded: the cover stays light. */
function DemoMap() {
  return (
    <figure className="relative">
      <div
        className="relative aspect-[4/3] overflow-hidden rounded-[var(--radius-lg)] bg-map-land shadow-[var(--shadow-hairline)]"
        aria-hidden
      >
        <MapArt />
        {RESULTS.map((r) => (
          <DrawnPin key={r.rank} rank={r.rank} status={r.status} x={r.at.x} y={r.at.y} focus={r === BOOKED} />
        ))}
      </div>

      {/* Solid, like every card; it hangs over the map's edge, so it takes Lucent's lift. */}
      <div className="relative mx-3 -mt-14 rounded-[var(--radius-md)] bg-surface p-4 shadow-[var(--shadow-lift)] sm:mx-6 lg:absolute lg:bottom-0 lg:left-[-28px] lg:mx-0 lg:mt-0 lg:w-[340px] min-[1600px]:w-[400px]">
        <div className="result" data-s={BOOKED.status.pin}>
          <span className="pin-face" aria-hidden>
            {BOOKED.rank}
          </span>
          <div className="min-w-0">
            <p className="price">
              <span className="t-price-lead">$2,500</span>
              <span className="per">/mo</span>
            </p>
            <p className="addr mt-0!">650 Briar Hill Avenue</p>
            <p className="spec">999 sq ft &middot; $300 under budget</p>
          </div>
        </div>
        <div className="card-foot mt-3 pt-3">
          <StatusLine status={BOOKED.status} />
          <p className="fact">
            <Icon name="paw-print" />
            <span>
              <b>Pets:</b> Dogs are fine
            </span>
          </p>
          <p className="quote">
            &ldquo;{BOOKED.quote}&rdquo; <span className="src">Listing agent</span>
          </p>
        </div>
      </div>

      {/* Beside the card on a wide screen: the card hangs off the map's bottom left, level with this line. */}
      <figcaption className="t-caption c-2 mt-3 px-3 sm:px-6 lg:ml-[328px] lg:px-0 min-[1600px]:ml-[388px]">
        The scripted demo once its four calls end. Each pin&rsquo;s shape and badge say how its call went.
      </figcaption>
    </figure>
  );
}

/** Catalogue dots, fixed so the server and the browser draw the same ones. */
const DOTS = (() => {
  let seed = 7;
  const next = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  return Array.from({ length: 64 }, () => ({ x: 16 + next() * 568, y: 14 + next() * 300 }));
})();

/** A quiet sketch of the city: a grid of streets on Toronto's tilt, a ravine, a park and the lake. */
function MapArt() {
  const major = [-200, -40, 120, 280, 440, 600, 760];
  const minor = [-120, 40, 200, 360, 520, 680];
  return (
    <svg viewBox="0 0 600 450" preserveAspectRatio="xMidYMid slice" className="absolute inset-0 h-full w-full">
      <path d="M-20 70 C 80 110, 130 170, 190 250 S 270 360, 300 460 L 250 460 C 225 370, 160 300, 110 240 S 20 130, -20 120 Z" fill="var(--color-map-park)" />
      <rect x="400" y="250" width="90" height="56" rx="6" fill="var(--color-map-park)" />
      <g transform="rotate(-17 300 225)" stroke="#fff" strokeLinecap="round">
        {major.map((v) => (
          <line key={`h${v}`} x1="-300" y1={v} x2="900" y2={v} strokeWidth="7" />
        ))}
        {major.map((v) => (
          <line key={`v${v}`} x1={v} y1="-300" x2={v} y2="760" strokeWidth="7" />
        ))}
        {minor.map((v) => (
          <line key={`hm${v}`} x1="-300" y1={v} x2="900" y2={v} strokeWidth="3" opacity="0.8" />
        ))}
        {minor.map((v) => (
          <line key={`vm${v}`} x1={v} y1="-300" x2={v} y2="760" strokeWidth="3" opacity="0.8" />
        ))}
      </g>
      {/* The kit's catalogue dot (.cdot): ink at a third, on a hairline of white. */}
      {DOTS.map((d, i) => (
        <circle key={i} cx={d.x} cy={d.y} r="3.5" fill="rgb(25 25 24 / 0.34)" stroke="#fff" strokeWidth="0.75" />
      ))}
      <path d="M0 352 C 110 336, 200 366, 320 350 S 520 322, 600 338 L 600 460 L 0 460 Z" fill="var(--color-map-water)" />
      <path d="M250 398 C 290 386, 350 388, 392 400 C 360 408, 290 410, 250 398 Z" fill="var(--color-map-land)" />
    </svg>
  );
}

function HowItWorks() {
  return (
    <section className="mt-20 sm:mt-28" aria-labelledby="how">
      <h2 id="how" className="text-title-2">
        How it works
      </h2>
      <ol className="mt-6 grid gap-3 md:gap-4 lg:grid-cols-3">
        {STEPS.map((s, i) => (
          <li key={s.title} className="rounded-[var(--radius-md)] bg-surface p-5 shadow-[var(--shadow-hairline)]">
            <span aria-hidden className="pin-face">
              {i + 1}
            </span>
            <h3 className="t-headline mt-4">{s.title}</h3>
            <p className="t-callout c-2 mt-1.5">{s.body}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function WhatComesBack() {
  return (
    <section className="mt-20 sm:mt-28" aria-labelledby="back">
      <h2 id="back" className="text-title-2">
        What the calls find out
      </h2>
      <p className="t-body c-2 mt-2 max-w-2xl">
        Is it still available, what does parking really cost, are dogs allowed: the things listings never say. Here
        are the four calls of the scripted demo, where the listing agents follow a script.
      </p>

      <div className="mt-6 grid items-start gap-3 md:gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <ul className="rounded-[var(--radius-md)] bg-surface shadow-[var(--shadow-hairline)]">
          {RESULTS.map((r) => (
            <li key={r.rank} className="result border-t border-hairline p-4 first:border-t-0 sm:p-5" data-s={r.status.pin}>
              <span className="pin-face" aria-hidden>
                {r.rank}
              </span>
              <div className="min-w-0">
                <span className="sr">Number {r.rank}: </span>
                <StatusLine status={r.status} />
                {r.heard && <p className="t-callout mt-1">{r.heard}</p>}
                {r.quote && (
                  <p className="quote mt-2">
                    &ldquo;{r.quote}&rdquo; <span className="src">Listing agent</span>
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>

        <figure className="rounded-[var(--radius-md)] bg-surface p-4 shadow-[var(--shadow-hairline)] sm:p-5">
          <figcaption className="t-caption c-2">The call to 650 Briar Hill Avenue, from its captions</figcaption>
          <ol className="lines mt-3">
            {CALL.map(([who, text], i) => (
              <li key={i} className="line">
                <span className="who">{who}</span>
                {text}
              </li>
            ))}
          </ol>
          <a href="/map?dev=1" className="btn btn-secondary btn-small mt-4 gap-1 pr-3!">
            Run the scripted demo
            <Icon name="chevron-right" size={18} />
          </a>
        </figure>
      </div>
    </section>
  );
}
