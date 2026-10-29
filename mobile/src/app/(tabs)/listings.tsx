import { useAuth } from "@clerk/expo";
import { Redirect } from "expo-router";
import { ActivityIndicator } from "react-native";

import { ListingCard } from "@/components/ListingCard";
import { Button, Calm, ErrorLine, LargeTitle, NoCallsYet, Screen } from "@/components/ui";
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

  const sub = listings?.length
    ? `${listings.length} ${listings.length === 1 ? "call" : "calls"}, newest first, with what each agent said.`
    : undefined;
  return (
    <Screen tabs gap={14} onRefresh={() => void refresh()} refreshing={refreshing}>
      <LargeTitle title="My listings" sub={sub} />
      {error ? <ErrorLine>{error}</ErrorLine> : null}
      {listings ? (
        listings.length ? (
          listings.map((x) => <ListingCard key={x.call_id} listing={x} quotesOpen />)
        ) : (
          <Calm>{NO_CALLS_YET}</Calm>
        )
      ) : newNumber ? (
        <NoCallsYet />
      ) : error ? (
        <Button label="Try again" kind="secondary" onPress={() => void load()} />
      ) : (
        <ActivityIndicator color={color.ink} />
      )}
    </Screen>
  );
}
