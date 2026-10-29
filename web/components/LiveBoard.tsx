"use client";

import { AnimatePresence, motion } from "motion/react";
import dynamic from "next/dynamic";
import { notFound } from "next/navigation";
import { type CSSProperties, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Drawer } from "vaul";
import { AvatarClips } from "./AvatarClips";
import { Board } from "./Board";
import { DemoControls } from "./DemoControls";
import { Icon } from "./Icon";
import type { Mark } from "./ListingCard";
import { ListingDetail, type Reaction } from "./ListingDetail";
import type { Padding } from "./MapView";
import {
  CAPSULE_STYLE,
  PANEL,
  PANEL_STYLE,
  PEEK,
  PanelFoot,
  SHORT,
  SPLIT,
  Shell,
  panelRoom,
  sheetLook,
} from "./Shell";
import { Activity, Brand, Heard, Plan, Results, Welcome } from "./Summary";
import { type Opened, follows } from "@/lib/follow";
import { nothingHeard } from "@/lib/format";
import { type Unheard, unheard } from "@/lib/outcome";
import { useAvatarClips } from "@/lib/avatars";
import { api, postJSON, tap, useCatalogue, useLiveSession } from "@/lib/live";
import { OFFLINE } from "@/lib/pill";
import type { SessionView } from "@/lib/types";
import { useMedia } from "@/lib/useMedia";

// MapLibre is a 1 MB chunk. Loading it after the panel lets the places paint first on a slow phone;
// until then the map's own land colour holds its spot.
const MapView = dynamic(() => import("./MapView").then((m) => m.MapView), {
  ssr: false,
  loading: () => <div className="absolute inset-0 bg-map-land" aria-hidden />,
});

const SNAPS: (string | number)[] = [PEEK, 0.56, 1];

/**
 * The whole screen: the map fills it, and what's happening, what Landline heard and the ranked places float
 * over it. Desktops, tablets on their side and phones held sideways (SPLIT): a glass panel inset 16px on the
 * right (440px, narrower on smaller screens, 560px on a projector), and the map frames the board in the room
 * it leaves. Phones and tablets upright: a glass capsule for the header and a bottom sheet with three stops,
 * glass at the low and half stops and solid at full; at the lowest the renter sees the activity line, one
 * line of what was heard, and the top place.
 *
 * Judge mode reuses it: `intro` replaces the panel until a session exists,
 * `dock` (the voice controls) stays pinned to the panel, and `line` puts the
 * voice line's state in the header while it isn't live.
 */
