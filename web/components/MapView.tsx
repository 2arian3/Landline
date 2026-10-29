"use client";

import {
  AttributionControl,
  type GeoJSONSource,
  type IControl,
  LngLatBounds,
  Map as MapLibre,
  Marker,
  setWorkerUrl,
} from "maplibre-gl";
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { type Stage, type Status, statusOf } from "@/lib/status";
import type { Unheard } from "@/lib/outcome";
import type { Anchor, Area, CallView, Card, CataloguePoint } from "@/lib/types";
import { glyphElement, type IconName } from "./Icon";
import { Legend } from "./Legend";
import { SPLIT } from "./Shell";

// Copied there by `npm install` (scripts/copy-maplibre-worker.mjs); the bundler
// can't resolve MapLibre's own worker path.
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

const STYLE = "https://tiles.openfreemap.org/styles/positron";
const TORONTO: [number, number] = [-79.39, 43.69];

/** The map's colours, from the tokens in app/globals.css (--color-ink, --color-map-*), so the map and the
 *  kit change together. The fallbacks are the same values, for a page whose CSS hasn't loaded. */
function colours() {
  const css = getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    ink: token("--color-ink", "#191918"),
    land: token("--color-map-land", "#efece6"),
    water: token("--color-map-water", "#cad7dc"),
    park: token("--color-map-park", "#dfe5d4"),
  };
}

// Close enough to find the street, far enough that the board's neighbours stay on screen: a phone's
// narrower map needs one step further out to show the same few kilometres.
const FOCUS_ZOOM = 13;
const FOCUS_ZOOM_NARROW = 12;
const NARROW_PX = 640;
// Pins closer than this on screen get nudged apart: one round 44 px tap target, so no pin's target covers
// a neighbour's face, and wide enough for a focused face or a calling ring not to cover its neighbour's.
const PIN_GAP = 44;

export type Padding = { top: number; right: number; bottom: number; left: number };
/** One board pin: the 44px button, its rank, the badge for its status, and the status in words. */
type Pin = {
  marker: Marker;
  el: HTMLButtonElement;
  rank: Text;
  face: HTMLSpanElement;
  badge: { name: IconName; el: HTMLSpanElement } | null;
  said: HTMLSpanElement;
};
type Located = Anchor & { lat: number; lng: number };

/**
 * Greater Toronto, generously. A pin or a fit outside it is a data error (a
 * flipped longitude once sent the whole map to the Atlantic), so those
 * listings stay in the list but never move the map.
 */
const onMap = (lat?: number | null, lng?: number | null): boolean =>
  lat != null && lng != null && lat > 43.3 && lat < 44.4 && lng > -80.3 && lng < -78.6;

/**
 * The map. Every catalogue listing is a small dot; the ones that stop matching
 * fade rather than disappear, so the renter watches the city narrow while
 * they talk. The ranked board sits on top as numbered pins, one DOM node per
 * listing for the life of the session, so a re-rank renumbers instead of
 * redrawing.
 */
