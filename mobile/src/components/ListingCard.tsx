import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { HeardFact, MyListing } from "@/lib/api";
import { factLine, viewingLine, WENT, when } from "@/lib/heard";
import { color, radius } from "@/lib/theme";

/** One listing Landline called, as the web's /me shows it: how the call went, the viewing, what to watch out
 *  for, and each fact the agent gave, with their own words a tap away. */
export function ListingCard({ listing: x }: { listing: MyListing }) {
  return (
    <View style={styles.card}>
      <View style={styles.top}>
        <Text style={styles.address}>
          {x.address}
          {x.unit_label ? <Text style={styles.unit}>, unit {x.unit_label}</Text> : null}
        </Text>
        <Text style={styles.meta}>{WENT[x.ended] ?? x.ended}</Text>
      </View>
      <Text style={styles.meta}>Called {when(x.called_at)}</Text>
      {x.viewing ? (
        <Text style={[styles.line, { color: x.viewing.status === "declined" ? color.dead : color.real }]}>
          {viewingLine(x.viewing)}
        </Text>
      ) : null}
      {x.red_flags.map((f) => (
        <Text key={f} style={[styles.line, { color: color.dead }]}>Watch out: {f}</Text>
      ))}
      {x.facts.length ? (
        <View>
          {x.facts.map((f, n) => <FactRow key={`${f.field}-${n}`} fact={f} />)}
        </View>
      ) : null}
    </View>
  );
}

/** A heard fact. A tap shows its source: the agent's words, and when they said them. */
function FactRow({ fact: f }: { fact: HeardFact }) {
  const [open, setOpen] = useState(false);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ expanded: open }}
      accessibilityHint="Shows what the agent said"
      onPress={() => setOpen(!open)}
      style={styles.fact}
    >
      <Text style={styles.line}>
        <Text style={styles.meta}>{open ? "▾ " : "▸ "}</Text>
        {factLine(f.field, f.value)} <Text style={styles.meta}>· Agent heard</Text>
      </Text>
      {open ? <Text style={styles.quote}>“{f.quote}”{f.at ? ` · ${when(f.at)}` : ""}</Text> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: color.surface, borderRadius: radius.card, padding: 16, gap: 4,
    borderWidth: StyleSheet.hairlineWidth, borderColor: color.line,
  },
  top: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between", gap: 12 },
  address: { flexShrink: 1, fontSize: 17, fontWeight: "600", color: color.ink },
  unit: { fontWeight: "400", color: color.ink2 },
  meta: { fontSize: 13, color: color.ink3 },
  line: { fontSize: 15, lineHeight: 20, color: color.ink, marginTop: 4 },
  fact: { minHeight: 44, justifyContent: "center" },   // a thumb-sized target
  quote: { fontSize: 13, lineHeight: 18, color: color.ink2, paddingLeft: 16, paddingBottom: 8 },
});
