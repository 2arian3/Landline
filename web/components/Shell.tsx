import type { CSSProperties, ReactNode } from "react";
import { Brand } from "./Summary";

/** How much of the phone sheet shows at its lowest stop. */
export const PEEK = "196px";

/**
 * Where the split layout takes over from the bottom sheet: desktops, tablets on their side, and phones held
 * sideways. A tablet upright (768 px) gets the sheet: split, its cards were too narrow for an address. The
 * same query as the `split` variant in app/globals.css, which this shell's CSS uses.
 */
export const SPLIT = "(min-width: 1024px), (orientation: landscape) and (max-height: 520px)";

/** A short screen (a phone on its side): the `short` variant in app/globals.css. */
export const SHORT = "(max-height: 520px)";

/**
 * The split layout's panel: 440px on a desktop, narrower on a tablet or a phone on its side, and 560px from
 * 1600px (a projector), where its type also steps up a size (`.panel` in app/globals.css).
 */
export const PANEL_WIDTH = "panel w-[clamp(340px,42vw,440px)] min-[1600px]:w-[560px]";

/** The split layout's panel, clear of a phone's notch when it's held sideways. */
export const PANEL_STYLE: CSSProperties = {
  boxShadow: "-1px 0 0 var(--color-line)",
  paddingRight: "env(safe-area-inset-right)",
};

/** The split layout's panel header: the brand line, and the tagline where there's room for it. */
export function PanelHeader({ brand }: { brand: ReactNode }) {
  return (
    <div className="px-4 pb-4 pt-5 short:pb-1 short:pt-3">
      {brand}
      <p className="mt-0.5 text-meta text-ink-3 short:hidden">Toronto rentals, checked by phone</p>
    </div>
  );
}

/**
 * The first paint, sent as HTML before any script runs: the brand and the panel, with grey places where
 * the board will be, in the spots LiveBoard puts them. CSS picks the phone or desktop layout because the
 * page hasn't asked the browser yet; LiveBoard takes over in place once it has. `idle` is judge mode's
 * intro, which has no board to wait for.
 */
export function Shell({ idle = false }: { idle?: boolean }) {
  const places = !idle && (
    <div className="space-y-3 px-4 pt-1 split:space-y-4" aria-hidden>
      <div className="h-[18px] w-1/2 rounded bg-press" />
      <div className="h-4 w-2/3 rounded bg-press" />
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-[110px] rounded-[var(--radius-card)] bg-surface"
          style={{ boxShadow: "0 0 0 1px var(--color-hair)" }}
        />
      ))}
    </div>
  );

  return (
    <div className="fixed inset-0 overflow-hidden bg-map-land" aria-busy="true">
      <div className="hidden h-full split:flex">
        <div className="min-w-0 flex-1" />
        <aside className={`flex ${PANEL_WIDTH} shrink-0 flex-col bg-ground`} style={PANEL_STYLE}>
          <PanelHeader brand={<Brand stale={false} connected={false} idle={idle} />} />
          {places}
        </aside>
      </div>

      <div className="split:hidden">
        {!idle && (
          <header
            className="absolute inset-x-0 top-0 z-10 px-3"
            style={{ paddingTop: "max(12px, env(safe-area-inset-top))" }}
          >
            <div
              className="rounded-[var(--radius-card)] bg-surface/95 px-4 py-2.5"
              style={{ boxShadow: "0 0 0 1px var(--color-line), 0 4px 16px rgba(26,24,21,.08)" }}
            >
              <Brand stale={false} connected={false} idle={idle} />
            </div>
          </header>
        )}
        {/* Where LiveBoard's sheet starts: the viewport less PEEK, as vaul computes it. */}
        <div
          className="fixed inset-x-0 bottom-0 z-20 h-[96dvh] rounded-t-[22px] bg-ground"
          style={{
            boxShadow: "0 -1px 0 var(--color-line), 0 -8px 28px rgba(26,24,21,.10)",
            transform: `translate3d(0, calc(100dvh - ${PEEK}), 0)`,
          }}
        >
          <div aria-hidden className="mx-auto mb-2 mt-2.5 h-1 w-10 rounded-full bg-ink/20" />
          {places}
        </div>
      </div>
    </div>
  );
}
