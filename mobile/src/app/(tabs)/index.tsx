import { useAuth, useClerk } from "@clerk/expo";
import type { SFSymbol } from "expo-symbols";
import { Redirect, router } from "expo-router";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { ListingCard, ListingRow } from "@/components/ListingCard";
import { Button, CallLandline, Calm, ErrorLine, Glyph, LargeTitle, NoCallsYet, Screen } from "@/components/ui";
import { getMe, NO_CALLS_YET } from "@/lib/api";
import { phoneLabel } from "@/lib/heard";
import { latest, tally } from "@/lib/listings";
import { useListings } from "@/lib/listingsContext";
import { color, type } from "@/lib/theme";
import { useLoad } from "@/lib/useLoad";

/** Signed in: who Landline knows this phone as, what its calls came to, a button that calls Landline, and the
 *  latest results; a number Landline doesn't know yet gets how to start, never an error. Signed out: the
 *  sign-in screen. */
export default function Home() {
  const { isLoaded, isSignedIn } = useAuth();
  const { signOut } = useClerk();
  // Two loads, so a failed listings request still shows who the renter is.
  const who = useLoad(getMe);
  const called = useListings();   // shared with My listings

  if (!isLoaded) return <Screen><ActivityIndicator color={color.ink} /></Screen>;
  if (!isSignedIn) return <Redirect href="/sign-in" />;

  const fresh = who.newNumber || called.newNumber;
  const me = who.data;
  const errors = [...new Set([who.error, called.error])].filter((e) => e !== null);
  const again = () => {
    void who.load();
    void called.load();
  };
  const signOutButton = <Button label="Sign out" kind="quiet" onPress={() => void signOut()} style={styles.signOut} />;

  if (fresh) {
    return (
      <Screen tabs onRefresh={() => void Promise.all([who.refresh(), called.refresh()])} refreshing={who.refreshing}>
        <NoCallsYet title />
        {signOutButton}
      </Screen>
    );
  }

  const results = called.data ? latest(called.data) : [];
  return (
    <Screen
      tabs
      onRefresh={() => void Promise.all([who.refresh(), called.refresh()])}
      refreshing={who.refreshing || called.refreshing}
    >
      {me ? (
        <View>
          <LargeTitle title={me.name ? `Hi, ${me.name}` : "Hi"} sub={phoneLabel(me.phone)} />
          {/* Counted from the listings below, so the tally and the cards say the same. */}
          {called.data?.length ? (
            <View style={styles.stats}>
              {tally(called.data).map(({ kind, words }) => (
                <View key={kind} style={styles.stat}>
                  <Glyph name={STAT_ICON[kind]} size={15} />
                  <Text style={styles.statText}>{words}</Text>
                </View>
              ))}
            </View>
          ) : null}
        </View>
      ) : who.error ? null : (
        <ActivityIndicator color={color.ink} />
      )}
      {errors.map((e) => <ErrorLine key={e}>{e}</ErrorLine>)}
      {errors.length > 0 && !(who.data && called.data) ? <Button label="Try again" kind="secondary" onPress={again} /> : null}

      <CallLandline />

      {called.data ? (
        <>
          <View style={styles.sectionHead}>
            <Text accessibilityRole="header" style={styles.section}>Latest results</Text>
            {results.length ? (
              <Button label="See all" kind="quiet" onPress={() => router.navigate("/listings")} style={{ marginRight: -12 }} />
            ) : null}
          </View>
          {results.length ? (
            results.map((x, i) => (i === 0
              ? <ListingCard key={x.call_id} listing={x} called={false} />
              : <ListingRow key={x.call_id} listing={x} />))
          ) : (
            <Calm>{NO_CALLS_YET}</Calm>
          )}
        </>
      ) : null}

      {signOutButton}
    </Screen>
  );
}

const STAT_ICON: Record<"called" | "booked" | "confirmed", SFSymbol> = {
  called: "phone",
  booked: "calendar",
  confirmed: "calendar.badge.checkmark",
};

const styles = StyleSheet.create({
  stats: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 10 },
  stat: {
    flexDirection: "row", alignItems: "center", gap: 6, height: 32, paddingHorizontal: 12, borderRadius: 999,
    backgroundColor: color.tint,
  },
  statText: { ...type.caption, fontWeight: "600", color: color.ink },
  sectionHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 6 },
  section: { ...type.section, color: color.ink },
  signOut: { alignSelf: "center", marginTop: 8 },
});
