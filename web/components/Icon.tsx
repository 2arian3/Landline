import type { ReactNode } from "react";
import type { Glyph } from "@/lib/status";

/**
 * The board's glyphs: inline SVG on Lucide's geometry (24 viewBox, 2px stroke, round caps and joins, in
 * currentColor), named as Lucide names them. The design canvas's Glyphs board, plus the ones its other
 * boards draw (zoom, offline, the pin badge's bare "!"). The `.i` classes in app/globals.css size them.
 */
const GLYPHS = {
  phone: (
    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" />
  ),
  "audio-lines": <path d="M2 10v3M6 6v11M10 3v18M14 8v7M18 5v13M22 10v3" />,
  check: <path d="M20 6 9 17l-5-5" />,
  calendar: (
    <>
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </>
  ),
  "calendar-check": (
    <>
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18M9 16l2 2 4-4" />
    </>
  ),
  "calendar-x": (
    <>
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18M14 14l-4 4M10 14l4 4" />
    </>
  ),
  "triangle-alert": (
    <>
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" />
      <path d="M12 9v4M12 17h.01" />
    </>
  ),
  voicemail: (
    <>
      <circle cx="6" cy="12" r="4" />
      <circle cx="18" cy="12" r="4" />
      <path d="M6 16h12" />
    </>
  ),
  x: <path d="M18 6 6 18M6 6l12 12" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 6v6l4 2" />
    </>
  ),
  ear: (
    <>
      <path d="M6 8.5a6.5 6.5 0 1 1 13 0c0 6-6 6-6 10a3.5 3.5 0 1 1-7 0" />
      <path d="M15 8.5a2.5 2.5 0 0 0-5 0v1a2 2 0 1 1 0 4" />
    </>
  ),
  "message-square": <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  "square-parking": (
    <>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M9 17V7h4a3 3 0 0 1 0 6H9" />
    </>
  ),
  "paw-print": (
    <>
      <circle cx="11" cy="4" r="2" />
      <circle cx="18" cy="8" r="2" />
      <circle cx="20" cy="16" r="2" />
      <path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.05Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10Z" />
    </>
  ),
  "circle-check": (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  "map-pin": (
    <>
      <path d="M20 10c0 5-5.54 10.19-7.4 11.8a1 1 0 0 1-1.2 0C9.54 20.19 4 15 4 10a8 8 0 0 1 16 0" />
      <circle cx="12" cy="10" r="3" />
    </>
  ),
  scan: <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" />,
  "chevron-left": <path d="m15 18-6-6 6-6" />,
  "chevron-right": <path d="m9 18 6-6-6-6" />,
  "chevron-down": <path d="m6 9 6 6 6-6" />,
  "arrow-up": <path d="m5 12 7-7 7 7M12 19V5" />,
  "arrow-down": <path d="M12 5v14M19 12l-7 7-7-7" />,
  "arrow-up-right": <path d="M7 7h10v10M7 17 17 7" />,
  "refresh-cw": (
    <>
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
      <path d="M8 16H3v5" />
    </>
  ),
  receipt: (
    <>
      <path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z" />
      <path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8M12 17.5v-11" />
    </>
  ),
  "washing-machine": (
    <>
      <path d="M3 6h3M17 6h.01" />
      <rect x="3" y="2" width="18" height="20" rx="2" />
      <circle cx="12" cy="13" r="5" />
      <path d="M12 18a2.5 2.5 0 0 0 0-5 2.5 2.5 0 0 1 0-5" />
    </>
  ),
  plug: <path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />,
  "message-circle": <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />,
  plus: <path d="M5 12h14M12 5v14" />,
  minus: <path d="M5 12h14" />,
  user: (
    <>
      <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </>
  ),
  "wifi-off": (
    <path d="M12 20h.01M8.5 16.43a5 5 0 0 1 7 0M2 8.82a15 15 0 0 1 4.17-2.65M10.66 5c4.01-.36 8.14.9 11.34 3.76M16.85 11.25a10 10 0 0 1 2.22 1.68M5 13a10 10 0 0 1 5.24-2.76M2 2l20 20" />
  ),
  "circle-alert": (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 8v4M12 16h.01" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4M12 8h.01" />
    </>
  ),
  /** The pin badge's bare "!", for a flagged place (the canvas's Status board). */
  exclamation: <path d="M12 6v8M12 18.5h.01" />,
} satisfies Record<string, ReactNode>;

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
      {GLYPHS[name]}
    </svg>
  );
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
