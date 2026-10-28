import { type ReactNode, useState } from "react";
import {
  ActivityIndicator, Linking, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { router } from "expo-router";

import { NO_CALLS_YET } from "@/lib/api";
import { LANDLINE_PHONE } from "@/lib/config";
import { phoneLabel } from "@/lib/heard";
import { color, radius } from "@/lib/theme";

/** A full screen on the warm ground, inside the safe area. With `scroll` or `onRefresh` it scrolls (above the
 *  keyboard), and with `onRefresh` a pull reloads it. */
export function Screen({ children, scroll = false, onRefresh, refreshing = false }: {
  children: ReactNode;
  scroll?: boolean;
  onRefresh?: () => void;
  refreshing?: boolean;
}) {
  return (
    <SafeAreaView style={styles.screen}>
      {scroll || onRefresh ? (
        <ScrollView
          automaticallyAdjustKeyboardInsets
          contentContainerStyle={styles.scroll}
          keyboardShouldPersistTaps="handled"
          refreshControl={onRefresh
            ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={color.ink3} />
            : undefined}
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
export function Button({ label, onPress, busy = false, quiet = false, disabled = false }: {
  label: string;
  onPress: () => void;
  busy?: boolean;
  quiet?: boolean;
  disabled?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ busy, disabled: busy || disabled }}
      disabled={busy || disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button, quiet && styles.quiet, disabled && !busy && { opacity: 0.4 }, pressed && { opacity: 0.85 },
      ]}
    >
      {busy ? <ActivityIndicator color={quiet ? color.ink : color.surface} />
        : <Text style={[styles.label, quiet && { color: color.ink }]}>{label}</Text>}
    </Pressable>
  );
}

/** Back to the screen before, or Home when there's none (a link opened straight to this screen). */
export function Back() {
  return (
    <Pressable
      accessibilityRole="button"
      hitSlop={12}
      onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))}
      style={styles.back}
    >
      <Text style={styles.backLabel}>‹ Home</Text>
    </Pressable>
  );
}

/** Calls Landline from the phone's own dialer; a phone that can't (a tablet, a simulator) gets the number to
 *  dial. Under it, the number, and with `pitch` what happens on the call. */
export function CallLandline({ pitch = true }: { pitch?: boolean }) {
  const [cant, setCant] = useState<string | null>(null);
  const call = () => {
    setCant(null);
    Linking.openURL(`tel:${LANDLINE_PHONE}`).catch(() =>
      setCant(`This phone can't place calls. Call ${phoneLabel(LANDLINE_PHONE)} from one that can.`));
  };
  return (
    <View style={styles.call}>
      <Button label="Call Landline" onPress={call} />
      <Text style={styles.meta}>
        {phoneLabel(LANDLINE_PHONE)}
        {pitch ? ". Say what you're looking for, and Landline calls the places for you." : ""}
      </Text>
      {cant ? <ErrorLine>{cant}</ErrorLine> : null}
    </View>
  );
}

/** What a renter Landline doesn't know yet sees: nothing failed, so no error and no Try again, only how to
 *  start. */
export function NoCallsYet() {
  return (
    <>
      <CallLandline pitch={false} />
      <Calm>{NO_CALLS_YET}</Calm>
    </>
  );
}

/** A quiet line: an empty list, or what to do next. */
export function Calm({ children }: { children: ReactNode }) {
  return <Text style={styles.calm}>{children}</Text>;
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
  call: { gap: 8 },
  meta: { fontSize: 15, color: color.ink3 },
  calm: { fontSize: 15, color: color.ink2 },
  back: { alignSelf: "flex-start", minHeight: 44, justifyContent: "center" },
  backLabel: { fontSize: 17, color: color.ink },
});
