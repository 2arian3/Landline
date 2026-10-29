import { GlassView, isLiquidGlassAvailable } from "expo-glass-effect";
import { SymbolView, type SFSymbol } from "expo-symbols";
import { type ComponentRef, type ReactNode, useRef, useState } from "react";
import {
  ActivityIndicator, Linking, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, type TextInputProps,
  View, type ViewStyle,
} from "react-native";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";

import { LANDLINE_PHONE } from "@/lib/config";
import { phoneLabel } from "@/lib/heard";
import { color, hairline, radius, TAB_ROOM, type } from "@/lib/theme";

/** An SF Symbol in the ink (or `tint`), decorative: the words beside it carry the meaning. */
export function Glyph({ name, size = 20, tint = color.ink }: { name: SFSymbol; size?: number; tint?: string }) {
  return (
    <SymbolView
      name={name}
      size={size}
      tintColor={tint}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      style={{ width: size, height: size }}
    />
  );
}

/** A full screen on the ground, inside the safe area (the bottom is the tab bar's). With `scroll` or
 *  `onRefresh` it scrolls above the keyboard, and with `onRefresh` a pull reloads it. `tabs`: under the floating
 *  tab bar, so the content scrolls clear of it. */
export function Screen({ children, scroll = false, onRefresh, refreshing = false, tabs = false, gap = 16 }: {
  children: ReactNode;
  scroll?: boolean;
  onRefresh?: () => void;
  refreshing?: boolean;
  tabs?: boolean;
  gap?: number;
}) {
  const room = { paddingBottom: tabs ? TAB_ROOM : 24, gap };
  return (
    <SafeAreaView style={styles.screen} edges={tabs ? ["top", "left", "right"] : undefined}>
      {scroll || onRefresh ? (
        <ScrollView
          automaticallyAdjustKeyboardInsets
          contentContainerStyle={[styles.scroll, room]}
          keyboardShouldPersistTaps="handled"
          refreshControl={onRefresh
            ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={color.ink2} />
            : undefined}
        >
          {children}
        </ScrollView>
      ) : (
        <View style={[styles.body, room]}>{children}</View>
      )}
    </SafeAreaView>
  );
}

/** A screen's large title, and under it what the screen is for. */
export function LargeTitle({ title, sub }: { title: string; sub?: ReactNode }) {
  return (
    <View style={{ gap: 4 }}>
      <Text accessibilityRole="header" style={styles.largeTitle}>{title}</Text>
      {sub ? <Text style={styles.sub}>{sub}</Text> : null}
    </View>
  );
}

/** Landline's mark (the favicon's pin in an ink squircle) and its name. */
export function Wordmark() {
  return (
    <View style={styles.wordmark} accessibilityRole="header">
      <View style={styles.mark}><Glyph name="mappin" size={15} tint={color.surface} /></View>
      <Text style={styles.wordmarkText}>Landline</Text>
    </View>
  );
}

type Kind = "primary" | "secondary" | "quiet";

/** Lucent's button: a pill, 52 pt across the screen (`block`) or 44 pt; one primary per screen. A press gives
 *  4% under the finger. */
export function Button({ label, onPress, busy = false, kind = "primary", block = false, icon, disabled = false, style }: {
  label: string;
  onPress: () => void;
  busy?: boolean;
  kind?: Kind;
  block?: boolean;
  icon?: SFSymbol;
  disabled?: boolean;
  style?: ViewStyle;
}) {
  const ink = kind === "primary" ? color.surface : color.ink;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ busy, disabled: busy || disabled }}
      disabled={busy || disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button, block && styles.block, styles[kind], disabled && !busy && { opacity: 0.45 },
        pressed && { transform: [{ scale: 0.96 }] }, style,
      ]}
    >
      {busy ? <ActivityIndicator color={ink} /> : (
        <>
          {icon ? <Glyph name={icon} size={19} tint={ink} /> : null}
          <Text style={[styles.label, { color: ink }]}>{label}</Text>
        </>
      )}
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
    <View style={{ gap: 10 }}>
      <Button label="Call Landline" icon="phone.fill" onPress={call} block />
      <Text style={[styles.sub, { textAlign: "center" }]}>
        {pitch ? "Say what you're looking for, and Landline calls the places for you. " : ""}
        <Text style={styles.nowrap}>{phoneLabel(LANDLINE_PHONE)}</Text>
      </Text>
      {cant ? <ErrorLine>{cant}</ErrorLine> : null}
    </View>
  );
}

