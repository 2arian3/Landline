import { useAuth, useClerk } from "@clerk/expo";
import { Redirect, router } from "expo-router";
import { useState } from "react";
import { ActivityIndicator, Linking, StyleSheet, Text, View } from "react-native";

import { ListingCard } from "@/components/ListingCard";
import { Button, ErrorLine, Screen } from "@/components/ui";
import { getListings, getMe } from "@/lib/api";
import { LANDLINE_PHONE } from "@/lib/config";
import { phoneLabel } from "@/lib/heard";
import { latest } from "@/lib/listings";
import { color, radius } from "@/lib/theme";
import { useLoad } from "@/lib/useLoad";

/** Signed in: who Landline knows this phone as, a button that calls Landline, and the latest results.
 *  Signed out: the sign-in screen. */
export default function Home() {
  const { isLoaded, isSignedIn } = useAuth();
  const { signOut } = useClerk();
  // Two loads, so a failed listings request still shows who the renter is.
  const who = useLoad(getMe);
  const called = useLoad(getListings);
  const [callError, setCallError] = useState<string | null>(null);

  if (!isLoaded) return <Screen><ActivityIndicator color={color.ink} /></Screen>;
  if (!isSignedIn) return <Redirect href="/sign-in" />;

  // The phone's own dialer places the call; a phone that can't (a tablet, a simulator) gets the number to dial.
  const call = () => {
    setCallError(null);
    Linking.openURL(`tel:${LANDLINE_PHONE}`).catch(() =>
      setCallError(`This phone can't place calls. Call ${phoneLabel(LANDLINE_PHONE)} from one that can.`));
  };

  const me = who.data;
  const errors = [...new Set([who.error, called.error])].filter((e) => e !== null);
  const again = () => {
    void who.load();
    void called.load();
  };
  return (
    <Screen
      onRefresh={() => void Promise.all([who.refresh(), called.refresh()])}
      refreshing={who.refreshing || called.refreshing}
    >
      <Text style={styles.brand}>Landline</Text>
      {me ? (
        <View style={styles.card}>
          <Text style={styles.hello}>Hi, {me.name ?? phoneLabel(me.phone)}</Text>
          {me.name ? <Text style={styles.meta}>{phoneLabel(me.phone)}</Text> : null}
          <Text style={styles.meta}>
            {me.calls} {me.calls === 1 ? "place" : "places"} called · {me.booked} {me.booked === 1 ? "viewing" : "viewings"} booked
          </Text>
        </View>
      ) : who.error ? null : (
        <ActivityIndicator color={color.ink} />
      )}
      {errors.map((e) => <ErrorLine key={e}>{e}</ErrorLine>)}
      {errors.length > 0 && !(who.data && called.data) ? <Button label="Try again" onPress={again} quiet /> : null}

      <View style={styles.call}>
        <Button label="Call Landline" onPress={call} />
        <Text style={styles.meta}>
          {phoneLabel(LANDLINE_PHONE)}. Say what you're looking for, and Landline calls the places for you.
        </Text>
        {callError ? <ErrorLine>{callError}</ErrorLine> : null}
      </View>

      {called.data ? (
        <>
          <Text style={styles.heading}>Latest results</Text>
          {called.data.length ? (
            <>
              {latest(called.data).map((x) => <ListingCard key={x.call_id} listing={x} />)}
              <Button label="See all my listings" onPress={() => router.push("/listings")} quiet />
            </>
          ) : (
            <Text style={styles.empty}>Landline hasn't called any places for you yet.</Text>
          )}
        </>
      ) : null}

      <Button label="My search" onPress={() => router.push("/search")} quiet />
      <View style={styles.spacer} />
      <Button label="Sign out" onPress={() => void signOut()} quiet />
    </Screen>
  );
}

const styles = StyleSheet.create({
  brand: { fontSize: 17, fontWeight: "600", color: color.ink },
  card: {
    backgroundColor: color.surface, borderRadius: radius.card, padding: 16, gap: 4,
    borderWidth: StyleSheet.hairlineWidth, borderColor: color.line,
  },
  hello: { fontSize: 28, fontWeight: "600", color: color.ink },
  meta: { fontSize: 15, color: color.ink3 },
  call: { gap: 8 },
  heading: { fontSize: 20, fontWeight: "600", color: color.ink, marginTop: 8 },
  empty: { fontSize: 15, color: color.ink2 },
  spacer: { flex: 1, minHeight: 8 },
});
