import Link from "next/link";

/**
 * The cover page at `/`: what Landline is, and the three ways to try it. Everything it shows comes from
 * the scripted demo (server/landline/api/demo.py) and the README, so nothing here is made up: the four
 * pins are the demo's four calls as they end, and the quotes are its listing agents' words.
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

// Status words as the cards say them (STATUS in ListingCard.tsx).
const RESULTS = [
  {
    rank: 1,
    status: "verified",
    word: "Checked by phone",
    color: "var(--color-real)",
    heard: "Parking is $180 a month, required. The monthly cost on its card goes up by $180.",
    quote: "Parking’s a hundred and eighty a month. It comes with the unit, it’s not optional.",
  },
  {
    rank: 2,
    status: "suspect",
    word: "Something’s off",
    color: "var(--color-dead)",
    heard: "Wants a deposit by e-transfer before anyone sees the unit.",
    quote: "I’ll need first and last by e-transfer before I can show it to you.",
  },
  {
    rank: 3,
    status: "booked",
    word: "Viewing booked",
    color: "var(--color-real)",
    heard: "Dogs are fine. A viewing on Saturday at 2 pm, inside the renter’s afternoon.",
    quote: "Dogs are fine. There are a couple in the building already.",
  },
  {
    rank: 4,
    status: "no_answer",
    word: "No answer yet",
    color: "var(--color-ink-3)",
    heard: "Nobody picked up. It says so, and doesn’t guess.",
    quote: null,
  },
] as const;

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

const hairline = { boxShadow: "0 0 0 1px var(--color-line)" };
const floating = { boxShadow: "0 0 0 1px var(--color-line), 0 8px 28px rgba(26,24,21,.10)" };

export function Cover() {
  return (
    <div className="fixed inset-0 overflow-y-auto bg-ground">
      <div
        className="mx-auto max-w-6xl px-4 sm:px-6"
        style={{
          paddingTop: "max(12px, env(safe-area-inset-top))",
          paddingLeft: "max(16px, env(safe-area-inset-left))",
          paddingRight: "max(16px, env(safe-area-inset-right))",
        }}
      >
        <header className="flex min-h-14 items-center justify-between gap-3">
          <p className="text-fact font-strong tracking-[-0.01em]">Landline</p>
          <nav className="flex items-center gap-1 text-support text-ink-2">
            <Link href="/me" className="inline-flex min-h-11 items-center rounded-[var(--radius-control)] px-2 hover:bg-press">
              Your calls
            </Link>
            <a href={REPO} className="inline-flex min-h-11 items-center rounded-[var(--radius-control)] px-2 hover:bg-press">
              Source
            </a>
          </nav>
        </header>

        <main>
          <Hero />
          <HowItWorks />
          <WhatComesBack />
        </main>

        <footer className="mt-20 border-t border-line py-8 text-meta text-ink-3 sm:mt-28">
          <p className="max-w-2xl">
            Built on Boson AI&rsquo;s Higgs Realtime, on the renter&rsquo;s call and every listing call, by Team AAMEL
            (Arian Boukani, Danial Kordmodanlou, Mahyar Jaberi). About
            1,080 real Toronto listings.
          </p>
        </footer>
      </div>
    </div>
  );
}

function Hero() {
  return (
    <section className="grid items-center gap-12 pb-6 pt-8 sm:pt-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-16 lg:pt-20">
      <div className="max-w-xl">
        <p className="text-support text-ink-3">Toronto rentals, checked by phone</p>
        <h1 className="mt-3 text-[40px] font-strong leading-[1.04] tracking-[-0.03em] sm:text-[56px] lg:text-[64px]">
          The voice agent that works for renters, not brokerages.
        </h1>
        <p className="mt-5 text-fact text-ink-2 sm:text-[19px] sm:leading-[27px]">
          Call it and say what you want, in any language. A live map of Toronto listings re-ranks while you talk.
          Pick the places you like and hang up: Landline phones those listing agents in parallel, asks what listings
          never say, books a viewing and texts you what it learned.
        </p>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Link
            href="/try"
            className="inline-flex h-12 items-center justify-center rounded-[var(--radius-control)] bg-ink px-5 text-support font-label text-white"
          >
            Talk in the browser
          </Link>
          <a
            href={PHONE.href}
            className="tnum inline-flex h-12 items-center justify-center rounded-[var(--radius-control)] bg-surface px-5 text-support font-label text-ink hover:bg-press"
            style={hairline}
          >
            Call {PHONE.label}
          </a>
        </div>
        {/* A full load, not a client navigation: the board reads ?dev=1 from the address when it mounts. */}
        <a
          href="/map?dev=1"
          className="mt-3 inline-flex min-h-11 items-center text-support text-ink-2 underline underline-offset-2"
        >
          No microphone? Watch the scripted demo
        </a>
        <p className="mt-2 max-w-md text-meta text-ink-3">
          In the browser the listing agents are simulated, so no phone rings. By phone, Landline calls only the
          team&rsquo;s own demo numbers.
        </p>
      </div>

      <DemoMap />
    </section>
  );
}