export function MapView({
  points,
  matches,
  board,
  focusId,
  hoverId,
  marks,
  calls = [],
  plan = [],
  ended = {},
  focusArea = null,
  anchors = [],
  padding,
  showZoom,
  onSelect,
  onHover,
}: {
  points: CataloguePoint[];
  matches: string[];
  board: Card[];
  focusId: string | null;
  hoverId: string | null;
  marks: Record<string, string>;
  /** The session's calls, plan and unheard ends, as the cards get them, so a pin and its card say one thing. */
  calls?: CallView[];
  plan?: string[];
  ended?: Record<string, Unheard>;
  focusArea?: Area | null;
  /** The places the renter named ("near King and Bay"): drawn as anchors, never as listings. */
  anchors?: Anchor[];
  padding: Padding;
  showZoom: boolean;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibre | null>(null);
  const pins = useRef(new Map<string, Pin>());
  const pinned = useRef<Card[]>([]); // the board's cards that have a pin, in rank order
  const spreadFor = useRef(""); // the pins and positions the offsets were last worked out for
  const [ready, setReady] = useState(false);
  const handlers = useRef({ onSelect, onHover });
  handlers.current = { onSelect, onHover };
  const anchorMarkers = useRef(new Map<string, Marker>());
  const legend = useRef<HTMLDivElement>(null);

  // Each card's one status (lib/status.ts): the pin's shape and badge, the legend, and what's said aloud.
  // The same inputs ListingCard gives statusOf (via Board), so the pin, the legend and the card can't disagree.
  const statuses = useMemo(
    () =>
      new Map(
        board.map((c) => [
          c.listing_id,
          statusOf(c, {
            call: calls.filter((call) => call.listing_id === c.listing_id).at(-1) ?? null,
            planned: plan.includes(c.listing_id),
            stage: (marks[c.listing_id] as Stage | undefined) ?? undefined,
            ended: ended[c.listing_id]?.reason ?? null,
          }),
        ]),
      ),
    [board, marks, calls, plan, ended],
  );
  const placed = anchors.filter((a): a is Located => onMap(a.lat, a.lng));
  const anchorKey = placed.map((a) => `${a.label}@${a.lat},${a.lng}`).join("|");

  // ── create the map once ─────────────────────────────────────────────────
  useEffect(() => {
    if (!container.current) return;
    const m = new MapLibre({
      container: container.current,
      style: STYLE,
      center: TORONTO,
      zoom: 11,
      minZoom: 9,
      maxZoom: 17,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      // Many phones are 2.6–3×; drawing at 2× fills up to ~55% fewer pixels a frame and still looks sharp.
      pixelRatio: Math.min(window.devicePixelRatio || 1, 2),
    });
    m.touchZoomRotate.disableRotation();
    // Bottom right, so the legend has the bottom left. Under the sheet layout it's the (i) alone, riding on the
    // sheet (the canvas's M flows); MapView remounts when the layout changes, so this holds for its life.
    m.addControl(new AttributionControl(split() ? {} : { compact: true }), "bottom-right");
    // MapLibre opens a compact attribution on its first load; the board keeps it to the (i) until it's tapped.
    if (!split()) {
      m.once("load", () => {
        const box = m.getContainer().querySelector(".maplibregl-ctrl-attrib");
        box?.classList.remove("maplibregl-compact-show");
        box?.removeAttribute("open");
      });
    }
    map.current = m;

    m.on("load", () => {
      const colour = colours();
      const dot = window.matchMedia("(min-width: 1600px)").matches ? 10 / 7 : 1;
      tuneStyle(m, colour);
      m.addSource("catalogue", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({
        id: "catalogue",
        type: "circle",
        source: "catalogue",
        paint: {
          "circle-color": colour.ink,
          // The canvas's .cdot: 7px at the city's scale with a 1px white rim, 10px and 1.5px on a projector.
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2.5 * dot, 13, 3.5 * dot, 16, 5 * dot],
          "circle-opacity": ["case", ["get", "match"], 0.38, 0.07],
          "circle-opacity-transition": { duration: 600, delay: 0 },
          "circle-stroke-width": ["case", ["get", "match"], dot > 1 ? 1.5 : 1, 0],
          "circle-stroke-color": "#ffffff",
        },
      });
      m.on("click", "catalogue", (e) => {
        const id = e.features?.[0]?.properties?.id as string | undefined;
        if (id) handlers.current.onSelect(id);
      });
      m.on("mouseenter", "catalogue", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "catalogue", () => (m.getCanvas().style.cursor = ""));
      setReady(true);
    });
    // Pins that touch at one zoom may not at the next: re-spread as the zoom changes, once a frame.
    let frame = 0;
    m.on("zoom", () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        spread(m, pins.current, pinned.current);
      });
    });

    return () => {
      pins.current.forEach(({ marker }) => marker.remove());
      pins.current.clear();
      anchorMarkers.current.forEach((marker) => marker.remove());
      anchorMarkers.current.clear();
      m.remove();
      map.current = null;
    };
  }, []);

  // ── zoom and fit-all buttons only where there's a mouse ─────────────────
  const fitRef = useRef<() => void>(() => {});
  useEffect(() => {
    const m = map.current;
    // The split layout only: the phone's board has no map buttons (pinch and the list do the work).
    if (!m || !showZoom || !split()) return;
    const ctl = new MapButtons(() => fitRef.current());
    m.addControl(ctl, "top-right");
    return () => {
      // On unmount the map itself may already be gone.
      if (map.current === m) m.removeControl(ctl);
    };
  }, [showZoom]);

  // ── catalogue dots: matched vs faded ────────────────────────────────────
  const matchKey = matches.join(",");
  useEffect(() => {
    const src = ready ? (map.current?.getSource("catalogue") as GeoJSONSource | undefined) : undefined;
    if (!src) return;
    const matched = new Set(matches);
    src.setData({
      type: "FeatureCollection",
      features: points.filter((p) => onMap(p.lat, p.lng)).map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lng, p.lat] },
        properties: { id: p.listing_id, match: matched.has(p.listing_id) },
      })),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, points, matchKey]);

  // ── board pins ──────────────────────────────────────────────────────────
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const keep = new Set<string>();
    for (const card of board) {
      if (!onMap(card.lat, card.lng)) continue;
      keep.add(card.listing_id);
      let pin = pins.current.get(card.listing_id);
      if (!pin) {
        pin = makePin(m, card, handlers);
        pins.current.set(card.listing_id, pin);
      } else {
        pin.marker.setLngLat([card.lng!, card.lat!]); // a geocode can refine where a place is
      }
      dressPin(pin, card, statuses.get(card.listing_id)!);
      pin.el.dataset.mark = marks[card.listing_id] ?? "";
      pin.el.classList.toggle("is-focus", card.listing_id === focusId);
      pin.el.classList.toggle("is-hover", card.listing_id === hoverId);
      pin.el.style.zIndex = card.listing_id === focusId ? "30" : String(20 - card.rank);
    }
    for (const [id, pin] of pins.current) {
      if (!keep.has(id)) {
        pin.marker.remove();
        pins.current.delete(id);
      }
    }
    pinned.current = board.filter((c) => keep.has(c.listing_id));
    // Re-spread only when the pins or where they stand changed, not on every hover or focus.
    const where = pinned.current.map((c) => `${c.listing_id}@${c.lat},${c.lng}`).join("|");
    if (where !== spreadFor.current) {
      spreadFor.current = where;
      spread(m, pins.current, pinned.current);
    }
  }, [ready, board, focusId, hoverId, marks, statuses]);

  // ── the places the renter named, as anchors ─────────────────────────────
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const keep = new Set<string>();
    for (const a of placed) {
      const key = `${a.label}@${a.lat},${a.lng}`;
      keep.add(key);
      if (!anchorMarkers.current.has(key)) anchorMarkers.current.set(key, makeAnchor(m, a));
    }
    for (const [key, marker] of anchorMarkers.current) {
      if (!keep.has(key)) {
        marker.remove();
        anchorMarkers.current.delete(key);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, anchorKey]);

  // ── frame the board when its set of listings changes ────────────────────
  const boardKey = board.map((c) => c.listing_id).sort().join(",");
  const framed = useRef<string | null>(null); // the board last framed
  const frame = (force: boolean) => {
    const m = map.current;
    if (!m || !ready) return;
    const located = board.filter((c) => onMap(c.lat, c.lng));
    if (!located.length) return;
    const places = `${boardKey}|${anchorKey}`;
    const room = `${padding.top},${padding.right},${padding.bottom},${padding.left}`;
    const key = `${places}|${room}`;
    if (!force && framed.current === key) return;
    // Only the room changed (the phone's sheet settled on another stop, the capsule or the panel moved): with a
    // place in focus, keep it in view in the new room rather than pulling back to the whole board.
    const moved = framed.current?.startsWith(`${places}|`) ?? false;
    framed.current = key;
    if (!force && moved && focusId && focusOn(m)) return;
    const bounds = new LngLatBounds();
    located.forEach((c) => bounds.extend([c.lng!, c.lat!]));
    placed.forEach((a) => bounds.extend([a.lng, a.lat])); // the place they named stays in the frame
    m.resize(); // a layout change (the panel, the sheet, a rotation) may have resized the map since its last frame
    // The legend, where it shows (split layout), takes the map's bottom left: keep the pins clear of it.
    const clear = legend.current?.offsetParent ? legend.current.offsetHeight + 16 + PIN_GAP / 2 : 0;
    m.fitBounds(bounds, {
      padding: { ...padding, bottom: Math.max(padding.bottom, clear) },
      maxZoom: 14,
      duration: reducedMotion() ? 0 : 800,
    });
  };
  fitRef.current = () => frame(true);
  useEffect(() => {
    frame(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, boardKey, anchorKey, padding.top, padding.right, padding.bottom, padding.left]);

  // ── bring the focused listing into view ─────────────────────────────────
  // Centred in the room the capsule, the sheet or the panel leave (the same padding as the fit). False when the
  // focused place has no point on the map.
  function focusOn(m: MapLibre): boolean {
    const card = board.find((c) => c.listing_id === focusId);
    const pt = card ?? points.find((p) => p.listing_id === focusId);
    if (!pt || !onMap(pt.lat, pt.lng)) return false;
    m.easeTo({
      center: [pt.lng!, pt.lat!],
      zoom: Math.max(m.getZoom(), m.getContainer().clientWidth < NARROW_PX ? FOCUS_ZOOM_NARROW : FOCUS_ZOOM),
      padding,
      duration: reducedMotion() ? 0 : 600,
    });
    return true;
  }
  useEffect(() => {
    const m = map.current;
    if (!m || !ready || !focusId) return;
    focusOn(m);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, focusId]);

  // ── fly to an area the renter named ("show me Christie Pits") ──────────
  const areaKey = focusArea ? `${focusArea.lat},${focusArea.lng},${focusArea.zoom}` : "";
  useEffect(() => {
    const m = map.current;
    if (!m || !ready || !focusArea) return;
    const to = { center: [focusArea.lng, focusArea.lat] as [number, number], zoom: focusArea.zoom, padding };
    if (reducedMotion()) m.jumpTo(to);
    else m.flyTo({ ...to, duration: 600 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, areaKey]);

  // MapLibre sets `position: relative` on its container (unlayered CSS wins over
  // Tailwind's layered utilities), so the sizing lives on a wrapper.
  // The legend names only what's on the map.
  const shown = new Set(board.filter((c) => onMap(c.lat, c.lng)).map((c) => statuses.get(c.listing_id)!.pin));
  return (
    // Where the panel floats over the map (its fit padding is the panel plus 16 + 64), the map's corners keep
    // clear of it: the map buttons and attribution sit 16px left of the glass, and the legend wraps short of it.
    <div className="absolute inset-0" style={{
        "--map-chrome-right": `${Math.max(0, padding.right - 64)}px`,
        // Under the sheet layout, its visible height (the fit padding less 20px, less the 32px the sheet's top
        // stop leaves transparent above its edge): the (i) rides 16px above the glass.
        "--map-chrome-bottom": `${Math.max(0, padding.bottom - 52)}px`,
      } as CSSProperties}>
      <div ref={container} className="h-full w-full" aria-label="Map of matching rentals" />
      {ready && <Legend ref={legend} shapes={shown} named={placed.length > 0} />}
    </div>
  );
}

/** A board pin: the 44px target, a ring and a face that carries the rank, a badge for the status. */
type Handlers = { current: { onSelect: (id: string) => void; onHover: (id: string | null) => void } };

function makePin(m: MapLibre, card: Card, handlers: Handlers): Pin {
  const el = document.createElement("button");
  el.type = "button";
  el.className = "pin";
  el.addEventListener("click", (e) => {
    e.stopPropagation();
    handlers.current.onSelect(card.listing_id);
  });
  el.addEventListener("mouseenter", () => handlers.current.onHover(card.listing_id));
  el.addEventListener("mouseleave", () => handlers.current.onHover(null));
  const ring = document.createElement("span");
  ring.className = "pin-ring";
  const face = document.createElement("span");
  face.className = "pin-face";
  const rank = document.createTextNode("");
  face.append(rank);
  // The status in words, for a screen reader: the label stays "Number 3: 88 Bloor St E" and this describes it.
  const said = document.createElement("span");
  said.className = "sr";
  said.id = `pin-${card.listing_id.replace(/[^\w-]/g, "-")}`;
  el.setAttribute("aria-describedby", said.id);
  el.append(ring, face, said);
  const marker = new Marker({ element: el, anchor: "center" }).setLngLat([card.lng!, card.lat!]).addTo(m);
  return { marker, el, rank, face, badge: null, said };
}

/** Puts a card's rank and status on its pin: the shape (data-s), the badge, the words. */
function dressPin(pin: Pin, card: Card, status: Status) {
  pin.rank.data = String(card.rank);
  pin.el.dataset.s = status.pin;
  pin.el.dataset.status = card.status; // the server's word, which the smoke check compares
  pin.el.setAttribute("aria-label", `Number ${card.rank}: ${card.address}`);
  pin.said.textContent = status.spoken;
  if (pin.badge?.name !== status.badge) {
    pin.badge?.el.remove();
    pin.badge = null;
    if (status.badge) {
      const el = document.createElement("span");
      el.className = "pin-badge";
      el.append(glyphElement(status.badge));
      pin.face.append(el);
      pin.badge = { name: status.badge, el };
    }
  }
}

/** The renter's named place: a pin-shaped dot and its name, under the board's pins, not clickable. */
function makeAnchor(m: MapLibre, a: Located): Marker {
  const el = document.createElement("div");
  el.className = "anchor";
  el.setAttribute("role", "img");
  el.setAttribute("aria-label", `The place you named: ${a.label}`);
  const dot = document.createElement("span");
  dot.className = "anchor-dot";
  dot.append(glyphElement("map-pin"));
  const label = document.createElement("span");
  label.className = "anchor-label glass-thin";
  label.textContent = a.label;
  el.append(dot, label);
  el.style.zIndex = "5"; // under every board pin (20 − rank)
  // The dot (28px) sits on the point; the name runs to its right.
  return new Marker({ element: el, anchor: "left", offset: [-14, 0] }).setLngLat([a.lng, a.lat]).addTo(m);
}

/**
 * Nudges pins that would overlap on screen. Cards come in rank order, and a
 * higher rank keeps its true spot. A lower one that would touch a placed pin
 * takes the first free spot on rings around its own true spot (one, two, then
 * three gaps out, starting down and right), so a crowd of pins at one address
 * stays gathered around it. The offset is in pixels, so it's redone as the
 * zoom changes.
 */
function spread(m: MapLibre, pins: Map<string, Pin>, cards: Card[]) {
  const placed: { x: number; y: number }[] = [];
  const free = (x: number, y: number) => placed.every((q) => Math.hypot(x - q.x, y - q.y) >= PIN_GAP);
  for (const card of cards) {
    const pin = pins.get(card.listing_id);
    if (!pin) continue;
    const at = m.project(pin.marker.getLngLat());
    let spot = { x: at.x, y: at.y };
    search: for (let ring = 1; !free(spot.x, spot.y) && ring <= 3; ring++) {
      for (let k = 0; k < 8 * ring; k++) {
        const a = Math.PI / 4 + (2 * Math.PI * k) / (8 * ring);
        const x = at.x + Math.cos(a) * PIN_GAP * ring;
        const y = at.y + Math.sin(a) * PIN_GAP * ring;
        if (free(x, y)) {
          spot = { x, y };
          break search;
        }
      }
    }
    placed.push(spot);
    pin.marker.setOffset([spot.x - at.x, spot.y - at.y]);
  }
}

const split = () => typeof window !== "undefined" && window.matchMedia(SPLIT).matches;

function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Warm the basemap so it sits under the ink instead of competing with it. */
function tuneStyle(m: MapLibre, colour: ReturnType<typeof colours>) {
  for (const layer of m.getStyle().layers ?? []) {
    try {
      if (layer.type === "background") m.setPaintProperty(layer.id, "background-color", colour.land);
      else if (layer.type === "fill" && /water/.test(layer.id)) m.setPaintProperty(layer.id, "fill-color", colour.water);
      else if (layer.type === "fill" && /park|grass|wood|landcover/.test(layer.id))
        m.setPaintProperty(layer.id, "fill-color", colour.park);
      else if (layer.type === "symbol" && /poi/.test(layer.id)) m.setLayoutProperty(layer.id, "visibility", "none");
    } catch {
      // a layer the style doesn't let us repaint keeps its default
    }
  }
}

/** Zoom in, zoom out and fit the board: one glass group of 44px buttons (the canvas's map controls). */
class MapButtons implements IControl {
  private box: HTMLDivElement | null = null;
  constructor(private readonly fit: () => void) {}

  onAdd(m: MapLibre): HTMLElement {
    const box = document.createElement("div");
    box.className = "maplibregl-ctrl mapctl glass-clear";
    const button = (glyph: IconName, label: string, act: () => void) => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("aria-label", label);
      b.title = label;
      b.append(glyphElement(glyph));
      b.addEventListener("click", act);
      box.append(b);
    };
    button("plus", "Zoom in", () => m.zoomIn({ animate: !reducedMotion() }));
    button("minus", "Zoom out", () => m.zoomOut({ animate: !reducedMotion() }));
    button("scan", "Show every place", this.fit);
    this.box = box;
    return box;
  }

  onRemove(): void {
    this.box?.remove();
    this.box = null;
  }
}
