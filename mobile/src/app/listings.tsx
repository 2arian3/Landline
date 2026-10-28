import { useAuth } from "@clerk/expo";
import { Redirect, router } from "expo-router";
import { ActivityIndicator, Pressable, StyleSheet, Text } from "react-native";

import { ListingCard } from "@/components/ListingCard";
import { Button, ErrorLine, Screen } from "@/components/ui";
import { getListings } from "@/lib/api";
import { color } from "@/lib/theme";
import { useLoad } from "@/lib/useLoad";

/** Every listing Landline called for the renter, newest call first, each with what the agent said. */
export default function Listings() {
  const { isLoaded, isSignedIn } = useAuth();
  const { data: listings, error, load, refresh, refreshing } = useLoad(getListings);

  if (!isLoaded) return <Screen><ActivityIndicator color={color.ink} /></Screen>;
  if (!isSignedIn) return <Redirect href="/sign-in" />;

  return (
    <Screen onRefresh={() => void refresh()} refreshing={refreshing}>
      <Pressable
        accessibilityRole="button"
        hitSlop={12}
        onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))}
        style={styles.back}
      >
        <Text style={styles.backLabel}>‹ Home</Text>
      </Pressable>
      <Text style={styles.title}>My listings</Text>
      {error ? <ErrorLine>{error}</ErrorLine> : null}
      {listings ? (
        listings.length ? (
          <>
            <Text style={styles.meta}>
              {listings.length} {listings.length === 1 ? "call" : "calls"}, newest first. Tap a fact to see what the agent said.
            </Text>
            {listings.map((x) => <ListingCard key={x.call_id} listing={x} />)}
          </>
        ) : (
          <Text style={styles.empty}>Landline hasn't called any places for you yet.</Text>
        )
      ) : error ? (
        <Button label="Try again" onPress={() => void load()} quiet />
      ) : (
        <ActivityIndicator color={color.ink} />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  back: { alignSelf: "flex-start", minHeight: 44, justifyContent: "center" },
  backLabel: { fontSize: 17, color: color.ink },
  title: { fontSize: 28, fontWeight: "600", color: color.ink },
  meta: { fontSize: 15, color: color.ink3 },
  empty: { fontSize: 15, color: color.ink2 },
});
