import { useAuth, useClerk } from "@clerk/expo";
import { Redirect } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { Button, ErrorLine, Screen } from "@/components/ui";
import { ApiError, getMe, type Me } from "@/lib/api";
import { color, radius } from "@/lib/theme";

/** Signed in: who Landline knows this phone as (GET /api/me). Signed out: the sign-in screen. */
export default function Home() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const { signOut } = useClerk();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setMe(await getMe(await getToken()));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Try again.");
    }
  }, [getToken]);

  useEffect(() => {
    if (isSignedIn) void load();
  }, [isSignedIn, load]);

  if (!isLoaded) return <Screen><ActivityIndicator color={color.ink} /></Screen>;
  if (!isSignedIn) return <Redirect href="/sign-in" />;

  return (
    <Screen>
      <Text style={styles.brand}>Landline</Text>
      {me ? (
        <View style={styles.card}>
          <Text style={styles.hello}>Hi, {me.name ?? me.phone}</Text>
          {me.name ? <Text style={styles.meta}>{me.phone}</Text> : null}
          <Text style={styles.meta}>
            {me.calls} {me.calls === 1 ? "place" : "places"} called · {me.booked} {me.booked === 1 ? "viewing" : "viewings"} booked
          </Text>
        </View>
      ) : error ? (
        <>
          <ErrorLine>{error}</ErrorLine>
          <Button label="Try again" onPress={() => void load()} quiet />
        </>
      ) : (
        <ActivityIndicator color={color.ink} />
      )}
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
  spacer: { flex: 1 },
});
