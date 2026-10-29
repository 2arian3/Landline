// Lucent, as the web has it (web/app/globals.css, the design canvas "Landline demo polish"), so the app looks
// like the same product: warm-neutral ink on a near-white ground, the ink as the one accent, and the status
// hues for a call's state and nothing else.
export const color = {
  ground: "#fbfbfa",
  surface: "#ffffff",
  raised: "#f7f6f3",                // blocks inside a card
  ink: "#191918",                   // 17.6:1 on surface
  ink2: "#6b6a66",                  // Lucent's ink-secondary: meta, sources, hints · 5.4:1
  line: "rgba(55, 53, 47, 0.12)",   // hairlines
  tint: "rgba(55, 53, 47, 0.08)",   // selected chips, stat pills, the active tab
  off: "rgba(55, 53, 47, 0.2)",     // a switch that's off
  real: "#216a49",                  // checked by phone, confirmed
  dead: "#af2b25",                  // flagged, declined, an error
};

/** Lucent's corners: 12 small blocks, 20 cards, 28 a large block, and pills for buttons, inputs and chips. */
export const radius = { control: 12, card: 20, block: 28, pill: 999 };

/** Lucent's type ramp on a phone (the canvas's app boards). Nothing under 13 pt. */
export const type = {
  largeTitle: { fontSize: 34, lineHeight: 41, fontWeight: "700", letterSpacing: -0.75 },
  section: { fontSize: 20, lineHeight: 25, fontWeight: "600", letterSpacing: -0.24 },
  price: { fontSize: 22, lineHeight: 28, fontWeight: "600", letterSpacing: -0.33, fontVariant: ["tabular-nums"] },
  headline: { fontSize: 17, lineHeight: 22, fontWeight: "600", letterSpacing: -0.17 },
  body: { fontSize: 17, lineHeight: 24, fontWeight: "400", letterSpacing: -0.17 },
  callout: { fontSize: 15, lineHeight: 21, fontWeight: "400" },
  caption: { fontSize: 13, lineHeight: 18, fontWeight: "500" },
} as const;

/** A card at rest: Lucent's hairline. */
export const hairline = { borderWidth: 1, borderColor: color.line } as const;

/** Under the floating tab bar: how far a tab's content scrolls past the bottom so its last line clears the bar. */
export const TAB_ROOM = 112;
