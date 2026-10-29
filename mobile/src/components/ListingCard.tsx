import type { SFSymbol } from "expo-symbols";
import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { Glyph } from "@/components/ui";
import type { HeardFact, MyListing } from "@/lib/api";
import { factLine, formatWhen, viewingStatus, WENT } from "@/lib/heard";
import { money } from "@/lib/listings";
import { color, hairline, radius, type } from "@/lib/theme";

/** How the call went, as a glyph beside its words. */
const HOW: Record<string, SFSymbol> = { talked: "phone", voicemail: "recordingtape", no_answer: "phone.down" };

/** A viewing's glyph and colour by its tone (viewingStatus, the board's statusOf): ink while it waits for the
 *  agent, green once confirmed, red if declined. */
const VIEWING_LOOK = {
  ink: { icon: "calendar", tint: color.ink },
  real: { icon: "calendar.badge.checkmark", tint: color.real },
  dead: { icon: "calendar.badge.exclamationmark", tint: color.dead },
} as const satisfies Record<string, { icon: SFSymbol; tint: string }>;

/** Each fact's glyph, by what it's about; anything else is just "heard". */
const FACT_ICON: Record<string, SFSymbol> = {
  available: "checkmark.circle", available_from: "checkmark.circle", rent: "dollarsign.circle",
  fees: "dollarsign.circle", parking: "car", parking_cost: "car", pets: "pawprint", laundry: "washer",
  utilities_included: "bolt", ac: "snowflake", light: "sun.max", lease_months: "doc.text",
};

/** One listing Landline called, as the web's /me shows it: what it costs a month, how the call went, the
 *  viewing, what to watch out for, and each fact the agent gave, with their own words a tap away (shown
 *  from the start with `quotesOpen`). `called` adds when the call was. */
export function ListingCard({ listing: x, quotesOpen = false, called = true }: {
  listing: MyListing;
  quotesOpen?: boolean;
  called?: boolean;
}) {
  return (
    <View style={[styles.card, !x.facts.length && { paddingBottom: 18 }]}>
      <View style={styles.top}>
        {x.monthly_cost != null ? (
          <Text style={styles.price}>
            {money(x.monthly_cost)} <Text style={styles.per}>/mo</Text>
          </Text>
        ) : <View />}
        <How ended={x.ended} />
      </View>
      <Text style={styles.address}>
        {x.address}
        {x.unit_label ? <Text style={styles.unit}>, unit {x.unit_label}</Text> : null}
      </Text>
      {called ? <Text style={styles.meta}>Called {formatWhen(x.called_at)}</Text> : null}
      <Lines listing={x} />
      {x.facts.length ? (
        <View style={styles.facts}>
          {x.facts.map((f, n) => <FactRow key={`${f.field}-${n}`} fact={f} startOpen={quotesOpen} first={n === 0} />)}
        </View>
      ) : null}
    </View>
  );
}

/** Home's short row for the latest results after the first: the place, its cost and when, how it went, and
 *  the viewing and anything to watch out for, so a viewing Home counts always shows which place it's at. */
export function ListingRow({ listing: x }: { listing: MyListing }) {
  return (
    <View style={styles.row}>
      <View style={styles.rowTop}>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={styles.rowAddress} numberOfLines={2}>
            {x.address}
            {x.unit_label ? <Text style={styles.unit}>, unit {x.unit_label}</Text> : null}
          </Text>
          <Text style={styles.meta}>
            {x.monthly_cost != null ? `${money(x.monthly_cost)} /mo · ` : ""}called {formatWhen(x.called_at)}
          </Text>
        </View>
        <How ended={x.ended} />
      </View>
      <Lines listing={x} />
    </View>
  );
}

