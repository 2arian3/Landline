import { useAuth } from "@clerk/expo";
import { Redirect } from "expo-router";
import { ActivityIndicator, StyleSheet, Text } from "react-native";

import { ListingCard } from "@/components/ListingCard";
import { Back, Button, Calm, ErrorLine, NoCallsYet, Screen } from "@/components/ui";
import { getListings, NO_CALLS_YET } from "@/lib/api";
import { color } from "@/lib/theme";
import { useLoad } from "@/lib/useLoad";

/** Every listing Landline called for the renter, newest call first, each with what the agent said, in their own
 *  words. A number Landline doesn't know yet gets how to start instead. */
export default function Listings() {
  const { isLoaded, isSignedIn } = useAuth();
  const { data: listings, error, newNumber, load, refresh, refreshing } = useLoad(getListings);

  if (!isLoaded) return <Screen><ActivityIndicator color={color.ink} /></Screen>;
  if (!isSignedIn) return <Redirect href="/sign-in" />;

  return (
    <Screen onRefresh={() => void refresh()} refreshing={refreshing}>
      <Back />
      <Text style={styles.title}>My listings</Text>
      {error ? <ErrorLine>{error}</ErrorLine> : null}
      {listings ? (
        listings.length ? (
          <>
            <Text style={styles.meta}>
              {listings.length} {listings.length === 1 ? "call" : "calls"}, newest first, with what each agent said.
            </Text>
            {listings.map((x) => <ListingCard key={x.call_id} listing={x} quotesOpen />)}
          </>
        ) : (
          <Calm>{NO_CALLS_YET}</Calm>
        )
      ) : newNumber ? (
        <NoCallsYet />
      ) : error ? (
        <Button label="Try again" onPress={() => void load()} quiet />
      ) : (
        <ActivityIndicator color={color.ink} />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 28, fontWeight: "600", color: color.ink },
  meta: { fontSize: 15, color: color.ink3 },
});
