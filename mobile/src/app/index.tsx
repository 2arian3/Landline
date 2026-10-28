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

async function home(token: string | null) {
  const [me, listings] = await Promise.all([getMe(token), getListings(token)]);
  return { me, listings };
}

/** Signed in: who Landline knows this phone as, a button that calls Landline, and the latest results.
 *  Signed out: the sign-in screen. */
export default function Home() {
  const { isLoaded, isSignedIn } = useAuth();
  const { signOut } = useClerk();
  const { data, error, load, refresh, refreshing } = useLoad(home);
  const [callError, setCallError] = useState<string | null>(null);

  if (!isLoaded) return <Screen><ActivityIndicator color={color.ink} /></Screen>;
  if (!isSignedIn) return <Redirect href="/sign-in" />;

  // The phone's own dialer places the call; a phone that can't (a tablet, a simulator) gets the number to dial.
  const call = () => {
    setCallError(null);
    Linking.openURL(`tel:${LANDLINE_PHONE}`).catch(() =>
      setCallError(`This phone can't place calls. Call ${phoneLabel(LANDLINE_PHONE)} from one that can.`));
  };

  const me = data?.me;
  const shown = data ? latest(data.listings) : [];
  return (
    <Screen onRefresh={() => void refresh()} refreshing={refreshing}>
      <Text style={styles.brand}>Landline</Text>
      {me ? (
        <View style={styles.card}>
          <Text style={styles.hello}>Hi, {me.name ?? phoneLabel(me.phone)}</Text>
          {me.name ? <Text style={styles.meta}>{phoneLabel(me.phone)}</Text> : null}
          <Text style={styles.meta}>
            {me.calls} {me.calls === 1 ? "place" : "places"} called · {me.booked} {me.booked === 1 ? "viewing" : "viewings"} booked
          </Text>
        </View>
      ) : error ? null : (
        <ActivityIndicator color={color.ink} />
      )}
      {error ? (
        <>
          <ErrorLine>{error}</ErrorLine>
          {data ? null : <Button label="Try again" onPress={() => void load()} quiet />}
        </>
      ) : null}

      <View style={styles.call}>
        <Button label="Call Landline" onPress={call} />
        <Text style={styles.meta}>
          {phoneLabel(LANDLINE_PHONE)}. Say what you're looking for, and Landline calls the places for you.
        </Text>
        {callError ? <ErrorLine>{callError}</ErrorLine> : null}
      </View>

      {data ? (
        <>
          <Text style={styles.heading}>Latest results</Text>
          {shown.length ? (
            <>
              {shown.map((x) => <ListingCard key={x.call_id} listing={x} />)}
              <Button label="See all my listings" onPress={() => router.push("/listings")} quiet />
            </>
          ) : (
            <Text style={styles.empty}>Landline hasn't called any places for you yet.</Text>
          )}
        </>
      ) : null}

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
