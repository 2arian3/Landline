import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { color, radius } from "@/lib/theme";

/** A full screen on the warm ground, inside the safe area. With `onRefresh` it scrolls, and a pull reloads it. */
export function Screen({ children, onRefresh, refreshing = false }: {
  children: ReactNode;
  onRefresh?: () => void;
  refreshing?: boolean;
}) {
  return (
    <SafeAreaView style={styles.screen}>
      {onRefresh ? (
        <ScrollView
          contentContainerStyle={styles.scroll}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={color.ink3} />}
        >
          {children}
        </ScrollView>
      ) : (
        <View style={styles.body}>{children}</View>
      )}
    </SafeAreaView>
  );
}

/** The one primary action on a screen: ink on ground, 52 pt tall. */
export function Button({ label, onPress, busy = false, quiet = false }: {
  label: string;
  onPress: () => void;
  busy?: boolean;
  quiet?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ busy, disabled: busy }}
      disabled={busy}
      onPress={onPress}
      style={({ pressed }) => [styles.button, quiet && styles.quiet, pressed && { opacity: 0.85 }]}
    >
      {busy ? <ActivityIndicator color={quiet ? color.ink : color.surface} />
        : <Text style={[styles.label, quiet && { color: color.ink }]}>{label}</Text>}
    </Pressable>
  );
}

/** A line that says what went wrong, in plain words. */
export function ErrorLine({ children }: { children: ReactNode }) {
  return <Text accessibilityRole="alert" style={styles.error}>{children}</Text>;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: color.ground },
  body: { flex: 1, paddingHorizontal: 20, paddingTop: 24, gap: 16 },
  scroll: { flexGrow: 1, paddingHorizontal: 20, paddingTop: 24, paddingBottom: 24, gap: 16 },
  button: {
    height: 52, borderRadius: radius.control, backgroundColor: color.ink,
    alignItems: "center", justifyContent: "center",
  },
  quiet: { backgroundColor: color.surface, borderWidth: StyleSheet.hairlineWidth, borderColor: color.line },
  label: { color: color.surface, fontSize: 17, fontWeight: "600" },
  error: { color: color.dead, fontSize: 15 },
});