const NEXT = [
  ["Say what you need", "A map of places re-sorts while you talk."],
  ["Pick the ones to call", "Landline phones their agents once you hang up."],
  ["Get a text", "What each agent said, and any viewing it booked."],
] as const;

/** What a renter Landline doesn't know yet sees: nothing failed, so no error and no Try again, only how to
 *  start. `title` on Home, where it's the whole screen; a tab with its own title gets the rest. */
export function NoCallsYet({ title = false }: { title?: boolean }) {
  return (
    <>
      {title ? (
        <>
          <View style={styles.helloGlyph}><Glyph name="phone" size={30} /></View>
          <LargeTitle title="No calls yet" />
        </>
      ) : null}
      <Text style={[styles.lead, title && { marginTop: -10 }]}>
        Call Landline and say what you want. What it finds lands here.
      </Text>
      <CallLandline pitch={false} />
      <View style={styles.steps}>
        <Text style={styles.stepsLabel}>What happens next</Text>
        {NEXT.map(([head, line], i) => (
          <View key={head} style={styles.step}>
            <View style={styles.stepNo}><Text style={styles.stepNoText}>{i + 1}</Text></View>
            <View style={{ flex: 1 }}>
              <Text style={styles.headline}>{head}</Text>
              <Text style={styles.sub}>{line}</Text>
            </View>
          </View>
        ))}
      </View>
    </>
  );
}

/** A quiet line: an empty list, or what to do next. */
export function Calm({ children }: { children: ReactNode }) {
  return <Text style={styles.lead}>{children}</Text>;
}

/** A line that says what went wrong, in plain words. */
export function ErrorLine({ children }: { children: ReactNode }) {
  return <Text accessibilityRole="alert" style={styles.error}>{children}</Text>;
}

/** Lucent's field: a label, a pill input (with a "$" before or "a month" after it), and a hint line that an
 *  error takes over, so showing one never moves the form. */
export function Field({ label, hint, error, before, after, big = false, ...input }: {
  label: string;
  hint?: string;
  error?: string;
  before?: string;
  after?: string;
  big?: boolean;
} & TextInputProps) {
  const [focus, setFocus] = useState(false);
  const ref = useRef<ComponentRef<typeof TextInput>>(null);
  return (
    <View style={styles.field}>
      <Text style={[styles.fieldLabel, big && styles.fieldLabelBig]}>{label}</Text>
      <Pressable
        onPress={() => ref.current?.focus()}
        accessible={false}
        style={[styles.input, focus && styles.inputFocus, error ? styles.inputError : null]}
      >
        {before ? <Text style={[styles.affix, big && styles.inputBig]}>{before}</Text> : null}
        <TextInput
          ref={ref}
          accessibilityLabel={label}
          accessibilityHint={error ?? hint}
          placeholderTextColor={color.ink2}
          style={[styles.inputText, big && styles.inputBig]}
          {...input}
          onFocus={(e) => { setFocus(true); input.onFocus?.(e); }}
          onBlur={(e) => { setFocus(false); input.onBlur?.(e); }}
        />
        {after ? <Text style={styles.affix}>{after}</Text> : null}
      </Pressable>
      {error || hint ? (
        <Text style={[styles.hint, big && styles.hintBig, error ? styles.hintError : null]}>{error ?? hint}</Text>
      ) : null}
    </View>
  );
}

/** The texted code: six boxes over one input, so iOS can offer the code from Messages above the keyboard
 *  (oneTimeCode) and Android can read it (sms-otp). The box to type into next wears the ink ring. */
export function CodeBoxes({ value, onChange, onDone, error }: {
  value: string;
  onChange: (code: string) => void;
  onDone: (code: string) => void;
  error?: boolean;
}) {
  const ref = useRef<ComponentRef<typeof TextInput>>(null);
  const [focus, setFocus] = useState(true);
  return (
    <Pressable onPress={() => ref.current?.focus()} accessible={false}>
      <View style={styles.otp} importantForAccessibility="no-hide-descendants" accessibilityElementsHidden>
        {Array.from({ length: 6 }, (_, i) => (
          <View
            key={i}
            style={[styles.otpBox, focus && i === Math.min(value.length, 5) && styles.inputFocus,
              error && styles.inputError]}
          >
            <Text style={styles.otpDigit}>{value[i] ?? ""}</Text>
          </View>
        ))}
      </View>
      <TextInput
        ref={ref}
        accessibilityLabel="Code from the text"
        autoComplete="sms-otp"
        autoFocus
        caretHidden
        keyboardType="number-pad"
        maxLength={6}
        onBlur={() => setFocus(false)}
        onChangeText={(t) => {
          const code = t.replace(/\D/g, "").slice(0, 6);
          onChange(code);
          if (code.length === 6) onDone(code);
        }}
        onFocus={() => setFocus(true)}
        style={styles.otpInput}
        textContentType="oneTimeCode"
        value={value}
      />
    </Pressable>
  );
}

