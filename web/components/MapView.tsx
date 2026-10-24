"use client";

import {
  AttributionControl,
  type GeoJSONSource,
  LngLatBounds,
  Map as MapLibre,
  Marker,
  NavigationControl,
  setWorkerUrl,
} from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import type { Area, Card, CataloguePoint } from "@/lib/types";

// Copied there by `npm install` (scripts/copy-maplibre-worker.mjs); the bundler
// can't resolve MapLibre's own worker path.
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

const STYLE = "https://tiles.openfreemap.org/styles/positron";
const TORONTO: [number, number] = [-79.39, 43.69];

const INK = "#1a1815";
const LAND = "#efece6";
const WATER = "#cad7dc";
const PARK = "#dfe5d4";

export type Padding = { top: number; right: number; bottom: number; left: number };

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
  focusArea = null,
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
  focusArea?: Area | null;
  padding: Padding;
  showZoom: boolean;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibre | null>(null);
  const pins = useRef(new Map<string, { marker: Marker; el: HTMLButtonElement }>());
  const [ready, setReady] = useState(false);
  const handlers = useRef({ onSelect, onHover });
  handlers.current = { onSelect, onHover };

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
    m.addControl(new AttributionControl({ compact: true }), "bottom-left");
    map.current = m;

    m.on("load", () => {
      tuneStyle(m);
      m.addSource("catalogue", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({
        id: "catalogue",
        type: "circle",
        source: "catalogue",
        paint: {
          "circle-color": INK,
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2, 13, 3.5, 16, 5.5],
          "circle-opacity": ["case", ["get", "match"], 0.34, 0.07],
          "circle-opacity-transition": { duration: 600, delay: 0 },
          "circle-stroke-width": ["case", ["get", "match"], 0.75, 0],
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

    return () => {
      pins.current.forEach(({ marker }) => marker.remove());
      pins.current.clear();
      m.remove();
      map.current = null;
    };
  }, []);

  // ── zoom buttons only where there's a mouse ─────────────────────────────
  useEffect(() => {
    const m = map.current;
    if (!m || !showZoom) return;
    const ctl = new NavigationControl({ showCompass: false });
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
        const el = document.createElement("button");
        el.type = "button";
        el.className = "pin";
        el.addEventListener("click", (e) => {
          e.stopPropagation();
          handlers.current.onSelect(card.listing_id);
        });
        el.addEventListener("mouseenter", () => handlers.current.onHover(card.listing_id));
        el.addEventListener("mouseleave", () => handlers.current.onHover(null));
        const marker = new Marker({ element: el, anchor: "center" }).setLngLat([card.lng!, card.lat!]).addTo(m);
        pin = { marker, el };
        pins.current.set(card.listing_id, pin);
      }
      pin.el.textContent = String(card.rank);
      pin.el.dataset.status = card.status;
      pin.el.dataset.mark = marks[card.listing_id] ?? "";
      pin.el.dataset.focus = String(card.listing_id === focusId);
      pin.el.dataset.hover = String(card.listing_id === hoverId);
      pin.el.style.zIndex = card.listing_id === focusId ? "30" : String(20 - card.rank);
      pin.el.setAttribute("aria-label", `Number ${card.rank}: ${card.address}`);
    }
    for (const [id, pin] of pins.current) {
      if (!keep.has(id)) {
        pin.marker.remove();
        pins.current.delete(id);
      }
    }
  }, [ready, board, focusId, hoverId, marks]);

  // ── frame the board when its set of listings changes ────────────────────
  const boardKey = board.map((c) => c.listing_id).sort().join(",");
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const located = board.filter((c) => onMap(c.lat, c.lng));
    if (!located.length) return;
    const bounds = new LngLatBounds();
    located.forEach((c) => bounds.extend([c.lng!, c.lat!]));
    m.fitBounds(bounds, { padding, maxZoom: 14, duration: reducedMotion() ? 0 : 800 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, boardKey, padding.bottom, padding.right]);

  // ── bring the focused listing into view ─────────────────────────────────
  useEffect(() => {
    const m = map.current;
    if (!m || !ready || !focusId) return;
    const card = board.find((c) => c.listing_id === focusId);
    const pt = card ?? points.find((p) => p.listing_id === focusId);
    if (!pt || !onMap(pt.lat, pt.lng)) return;
    m.easeTo({
      center: [pt.lng!, pt.lat!],
      zoom: Math.max(m.getZoom(), 14),
      padding,
      duration: reducedMotion() ? 0 : 600,
    });
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
  return (
    <div className="absolute inset-0">
      <div ref={container} className="h-full w-full" aria-label="Map of matching rentals" />
    </div>
  );
}

function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Warm the basemap so it sits under the ink instead of competing with it. */
function tuneStyle(m: MapLibre) {
  for (const layer of m.getStyle().layers ?? []) {
    try {
      if (layer.type === "background") m.setPaintProperty(layer.id, "background-color", LAND);
      else if (layer.type === "fill" && /water/.test(layer.id)) m.setPaintProperty(layer.id, "fill-color", WATER);
      else if (layer.type === "fill" && /park|grass|wood|landcover/.test(layer.id))
        m.setPaintProperty(layer.id, "fill-color", PARK);
      else if (layer.type === "symbol" && /poi/.test(layer.id)) m.setLayoutProperty(layer.id, "visibility", "none");
    } catch {
      // a layer the style doesn't let us repaint keeps its default
    }
  }
}
