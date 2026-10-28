import { useAuth, useClerk } from "@clerk/expo";
import { Redirect, router } from "expo-router";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { ListingCard } from "@/components/ListingCard";
import { Button, CallLandline, Calm, ErrorLine, Screen } from "@/components/ui";
import { getListings, getMe, NO_CALLS_YET } from "@/lib/api";
import { phoneLabel } from "@/lib/heard";
import { latest } from "@/lib/listings";
import { color, radius } from "@/lib/theme";
import { useLoad } from "@/lib/useLoad";

/** Signed in: who Landline knows this phone as, a button that calls Landline, and the latest results; a number
 *  Landline doesn't know yet gets the button and how to start, never an error. Signed out: the sign-in screen. */
export default function Home() {
  const { isLoaded, isSignedIn } = useAuth();
  const { signOut } = useClerk();
  // Two loads, so a failed listings request still shows who the renter is.
  const who = useLoad(getMe);
  const called = useLoad(getListings);

  if (!isLoaded) return <Screen><ActivityIndicator color={color.ink} /></Screen>;
  if (!isSignedIn) return <Redirect href="/sign-in" />;

  const fresh = who.newNumber || called.newNumber;
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
      ) : who.error || fresh ? null : (
        <ActivityIndicator color={color.ink} />
      )}
      {errors.map((e) => <ErrorLine key={e}>{e}</ErrorLine>)}
      {errors.length > 0 && !(who.data && called.data) ? <Button label="Try again" onPress={again} quiet /> : null}

      <CallLandline pitch={!fresh} />
      {fresh ? <Calm>{NO_CALLS_YET}</Calm> : null}

      {called.data ? (
        <>
          <Text style={styles.heading}>Latest results</Text>
          {called.data.length ? (
            <>
              {latest(called.data).map((x) => <ListingCard key={x.call_id} listing={x} />)}
              <Button label="See all my listings" onPress={() => router.push("/listings")} quiet />
            </>
          ) : (
            <Calm>{NO_CALLS_YET}</Calm>
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
  heading: { fontSize: 20, fontWeight: "600", color: color.ink, marginTop: 8 },
  spacer: { flex: 1, minHeight: 8 },
});