/** The viewing, as the board's statusOf says it, and each red flag in full. */
function Lines({ listing: x }: { listing: MyListing }) {
  const v = x.viewing ? viewingStatus(x.viewing) : null;
  const look = v ? VIEWING_LOOK[v.tone] : null;
  return (
    <>
      {v && look ? (
        <View style={styles.status}>
          <Glyph name={look.icon} size={18} tint={look.tint} />
          <Text style={[styles.statusText, { color: look.tint }]}>
            {v.words}
            {v.sub ? <Text style={styles.statusSub}>{"\n"}{v.sub}</Text> : null}
          </Text>
        </View>
      ) : null}
      {x.red_flags.map((f) => (
        <View key={f} style={styles.status}>
          <Glyph name="exclamationmark.triangle" size={18} tint={color.dead} />
          <Text style={[styles.statusText, { color: color.dead, fontWeight: "500" }]}>Something’s off: {f}</Text>
        </View>
      ))}
    </>
  );
}

function How({ ended }: { ended: string }) {
  return (
    <View style={styles.how}>
      {HOW[ended] ? <Glyph name={HOW[ended]} size={15} tint={color.ink2} /> : null}
      <Text style={styles.howText}>{WENT[ended] ?? ended}</Text>
    </View>
  );
}

/** A heard fact. A tap shows its source: the agent's words, and when they said them. */
function FactRow({ fact: f, startOpen, first }: { fact: HeardFact; startOpen: boolean; first: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const line = factLine(f.field, f.value);
  const cut = line.indexOf(": ");
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ expanded: open }}
      accessibilityHint="Shows what the agent said"
      onPress={() => setOpen(!open)}
      style={[styles.fact, !first && styles.factRule]}
    >
      <Glyph name={FACT_ICON[f.field] ?? "ear"} size={18} tint={color.ink2} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={styles.factText}>
          {cut > 0 ? (
            <>
              <Text style={styles.bold}>{line.slice(0, cut + 1)}</Text>{line.slice(cut + 1)}
            </>
          ) : <Text style={styles.bold}>{line}</Text>}
          <Text style={styles.source}> · Agent heard</Text>
        </Text>
        {open ? (
          <View style={styles.quote}>
            <Text style={styles.quoteText}>“{f.quote}”</Text>
            {f.at ? <Text style={styles.quoteAt}>{formatWhen(f.at)}</Text> : null}
          </View>
        ) : null}
      </View>
      <View style={{ transform: [{ rotate: open ? "90deg" : "0deg" }], marginTop: 2 }}>
        <Glyph name="chevron.right" size={14} tint={color.ink2} />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: color.surface, borderRadius: radius.card, paddingTop: 18, paddingHorizontal: 20,
    paddingBottom: 6, gap: 4, ...hairline,
  },
  top: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between", gap: 12 },
  price: { ...type.price, color: color.ink },
  per: { fontSize: 13, fontWeight: "500", letterSpacing: 0, color: color.ink2 },
  how: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 0 },
  howText: { ...type.caption, color: color.ink2 },
  address: { ...type.headline, color: color.ink },
  unit: { fontWeight: "400", color: color.ink2 },
  meta: { ...type.caption, fontWeight: "400", color: color.ink2, fontVariant: ["tabular-nums"] },
  status: { flexDirection: "row", alignItems: "flex-start", gap: 7, marginTop: 6 },
  statusText: { flex: 1, ...type.callout, fontWeight: "600" },
  statusSub: { fontWeight: "400", color: color.ink2 },
  facts: { marginTop: 8, borderTopWidth: 1, borderTopColor: color.line },
  fact: { flexDirection: "row", alignItems: "flex-start", gap: 10, minHeight: 44, paddingVertical: 11 },
  factRule: { borderTopWidth: 1, borderTopColor: color.line },
  factText: { ...type.callout, color: color.ink },
  bold: { fontWeight: "600" },
  source: { ...type.caption, color: color.ink2 },
  quote: { marginTop: 6, paddingLeft: 12, borderLeftWidth: 2, borderLeftColor: color.line },
  quoteText: { ...type.callout, color: color.ink },
  quoteAt: { ...type.caption, color: color.ink2, marginTop: 2, fontVariant: ["tabular-nums"] },
  row: { gap: 2, paddingVertical: 14, paddingHorizontal: 18, borderRadius: radius.card, backgroundColor: color.surface, ...hairline },
  rowTop: { flexDirection: "row", alignItems: "center", gap: 12 },
  rowAddress: { ...type.headline, color: color.ink },
});
