import { ClerkProvider } from "@clerk/expo";
import { tokenCache } from "@clerk/expo/token-cache";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { StyleSheet, Text } from "react-native";

import { Screen } from "@/components/ui";
import { CLERK_PUBLISHABLE_KEY } from "@/lib/config";
import { color, type } from "@/lib/theme";

export default function RootLayout() {
  // No key yet (a fresh clone, or a teammate without mobile/.env): say so rather than crash.
  if (!CLERK_PUBLISHABLE_KEY) {
    return (
      <Screen>
        <Text style={styles.title}>Landline isn’t set up yet</Text>
        <Text style={styles.body}>
          Add the Clerk publishable key to mobile/.env as EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY, then restart Expo.
        </Text>
      </Screen>
    );
  }
  return (
    // The token cache keeps the renter signed in across restarts, in the phone's secure store.
    <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY} tokenCache={tokenCache}>
      <StatusBar style="dark" />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: color.ground } }} />
    </ClerkProvider>
  );
}

const styles = StyleSheet.create({
  title: { ...type.largeTitle, color: color.ink },
  body: { ...type.body, color: color.ink2 },
});
