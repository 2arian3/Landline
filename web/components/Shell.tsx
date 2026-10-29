import Link from "next/link";
import type { CSSProperties } from "react";
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
 * The split layout's panel (Lucent's `.panel`): glass, floating 16px in from the top, right and bottom of a
 * full-bleed map. 440px on a desktop, narrower on a tablet or a phone on its side, and 560px from 1600px (a
 * projector), where its type also steps up a size. The `!` keeps these over `.panel`'s own fixed width.
 */
export const PANEL = "panel glass !top-4 !right-4 !bottom-4 !w-[clamp(340px,42vw,440px)] min-[1600px]:!w-[560px]";

/** Clear of a phone's notch when it's held sideways. */
export const PANEL_STYLE: CSSProperties = { marginRight: "env(safe-area-inset-right)" };

/** The room the map leaves the panel when it frames the board: its width, its 16px inset and 16px more. */
export const panelRoom = (width: number) => width + 32;

/** The phone's header: one glass capsule floating over the map, clear of the notch. */
export const CAPSULE_STYLE: CSSProperties = { top: "max(12px, env(safe-area-inset-top))" };

/** The panel's foot: the tagline, and "Your calls" (a phone has it as a button in its capsule instead). */
export function PanelFoot() {
  return (
    <div className="flex shrink-0 items-center justify-between gap-3 border-t border-line py-1.5 pl-5 pr-3">
      <p className="t-caption c-2 short:hidden">Toronto rentals, checked by phone</p>
      <Link href="/me" className="btn btn-quiet btn-small ml-auto">
        Your calls
      </Link>
    </div>
  );
}

/** The phone sheet's two looks: glass floating 8px in at its low and half stops, solid edge to edge at full. */
export function sheetLook(full: boolean): { className: string; style: CSSProperties } {
  return full
    ? { className: "bg-ground", style: { position: "fixed", left: 0, right: 0, borderRadius: 0 } }
    : { className: "glass", style: { position: "fixed", left: 8, right: 8, borderRadius: "34px 34px 0 0" } };
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
  const sheet = sheetLook(false);

  return (
    <div className="fixed inset-0 overflow-hidden bg-map-land" aria-busy="true">
      <div className="hidden split:block">
        <aside className={PANEL} style={PANEL_STYLE}>
          <div className="panel-head">
            <Brand stale={false} connected={false} idle={idle} />
          </div>
          <div className="min-h-0 flex-1">{places}</div>
          <PanelFoot />
        </aside>
      </div>

      <div className="split:hidden">
        {!idle && (
          <header className="cap glass" style={CAPSULE_STYLE}>
            <Brand stale={false} connected={false} idle={idle} yourCalls />
          </header>
        )}
        {/* Where LiveBoard's sheet starts: the viewport less PEEK, as vaul computes it. */}
        <div
          className={`bottom-0 z-20 flex h-[96dvh] flex-col ${sheet.className}`}
          style={{ ...sheet.style, transform: `translate3d(0, calc(100dvh - ${PEEK}), 0)` }}
        >
          <span aria-hidden className="grabber" />
          {places}
        </div>
      </div>
    </div>
  );
}
