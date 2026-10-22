"use client";

import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Drawer } from "vaul";
import { Board } from "./Board";
import { DemoControls } from "./DemoControls";
import { ListingDetail } from "./ListingDetail";
import { MapView, type Padding } from "./MapView";
import { Brand, Heard } from "./Summary";
import { postJSON, useCatalogue, useLiveSession } from "@/lib/live";
import { useMedia } from "@/lib/useMedia";

const PEEK = "196px";
const SNAPS: (string | number)[] = [PEEK, 0.56, 1];

/**
 * The whole screen. Desktop (≥1024px): the map fills the left, a 440px panel
 * on the right holds what Landline heard and the ranked places. Phones and
 * small tablets: the map fills the screen and the panel becomes a bottom sheet
 * with three stops.
 */
export function LiveBoard({ sessionId }: { sessionId: string }) {
  const { view, stale } = useLiveSession(sessionId);
  const points = useCatalogue();
  const desktop = useMedia("(min-width: 1024px)");
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [snap, setSnap] = useState<string | number | null>(PEEK);
  const [dev, setDev] = useState(false);
  const [vh, setVh] = useState(800);

  useEffect(() => {
    setDev(new URLSearchParams(window.location.search).get("dev") === "1");
    const onResize = () => setVh(window.innerHeight);
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const board = useMemo(() => view?.board ?? [], [view]);

  // Follow the server's focus (the voice agent saying "the Yorkville one") only
  // when it changes after the page opened; on open, frame the whole board.
  const openingFocus = useRef<string | null | undefined>(undefined);
  if (view && openingFocus.current === undefined) openingFocus.current = view.focus_listing_id ?? null;
  const serverFocus =
    view?.focus_listing_id && view.focus_listing_id !== openingFocus.current ? view.focus_listing_id : null;
  const focusId = selected ?? serverFocus;
  const selectedCard = board.find((c) => c.listing_id === selected);

  function select(id: string) {
    setSelected(id);
    if (!desktop && snap === PEEK) setSnap(0.56);
    void postJSON(`/api/sessions/${encodeURIComponent(sessionId)}/focus`, { listing_id: id });
  }

  const padding: Padding = desktop
    ? { top: 64, right: 64, bottom: 64, left: 64 }
    : { top: 84, right: 28, bottom: snap === PEEK ? 216 : Math.round(vh * 0.58), left: 28 };

  const map = (
    <MapView
      points={points}
      matches={view?.matches ?? []}
      board={board}
      focusId={focusId}
      hoverId={hover}
      padding={padding}
      showZoom={desktop === true}
      onSelect={select}
      onHover={setHover}
    />
  );

  const panel = (
    <AnimatePresence mode="wait" initial={false}>
      {selected ? (
        <motion.div
          key="detail"
          initial={{ opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 24 }}
          transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
        >
          <ListingDetail id={selected} card={selectedCard} onBack={() => setSelected(null)} />
        </motion.div>
      ) : (
        <motion.div
          key="list"
          initial={{ opacity: 0, x: -24 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -24 }}
          transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
          className="space-y-5 px-4 pb-8 pt-1"
        >
          <Heard view={view} />
          <Board
            board={board}
            loading={!view}
            focusId={focusId}
            hoverId={hover}
            onSelect={select}
            onHover={setHover}
          />
          {dev && view && <DemoControls sessionId={sessionId} prefs={view.preferences} />}
        </motion.div>
      )}
    </AnimatePresence>
  );

  // Until the browser says which layout this is, show the ground and nothing else.
  // The shell is `fixed inset-0`: nothing can scroll the page itself, not even
  // focus or scrollIntoView on something inside the panel.
  if (desktop === null) return <div className="fixed inset-0 bg-ground" aria-busy="true" />;

  if (desktop) {
    return (
      <div className="fixed inset-0 flex overflow-hidden">
        <main className="relative min-w-0 flex-1">{map}</main>
        <aside
          className="flex w-[440px] shrink-0 flex-col bg-ground"
          style={{ boxShadow: "-1px 0 0 var(--color-line)" }}
          aria-label="Places Landline found"
        >
          <div className="px-4 pb-4 pt-5">
            <Brand stale={stale} connected={!!view} />
            <p className="mt-0.5 text-meta text-ink-3">Toronto rentals, checked by phone</p>
          </div>
          <div className="relative min-h-0 flex-1 overflow-y-auto">{panel}</div>
        </aside>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 overflow-hidden">
      {map}
      <header
        className="pointer-events-none absolute inset-x-0 top-0 z-10 px-3"
        style={{ paddingTop: "max(12px, env(safe-area-inset-top))" }}
      >
        <div
          className="pointer-events-auto rounded-[var(--radius-card)] bg-surface/95 px-4 py-2.5 backdrop-saturate-150"
          style={{ boxShadow: "0 0 0 1px var(--color-line), 0 4px 16px rgba(26,24,21,.08)" }}
        >
          <Brand stale={stale} connected={!!view} />
        </div>
      </header>

      <Drawer.Root
        open
        modal={false}
        dismissible={false}
        snapPoints={SNAPS}
        activeSnapPoint={snap}
        setActiveSnapPoint={setSnap}
      >
        <Drawer.Portal>
          <Drawer.Content
            aria-describedby={undefined}
            className="fixed inset-x-0 bottom-0 z-20 flex h-[96dvh] flex-col rounded-t-[22px] bg-ground outline-none"
            style={{ boxShadow: "0 -1px 0 var(--color-line), 0 -8px 28px rgba(26,24,21,.10)" }}
          >
            <Drawer.Title className="sr-only">Places Landline found</Drawer.Title>
            <div aria-hidden className="mx-auto mb-2 mt-2.5 h-1 w-10 shrink-0 rounded-full bg-ink/20" />
            <div
              className="relative min-h-0 flex-1 overflow-y-auto"
              style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
            >
              {panel}
            </div>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
    </div>
  );
}
