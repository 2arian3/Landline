import type { Glyph } from "@/lib/status";

/**
 * The board's glyphs: inline SVG on Lucide's geometry (24 viewBox, 2px stroke, round caps and joins, in
 * currentColor), named as Lucide names them. The design canvas's Glyphs board, plus the ones its other
 * boards draw (zoom, offline, the pin badge's bare "!"). The `.i` classes in app/globals.css size them.
 * Kept as data, so React (Icon) and the map's DOM pins (glyphElement) draw the same shapes.
 */
type Part =
  | readonly ["path", string]
  | readonly ["circle", number, number, number]
  | readonly ["rect", number, number, number, number, number];

const GLYPHS = {
  phone: [["path", "M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"]],
  "audio-lines": [["path", "M2 10v3M6 6v11M10 3v18M14 8v7M18 5v13M22 10v3"]],
  check: [["path", "M20 6 9 17l-5-5"]],
  calendar: [["rect", 3, 4, 18, 18, 2], ["path", "M16 2v4M8 2v4M3 10h18"]],
  "calendar-check": [["rect", 3, 4, 18, 18, 2], ["path", "M16 2v4M8 2v4M3 10h18M9 16l2 2 4-4"]],
  "calendar-x": [["rect", 3, 4, 18, 18, 2], ["path", "M16 2v4M8 2v4M3 10h18M14 14l-4 4M10 14l4 4"]],
  "triangle-alert": [["path", "m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"], ["path", "M12 9v4M12 17h.01"]],
  voicemail: [["circle", 6, 12, 4], ["circle", 18, 12, 4], ["path", "M6 16h12"]],
  x: [["path", "M18 6 6 18M6 6l12 12"]],
  clock: [["circle", 12, 12, 10], ["path", "M12 6v6l4 2"]],
  ear: [["path", "M6 8.5a6.5 6.5 0 1 1 13 0c0 6-6 6-6 10a3.5 3.5 0 1 1-7 0"], ["path", "M15 8.5a2.5 2.5 0 0 0-5 0v1a2 2 0 1 1 0 4"]],
  "message-square": [["path", "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"]],
  "square-parking": [["rect", 3, 3, 18, 18, 2], ["path", "M9 17V7h4a3 3 0 0 1 0 6H9"]],
  "paw-print": [["circle", 11, 4, 2], ["circle", 18, 8, 2], ["circle", 20, 16, 2], ["path", "M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.05Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10Z"]],
  "circle-check": [["circle", 12, 12, 10], ["path", "m9 12 2 2 4-4"]],
  "map-pin": [["path", "M20 10c0 5-5.54 10.19-7.4 11.8a1 1 0 0 1-1.2 0C9.54 20.19 4 15 4 10a8 8 0 0 1 16 0"], ["circle", 12, 10, 3]],
  scan: [["path", "M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"]],
  "chevron-left": [["path", "m15 18-6-6 6-6"]],
  "chevron-right": [["path", "m9 18 6-6-6-6"]],
  "chevron-down": [["path", "m6 9 6 6 6-6"]],
  "arrow-up": [["path", "m5 12 7-7 7 7M12 19V5"]],
  "arrow-down": [["path", "M12 5v14M19 12l-7 7-7-7"]],
  "arrow-up-right": [["path", "M7 7h10v10M7 17 17 7"]],
  "refresh-cw": [["path", "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"], ["path", "M21 3v5h-5"], ["path", "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"], ["path", "M8 16H3v5"]],
  receipt: [["path", "M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"], ["path", "M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8M12 17.5v-11"]],
  "washing-machine": [["path", "M3 6h3M17 6h.01"], ["rect", 3, 2, 18, 20, 2], ["circle", 12, 13, 5], ["path", "M12 18a2.5 2.5 0 0 0 0-5 2.5 2.5 0 0 1 0-5"]],
  plug: [["path", "M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"]],
  "message-circle": [["path", "M7.9 20A9 9 0 1 0 4 16.1L2 22Z"]],
  plus: [["path", "M5 12h14M12 5v14"]],
  minus: [["path", "M5 12h14"]],
  user: [["path", "M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"], ["circle", 12, 7, 4]],
  "wifi-off": [["path", "M12 20h.01M8.5 16.43a5 5 0 0 1 7 0M2 8.82a15 15 0 0 1 4.17-2.65M10.66 5c4.01-.36 8.14.9 11.34 3.76M16.85 11.25a10 10 0 0 1 2.22 1.68M5 13a10 10 0 0 1 5.24-2.76M2 2l20 20"]],
  "circle-alert": [["circle", 12, 12, 10], ["path", "M12 8v4M12 16h.01"]],
  info: [["circle", 12, 12, 10], ["path", "M12 16v-4M12 8h.01"]],
  /** The pin badge's bare "!", for a flagged place (the canvas's Status board). */
  exclamation: [["path", "M12 6v8M12 18.5h.01"]],
} satisfies Record<string, readonly Part[]>;

export type IconName = keyof typeof GLYPHS;

// Every glyph statusOf() names (lib/status.ts) is drawn here: this stops compiling if one isn't.
type Assert<T extends true> = T;
export type StatusGlyphsDrawn = Assert<Glyph extends IconName ? true : false>;

const SIZE = { 16: "i i-16", 18: "i i-18", 20: "i i-20", 24: "i" } as const;

/**
 * One glyph. Decorative by default (aria-hidden), because the words beside it carry the meaning; give it a
 * `label` when it stands alone, and it's announced as an image with that name.
 */
export function Icon({
  name,
  size = 24,
  label,
  className,
}: {
  name: IconName;
  size?: keyof typeof SIZE;
  label?: string;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className ? `${SIZE[size]} ${className}` : SIZE[size]}
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true, focusable: false })}
    >
      {(GLYPHS[name] as readonly Part[]).map((part, i) =>
        part[0] === "path" ? (
          <path key={i} d={part[1]} />
        ) : part[0] === "circle" ? (
          <circle key={i} cx={part[1]} cy={part[2]} r={part[3]} />
        ) : (
          <rect key={i} x={part[1]} y={part[2]} width={part[3]} height={part[4]} rx={part[5]} />
        ),
      )}
    </svg>
  );
}

const SVG_NS = "http://www.w3.org/2000/svg";

/** The same glyph as a DOM element, decorative, for markup built outside React (MapView's pins). */
export function glyphElement(name: IconName, className = "i"): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  for (const part of GLYPHS[name] as readonly Part[]) {
    const el = document.createElementNS(SVG_NS, part[0]);
    const attrs: Record<string, string | number> =
      part[0] === "path"
        ? { d: part[1] }
        : part[0] === "circle"
          ? { cx: part[1], cy: part[2], r: part[3] }
          : { x: part[1], y: part[2], width: part[3], height: part[4], rx: part[5] };
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
    svg.append(el);
  }
  return svg;
}

/** Landline's mark: the favicon's pin in an ink squircle (`.mark` in app/globals.css). */
export function Mark() {
  return (
    <span className="mark" aria-hidden>
      <svg viewBox="0 0 32 32">
        <path
          fillRule="evenodd"
          d="M16 5.5c-4.1 0-7.5 3.3-7.5 7.4 0 5.3 7.5 13.6 7.5 13.6s7.5-8.3 7.5-13.6c0-4.1-3.4-7.4-7.5-7.4zm0 10.2a2.8 2.8 0 1 1 0-5.6 2.8 2.8 0 0 1 0 5.6z"
        />
      </svg>
    </span>
  );
}
