import { Brand } from "./Summary";

/** How much of the phone sheet shows at its lowest stop. */
export const PEEK = "196px";

/**
 * The first paint, sent as HTML before any script runs: the brand and the panel, with grey places where
 * the board will be, in the spots LiveBoard puts them. CSS picks the phone or desktop layout because the
 * page hasn't asked the browser yet; LiveBoard takes over in place once it has. `idle` is judge mode's
 * intro, which has no board to wait for.
 */
export function Shell({ idle = false }: { idle?: boolean }) {
  const places = !idle && (
    <div className="space-y-3 px-4 pt-1 lg:space-y-4" aria-hidden>
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
      <div className="hidden h-full lg:flex">
        <div className="min-w-0 flex-1" />
        <aside
          className="flex w-[440px] shrink-0 flex-col bg-ground"
          style={{ boxShadow: "-1px 0 0 var(--color-line)" }}
        >
          <div className="px-4 pb-4 pt-5">
            <Brand stale={false} connected={false} idle={idle} />
            <p className="mt-0.5 text-meta text-ink-3">Toronto rentals, checked by phone</p>
          </div>
          {places}
        </aside>
      </div>

      <div className="lg:hidden">
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