export function LiveBoard({
  sessionId,
  intro,
  dock,
  line = null,
}: {
  sessionId: string | null;
  intro?: ReactNode;
  dock?: ReactNode;
  line?: string | null;
}) {
  const { view, stale, unreachable, missing } = useLiveSession(sessionId);
  const clips = useAvatarClips(sessionId, view?.phase ?? null);
  const points = useCatalogue();
  const desktop = useMedia(SPLIT);
  // A phone on its side gets the split layout but keeps the phone's short summaries, so a place shows.
  const short = useMedia(SHORT);
  const compact = !desktop || short === true;
  // Zoom buttons only where there's a mouse: a touch screen pinches.
  const mouse = useMedia("(hover: hover) and (pointer: fine)");
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [snap, setSnap] = useState<string | number | null>(PEEK);
  const [dev, setDev] = useState(false);
  const [vh, setVh] = useState(800);
  // The split panel's width, measured, so the map frames the board in the room the panel leaves.
  const [panelWidth, setPanelWidth] = useState(440);
  const measured = useRef<ResizeObserver | null>(null);
  const panelShown = useCallback((el: HTMLElement | null) => {
    measured.current?.disconnect();
    measured.current = null;
    if (!el) return;
    setPanelWidth(el.offsetWidth);
    measured.current = new ResizeObserver(() => setPanelWidth(el.offsetWidth));
    measured.current.observe(el);
  }, []);
  // What the renter tapped, shown at once; the server's card.reaction wins once it arrives.
  const [mine, setMine] = useState<Record<string, string>>({});
  // The server's focus and area when the page opened (below): followed once they change after that.
  const openingFocus = useRef<Opened<string | null>["current"]>(undefined);
  const openingArea = useRef<Opened<string | null>["current"]>(undefined);
  // Another session (judge mode's "Start again") opens with nothing selected or tapped, and frames its own
  // board. Reset while rendering, so not even one frame shows the last session's selection.
  const [shownSession, setShownSession] = useState(sessionId);
  if (shownSession !== sessionId) {
    setShownSession(sessionId);
    setSelected(null);
    setMine({});
    openingFocus.current = undefined;
    openingArea.current = undefined;
  }

  // Judge mode: the intro needs the whole sheet; once talking, the map gets it back.
  const hasIntro = !!intro;
  useEffect(() => {
    if (hasIntro) setSnap(sessionId ? PEEK : 1);
  }, [hasIntro, sessionId]);

  useEffect(() => {
    setDev(new URLSearchParams(window.location.search).get("dev") === "1");
    const onResize = () => setVh(window.innerHeight);
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Until the renter says something, no shortlist: the map's dots and Landline's opening sentence only.
  const blank = view !== null && nothingHeard(view);
  const board = useMemo(
    () =>
      blank
        ? []
        : (view?.board ?? []).map((c) => (!c.reaction && mine[c.listing_id] ? { ...c, reaction: mine[c.listing_id] } : c)),
    [view, mine, blank],
  );

  // Where each planned place stands until its call has a result: picked during the brief, queued
  // once the renter hangs up, then ringing. Live and finished calls speak through the card's status.
  const marks = useMemo(() => {
    const m: Record<string, Mark> = {};
    for (const id of view?.plan ?? []) m[id] = "will_call";
    for (const call of view?.calls ?? []) {
      if (call.state === "queued") m[call.listing_id] = "queued";
      else if (call.state === "dialing" || call.state === "ringing") m[call.listing_id] = "ringing";
      else delete m[call.listing_id];
    }
    return m;
  }, [view]);

  // A finished call that learned nothing, worded from how it ended ("Hung up early"), not "No answer yet".
  const ended = useMemo(() => {
    const e: Record<string, Unheard> = {};
    for (const card of board) {
      const u = unheard(card, view?.calls);
      if (u) e[card.listing_id] = u;
    }
    return e;
  }, [board, view]);

  // Follow the server's focus (the voice agent saying "the Yorkville one") once
  // it changes after the page opened; on open, frame the whole board.
  const serverFocus = view && follows(openingFocus, view.focus_listing_id ?? null) ? view.focus_listing_id : null;
  const focusId = selected ?? serverFocus ?? null;
  // The same for an area the renter names ("show me Christie Pits"): the map flies there when it changes.
  const area = view?.focus_area ?? null;
  const areaKey = area ? `${area.lat},${area.lng},${area.zoom}` : null;
  const focusArea = view && follows(openingArea, areaKey) ? area : null;
  const selectedCard = board.find((c) => c.listing_id === selected);

  // The panel's scroll: a detail opens at its top, and "← All places" puts the list back where it was.
  const scroller = useRef<HTMLDivElement>(null);
  const listTop = useRef<number | null>(null);
  const detailShown = useCallback((el: HTMLDivElement | null) => {
    if (el) scroller.current?.scrollTo({ top: 0 });
  }, []);
  const listShown = useCallback((el: HTMLDivElement | null) => {
    if (!el || listTop.current === null) return;
    scroller.current?.scrollTo({ top: listTop.current });
    listTop.current = null;
  }, []);

  function select(id: string) {
    if (!sessionId) return;
    if (selected === null) listTop.current = scroller.current?.scrollTop ?? 0;
    else scroller.current?.scrollTo({ top: 0 }); // another place's detail, from its pin
    // The detail's first photo starts loading with the tap, not after the detail's own fetch.
    const photo = board.find((c) => c.listing_id === id)?.photo;
    if (photo) new Image().src = photo;
    setSelected(id);
    // Raised even in the split layout, so a detail opened there isn't stuck in the peek if the phone turns.
    if (snap === PEEK) setSnap(0.56);
    void postJSON(`/api/sessions/${encodeURIComponent(sessionId)}/focus`, { listing_id: id });
  }

  // A tap shows at once; one the server didn't save puts the card back, so it never pretends it worked.
  const react = useCallback(
    async (id: string, reaction: Reaction, reason: string | null) => {
      if (!sessionId) return;
      const said = reason ?? (reaction === "like" ? "like it" : "not for me");
      setMine((m) => ({ ...m, [id]: said }));
      const got = await tap<SessionView>(`/api/sessions/${encodeURIComponent(sessionId)}/feedback`, {
        listing_id: id,
        reaction,
        reason: reason ?? undefined,
      });
      if (!got.ok) {
        setMine((m) => {
          const { [id]: _drop, ...rest } = m;
          return rest;
        });
      }
    },
    [sessionId],
  );

  const padding: Padding = desktop
    ? { top: 64, right: panelRoom(panelWidth), bottom: 64, left: 64 }
    : { top: 84, right: 28, bottom: snap === PEEK ? 216 : Math.round(vh * 0.58), left: 28 };

  // The map's chunk waits for the first board, so on a slow line the places and their photos don't
  // queue behind a megabyte of map. With no session to wait for (judge mode's intro), or once the API
  // has gone quiet, it loads anyway; once loaded it stays.
  const [mapOn, setMapOn] = useState(false);
  if (!mapOn && (view || !sessionId || stale)) setMapOn(true);

  const map = mapOn ? (
    // A phone turning between the sheet and the split layout starts the map afresh, so it frames the board
    // for its new shape: MapView fits before MapLibre has resized its canvas. Once it resizes first, this
    // key can go and the map (and where the renter had panned) survives the turn; the tree below is ready.
    <MapView
      key={desktop ? "split" : "sheet"}
      points={points}
      matches={view?.matches ?? points.map((p) => p.listing_id)}
      board={board}
      focusId={focusId}
      hoverId={hover}
      marks={marks}
      focusArea={focusArea}
      anchors={view?.preferences.anchors ?? []}
      padding={padding}
      showZoom={mouse === true}
      onSelect={select}
      onHover={setHover}
    />
  ) : (
    <div className="absolute inset-0 bg-map-land" aria-hidden />
  );

  // No snapshot at all for a while (the API is down or unreachable): say so plainly instead of skeletons
  // that never fill. Polling carries on, and the first answer brings the board in.
  const list = unreachable ? (
    <div className="banner" role="status">
      <Icon name="wifi-off" size={20} />
      <div>
        <p className="w6">{OFFLINE.title}</p>
        <p className="c-2">{OFFLINE.body}</p>
      </div>
    </div>
  ) : (
    <div className={desktop ? "space-y-4" : "space-y-3"}>
      <Welcome view={view} compact={compact} />
      {sessionId && (
        <AvatarClips
          clips={clips}
          videoUrl={(callId) =>
            api(`/api/sessions/${encodeURIComponent(sessionId)}/avatars/${encodeURIComponent(callId)}`)
          }
        />
      )}
      <Results view={view} compact={compact} />
      {blank ? (
        <p className="text-support text-ink">{view?.agent_says || "Tell Landline what you\u2019re looking for."}</p>
      ) : (
        <>
          <Plan view={view} />
          <Heard key={sessionId} view={view} compact={compact} wrap={!desktop && snap !== PEEK} />
          <Activity view={view} />
          <Board
            board={board}
            loading={!view}
            focusId={focusId}
            hoverId={hover}
            marks={marks}
            ended={ended}
            onSelect={select}
            onHover={setHover}
          />
        </>
      )}
      {dev && view && sessionId && (
        <DemoControls
          sessionId={sessionId}
          prefs={view.preferences}
          phase={view.phase}
          planned={view.plan.length}
          places={view.board.length}
          returning={view.returning}
          userName={view.user_name ?? null}
        />
      )}
    </div>
  );

  const panel =
    intro && !sessionId ? (
      <div className="px-4 pb-8 pt-1">{intro}</div>
    ) : (
      <AnimatePresence mode="wait" initial={false}>
        {selected ? (
          <motion.div
            key="detail"
            ref={detailShown}
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 24 }}
            transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
          >
            <ListingDetail
              id={selected}
              card={selectedCard}
              mark={marks[selected]}
              call={view?.calls.filter((c) => c.listing_id === selected).pop()}
              results={view?.phase === "done" ? view.results : null}
              onBack={() => setSelected(null)}
              onReact={(reaction, reason) => react(selected, reaction, reason)}
            />
          </motion.div>
        ) : (
          <motion.div
            key="list"
            ref={listShown}
            initial={{ opacity: 0, x: -24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -24 }}
            transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
            className="px-4 pb-8 pt-1"
          >
            {list}
          </motion.div>
        )}
      </AnimatePresence>
    );

  // A link that matches no call, found out after the page's own check: the same not-found page.
  if (missing) notFound();

  // Until the browser says which layout this is, show the shell the server sent.
  // Both are `fixed inset-0`: nothing can scroll the page itself, not even
  // focus or scrollIntoView on something inside the panel.
  if (desktop === null) return <Shell idle={!!intro && !sessionId} />;

  const idle = !!intro && !sessionId;
  const status = { view, stale, unreachable, idle, line };
  const full = snap === 1;
  const sheet = sheetLook(full);

  // One tree for both layouts, with the map in the same place in it, so a phone turning between the sheet
  // and the split layout keeps its map: the WebGL context, the tiles and where the renter had panned.
  return (
    <div className="fixed inset-0 overflow-hidden">
      <main className="absolute inset-0">
        {map}
        {/* At the sheet's top stop the capsule steps into the sheet, which covers it. */}
        {!desktop && !idle && !full && (
          <header className="cap glass" style={CAPSULE_STYLE}>
            <Brand {...status} yourCalls />
          </header>
        )}
      </main>
      {desktop ? (
        <aside ref={panelShown} className={PANEL} style={PANEL_STYLE} aria-label="Places Landline found">
          <div className="panel-head">
            <Brand {...status} />
          </div>
          <div
            ref={scroller}
            className="relative min-h-0 flex-1 overflow-y-auto"
            style={{ paddingBottom: dock && sessionId ? undefined : "env(safe-area-inset-bottom)" }}
          >
            {panel}
          </div>
          {dock && sessionId && (
            <div
              className="shrink-0 px-4 pt-3"
              style={{
                boxShadow: "0 -1px 0 var(--color-line)",
                paddingBottom: "max(12px, env(safe-area-inset-bottom))",
              }}
            >
              {dock}
            </div>
          )}
          <PanelFoot />
        </aside>
      ) : (
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
              className={`bottom-0 z-20 flex h-[96dvh] flex-col outline-none ${sheet.className}`}
              style={
                {
                  ...sheet.style,
                  // vaul starts a sheet with snap points off screen and slides it to the first stop. Starting
                  // at the stop (the offset vaul computes: the viewport less PEEK) puts the panel on screen
                  // in its first frame, so the places paint where they'll stay.
                  "--initial-transform": `calc(100dvh - ${PEEK})`,
                } as CSSProperties
              }
            >
              <Drawer.Title className="sr-only">Places Landline found</Drawer.Title>
              <span aria-hidden className="grabber" />
              {/* At the top stop the capsule steps into the sheet, so its status (the pill, the voice line)
                  and "Your calls" show here instead. */}
              {!idle && full && (
                <div className="flex shrink-0 items-center justify-between gap-2 px-4 pb-2">
                  <Brand {...status} yourCalls />
                </div>
              )}
              {dock && sessionId && <div className="shrink-0 px-4 pb-3">{dock}</div>}
              <div
                ref={scroller}
                className="relative min-h-0 flex-1 overflow-y-auto"
                style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
              >
                {panel}
              </div>
            </Drawer.Content>
          </Drawer.Portal>
        </Drawer.Root>
      )}
    </div>
  );
}