/** The scripted demo's board once its four calls end, drawn rather than loaded: the cover stays light. */
function DemoMap() {
  return (
    <figure className="relative">
      <div className="relative aspect-[4/3] overflow-hidden rounded-[22px] bg-map-land" style={hairline} aria-hidden>
        <MapArt />
        {PINS.map((p) => (
          <span
            key={p.rank}
            className="pin pointer-events-none absolute -translate-x-1/2 -translate-y-1/2"
            data-status={p.status}
            data-focus={p.rank === 3 ? "true" : undefined}
            style={{ left: `${p.x}%`, top: `${p.y}%` }}
          >
            <span className="pin-face">{p.rank}</span>
          </span>
        ))}
      </div>

      <div
        className="relative mx-3 -mt-14 rounded-[var(--radius-card)] bg-surface p-4 sm:mx-6 lg:absolute lg:bottom-0 lg:left-[-28px] lg:mx-0 lg:mt-0 lg:w-[330px]"
        style={floating}
      >
        <p className="text-fact font-label">650 Briar Hill Avenue</p>
        <p className="tnum mt-2 text-price font-strong">
          $2,500<span className="text-support font-normal text-ink-3"> /mo</span>
        </p>
        <p className="mt-1 text-support text-ink-2">999 sq ft &middot; $300 under budget</p>
        <p className="mt-2 flex items-center gap-1.5 text-support font-label" style={{ color: "var(--color-real)" }}>
          <span aria-hidden>&#9733;</span> Viewing booked &middot; Saturday at 2 pm
        </p>
        <div className="mt-3 border-t border-hair pt-3">
          <p className="text-support text-ink">
            <span className="text-ink-3">Pets</span> Dogs are fine
          </p>
          <p className="mt-1 text-meta text-ink-3">
            Agent heard: &ldquo;Dogs are fine. There are a couple in the building already.&rdquo;
          </p>
        </div>
      </div>

      {/* Beside the card on a wide screen: the card hangs off the map's bottom left, level with this line. */}
      <figcaption className="mt-3 px-3 text-meta text-ink-3 sm:px-6 lg:ml-[318px] lg:px-0">
        The scripted demo once its four calls end. The pin colour and shape say how each call went.
      </figcaption>
    </figure>
  );
}

const PINS = [
  { rank: 1, status: "verified", x: 30, y: 28 },
  { rank: 2, status: "suspect", x: 71, y: 19 },
  { rank: 3, status: "booked", x: 58, y: 45 },
  { rank: 4, status: "no_answer", x: 83, y: 62 },
] as const;

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
      {DOTS.map((d, i) => (
        <circle key={i} cx={d.x} cy={d.y} r="3.5" fill="var(--color-ink-3)" opacity="0.32" />
      ))}
      <path d="M0 352 C 110 336, 200 366, 320 350 S 520 322, 600 338 L 600 460 L 0 460 Z" fill="var(--color-map-water)" />
      <path d="M250 398 C 290 386, 350 388, 392 400 C 360 408, 290 410, 250 398 Z" fill="var(--color-map-land)" />
    </svg>
  );
}

function HowItWorks() {
  return (
    <section className="mt-20 sm:mt-28" aria-labelledby="how">
      <h2 id="how" className="text-title font-strong">
        How it works
      </h2>
      <ol className="mt-6 grid gap-4 md:grid-cols-3">
        {STEPS.map((s, i) => (
          <li key={s.title} className="rounded-[var(--radius-card)] bg-surface p-5" style={hairline}>
            <span
              aria-hidden
              className="tnum grid h-8 w-8 place-items-center rounded-full bg-ink text-meta font-strong text-white"
            >
              {i + 1}
            </span>
            <h3 className="mt-4 text-fact font-strong">{s.title}</h3>
            <p className="mt-2 text-support text-ink-2">{s.body}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function WhatComesBack() {
  return (
    <section className="mt-20 sm:mt-28" aria-labelledby="back">
      <h2 id="back" className="text-title font-strong">
        What the calls find out
      </h2>
      <p className="mt-2 max-w-2xl text-support text-ink-2">
        Is it still available, what does parking really cost, are dogs allowed: the things listings never say. Here
        are the four calls of the scripted demo, where the listing agents follow a script.
      </p>

      <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <ul className="rounded-[var(--radius-card)] bg-surface" style={hairline}>
          {RESULTS.map((r) => (
            <li key={r.rank} className="flex gap-3 border-t border-hair p-4 first:border-t-0 sm:p-5">
              <span className="pin pointer-events-none -m-1.5 shrink-0" data-status={r.status} aria-hidden>
                <span className="pin-face">{r.rank}</span>
              </span>
              <div className="min-w-0">
                <p className="text-support font-label" style={{ color: r.color }}>
                  {r.word}
                </p>
                <p className="mt-0.5 text-support text-ink">{r.heard}</p>
                {r.quote && <p className="mt-1 text-meta text-ink-3">Agent heard: &ldquo;{r.quote}&rdquo;</p>}
              </div>
            </li>
          ))}
        </ul>

        <figure className="rounded-[var(--radius-card)] bg-surface p-5" style={hairline}>
          <figcaption className="text-meta text-ink-3">The call to 650 Briar Hill Avenue, from its captions</figcaption>
          <ol className="mt-3 space-y-2.5">
            {CALL.map(([who, text], i) => (
              <li key={i} className="text-support text-ink">
                <span className="font-label text-ink-3">{who}</span> {text}
              </li>
            ))}
          </ol>
          <a
            href="/map?dev=1"
            className="mt-4 inline-flex min-h-11 items-center text-support text-ink-2 underline underline-offset-2"
          >
            Run the scripted demo
          </a>
        </figure>
      </div>
    </section>
  );
}