/** One-of chips (Lucent's reason chips): the chosen one in ink tint with an ink ring. */
export function Chips<T extends string>({ label, options, value, onChange }: {
  label: string;
  options: readonly (readonly [T, string])[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <View accessibilityRole="radiogroup" accessibilityLabel={label} style={styles.chips}>
        {options.map(([v, words]) => {
          const on = v === value;
          return (
            <Pressable
              key={v}
              accessibilityRole="radio"
              accessibilityState={{ checked: on }}
              onPress={() => onChange(v)}
              style={({ pressed }) => [styles.chip, on && styles.chipOn, pressed && { transform: [{ scale: 0.96 }] }]}
            >
              <Text style={styles.chipText}>{words}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

/** A tick row: Lucent's rounded square, ink when on. */
export function Tick({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="checkbox"
      accessibilityState={{ checked: on }}
      onPress={onPress}
      style={styles.tickRow}
    >
      <View style={[styles.tick, on && styles.tickOn]}>
        {on ? <Glyph name="checkmark" size={14} tint={color.surface} /> : null}
      </View>
      <Text style={styles.bodyText}>{label}</Text>
    </Pressable>
  );
}

/** Glass where something floats over the content (the tab bar, the save bar): Liquid Glass where the phone has
 *  it, else the same shape nearly solid, so what scrolls under it never shows through the words. */
export function Floating({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  if (isLiquidGlassAvailable()) {
    return <GlassView glassEffectStyle="regular" isInteractive style={[styles.floating, style]}>{children}</GlassView>;
  }
  return <View style={[styles.floating, styles.floatingSolid, style]}>{children}</View>;
}

/** The floating save bar over the tab bar: what will be saved, or what happened, and Save. */
export function SaveBar({ state, tone = "ink", onSave, busy, disabled }: {
  state: string;
  tone?: "ink" | "real" | "dead";
  onSave: () => void;
  busy: boolean;
  disabled: boolean;
}) {
  const insets = useSafeAreaInsets();
  const tint = tone === "real" ? color.real : tone === "dead" ? color.dead : color.ink;
  // Over the tab bar (66 tall) with 12 between, as the canvas draws it.
  return (
    <View pointerEvents="box-none" style={[styles.saveWrap, { bottom: tabBarBottom(insets.bottom) + 66 + 12 }]}>
      <Floating style={styles.saveBar}>
        <View style={styles.saveState} accessibilityLiveRegion="polite">
          {tone === "real" ? <Glyph name="checkmark.circle" size={18} tint={tint} /> : null}
          {tone === "dead" ? <Glyph name="exclamationmark.circle" size={18} tint={tint} /> : null}
          <Text style={[styles.saveText, { color: tint }]} numberOfLines={2}>{state}</Text>
        </View>
        <Button label="Save" onPress={onSave} busy={busy} disabled={disabled} style={{ minWidth: 108 }} />
      </Floating>
    </View>
  );
}

/** How far above the screen's bottom edge the tab bar floats: just above the home indicator. */
export const tabBarBottom = (inset: number) => Math.max(inset - 8, 12);

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: color.ground },
  body: { flex: 1, paddingHorizontal: 20, paddingTop: 12 },
  scroll: { flexGrow: 1, paddingHorizontal: 20, paddingTop: 12 },
  largeTitle: { ...type.largeTitle, color: color.ink },
  sub: { ...type.callout, color: color.ink2 },
  lead: { ...type.body, color: color.ink2 },
  headline: { ...type.headline, color: color.ink },
  bodyText: { ...type.body, color: color.ink },
  nowrap: { fontVariant: ["tabular-nums"] },
  wordmark: { flexDirection: "row", alignItems: "center", gap: 8 },
  mark: { width: 26, height: 26, borderRadius: 7, backgroundColor: color.ink, alignItems: "center", justifyContent: "center" },
  wordmarkText: { fontSize: 18, fontWeight: "600", letterSpacing: -0.27, color: color.ink },

  button: {
    minHeight: 44, paddingHorizontal: 20, borderRadius: radius.pill, flexDirection: "row", gap: 8,
    alignItems: "center", justifyContent: "center",
  },
  block: { minHeight: 52, alignSelf: "stretch" },
  primary: { backgroundColor: color.ink },
  secondary: { backgroundColor: color.surface, ...hairline },
  quiet: { paddingHorizontal: 12 },
  label: { ...type.headline },

  helloGlyph: {
    width: 64, height: 64, borderRadius: 20, backgroundColor: color.tint, alignItems: "center", justifyContent: "center",
  },
  steps: { gap: 14, padding: 20, borderRadius: 24, backgroundColor: color.surface, ...hairline },
  stepsLabel: { ...type.callout, fontWeight: "500", color: color.ink2 },
  step: { flexDirection: "row", gap: 12 },
  stepNo: {
    width: 32, height: 32, borderRadius: 16, backgroundColor: color.tint, alignItems: "center", justifyContent: "center",
  },
  stepNoText: { fontSize: 15, fontWeight: "600", color: color.ink, fontVariant: ["tabular-nums"] },
  error: { ...type.callout, fontWeight: "500", color: color.dead },

  field: { gap: 6 },
  fieldLabel: { ...type.caption, color: color.ink2 },
  fieldLabelBig: { fontSize: 15, lineHeight: 20, fontWeight: "600", color: color.ink },
  input: {
    minHeight: 52, paddingHorizontal: 18, borderRadius: radius.pill, flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: color.surface, ...hairline,
  },
  inputFocus: { borderWidth: 2, borderColor: color.ink },
  inputError: { borderWidth: 2, borderColor: color.dead },
  inputText: { flex: 1, minHeight: 48, ...type.body, lineHeight: undefined, color: color.ink, fontVariant: ["tabular-nums"] },
  inputBig: { fontSize: 20 },
  affix: { ...type.body, fontWeight: "500", color: color.ink2 },
  hint: { ...type.caption, fontWeight: "400", color: color.ink2, minHeight: 18 },
  hintBig: { fontSize: 15, lineHeight: 20 },
  hintError: { color: color.dead, fontWeight: "500" },

  otp: { flexDirection: "row", gap: 8 },
  otpBox: {
    flex: 1, height: 58, borderRadius: 16, alignItems: "center", justifyContent: "center",
    backgroundColor: color.surface, ...hairline,
  },
  otpDigit: { fontSize: 24, fontWeight: "600", color: color.ink, fontVariant: ["tabular-nums"] },
  // The real input sits under the boxes, invisible, so the keyboard, the paste menu and the code from Messages
  // all land in it.
  otpInput: { position: "absolute", left: 0, right: 0, top: 0, bottom: 0, opacity: 0.011, color: "transparent" },

  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    minHeight: 44, paddingHorizontal: 16, borderRadius: radius.pill, justifyContent: "center",
    backgroundColor: color.surface, ...hairline,
  },
  chipOn: { backgroundColor: color.tint, borderWidth: 1.5, borderColor: color.ink },
  chipText: { ...type.callout, fontWeight: "500", color: color.ink },

  tickRow: { flexDirection: "row", alignItems: "center", gap: 12, minHeight: 52, paddingHorizontal: 16 },
  tick: {
    width: 24, height: 24, borderRadius: 7, alignItems: "center", justifyContent: "center",
    backgroundColor: color.surface, borderWidth: 1.5, borderColor: "rgba(55, 53, 47, 0.35)",
  },
  tickOn: { backgroundColor: color.ink, borderColor: color.ink },

  floating: {
    borderRadius: radius.pill,
    shadowColor: "#0f0f0f", shadowOpacity: 0.12, shadowRadius: 24, shadowOffset: { width: 0, height: 10 },
  },
  floatingSolid: { backgroundColor: "rgba(255, 255, 255, 0.94)", ...hairline },
  saveWrap: { position: "absolute", left: 16, right: 16 },
  saveBar: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12,
    paddingVertical: 8, paddingRight: 8, paddingLeft: 20,
  },
  saveState: { flex: 1, flexDirection: "row", alignItems: "center", gap: 8 },
  saveText: { ...type.callout, fontWeight: "500" },
});
