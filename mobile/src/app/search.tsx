import { useAuth } from "@clerk/expo";
import { Redirect } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator, Pressable, StyleSheet, Switch, Text, TextInput, type TextInputProps, View,
} from "react-native";

import { Back, Button, ErrorLine, Screen } from "@/components/ui";
import { ApiError, getProfile, Refused, saveProfile } from "@/lib/api";
import {
  edit, type Field, type Form, formFrom, problems, type Problems, type Saved, savedFrom, SHAREABLE, type Shareable,
} from "@/lib/search";
import { color, radius } from "@/lib/theme";
import { useLoad } from "@/lib/useLoad";

// The web form's words (web/components/MySearch.tsx).
const SHARE_LABEL: Record<Shareable, string> = {
  name: "Your name",
  move_in: "Your move-in date",
  occupants: "How many people",
  pets: "Your pets",
};

const BEDS = [
  ["", "Any"],
  ["0", "Studio or bigger"],
  ["1", "1+ bedroom"],
  ["2", "2+ bedrooms"],
  ["3", "3+ bedrooms"],
  ["4", "4+ bedrooms"],
  ["5", "5+ bedrooms"],
] as const;

const COULD_NOT_SAVE = "Couldn't save. Try again in a moment.";

/**
 * "My search" (U-13d), as on the web's /me: the renter edits the search Landline keeps, chooses what it may
 * tell listing agents, and whether the next call starts from this search. Only what changed is sent (PATCH
 * /api/me/profile); the API checks every field again. The move-in date is typed as YYYY-MM-DD, since a date
 * picker would need a package.
 */
export default function MySearch() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const profile = useLoad(getProfile);
  const [saved, setSaved] = useState<Saved | null>(null);   // once saved here; until then, what loaded
  const [typed, setTyped] = useState<Form | null>(null);    // once edited; until then, the saved values
  const [wrong, setWrong] = useState<Problems>({});
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const [failure, setFailure] = useState(COULD_NOT_SAVE);

  if (!isLoaded) return <Screen><ActivityIndicator color={color.ink} /></Screen>;
  if (!isSignedIn) return <Redirect href="/sign-in" />;

  const base = saved ?? (profile.data ? savedFrom(profile.data) : null);
  const form = typed ?? (base ? formFrom(base) : null);
  if (!base || !form) {
    return (
      <Screen>
        <Back />
        <Text style={styles.title}>My search</Text>
        {profile.error ? (
          <>
            <ErrorLine>{profile.error}</ErrorLine>
            <Button label="Try again" onPress={() => void profile.load()} quiet />
          </>
        ) : (
          <ActivityIndicator color={color.ink} />
        )}
      </Screen>
    );
  }

  const change = edit(form, base);
  const dirty = Object.keys(change).length > 0;

  const set = (patch: Partial<Form>) => {
    setTyped({ ...form, ...patch });
    setWrong((w) => {
      const next = { ...w };
      for (const k of Object.keys(patch) as Field[]) delete next[k];
      return next;
    });
    if (status !== "saving") setStatus("idle");
  };

  const save = async () => {
    const found = problems(form);
    if (Object.keys(found).length) {
      setWrong(found);
      setStatus("failed");
      return;
    }
    setStatus("saving");
    try {
      const now = savedFrom(await saveProfile(await getToken(), change));
      setSaved(now);
      setTyped(formFrom(now));
      setWrong({});
      setStatus("saved");
    } catch (e) {
      setWrong(e instanceof Refused ? e.problems : {});
      setFailure(e instanceof ApiError && !(e instanceof Refused) ? e.message : COULD_NOT_SAVE);
      setStatus("failed");
    }
  };

  const failedWhy = Object.keys(wrong).length ? "Check the fields marked above." : failure;
  return (
    <Screen scroll>
      <Back />
      <Text style={styles.title}>My search</Text>

      <TextField
        label="Monthly budget" hint="Whole dollars a month" error={wrong.budget}
        value={form.budget} onChangeText={(budget) => set({ budget })}
        keyboardType="number-pad" placeholder="2600" maxLength={8}
      />

      <View>
        <Text style={styles.label}>Bedrooms</Text>
        <View accessibilityRole="radiogroup" style={styles.options}>
          {BEDS.map(([v, words]) => {
            const on = form.beds === v;
            return (
              <Pressable
                key={v}
                accessibilityRole="radio"
                accessibilityState={{ checked: on }}
                onPress={() => set({ beds: v })}
                style={[styles.option, on && styles.optionOn]}
              >
                <Text style={[styles.optionText, on && { color: color.surface }]}>{words}</Text>
              </Pressable>
            );
          })}
        </View>
        <Text style={[styles.note, wrong.beds ? styles.wrong : null]}>{wrong.beds ?? "The fewest you'd take"}</Text>
      </View>

      <TextField
        label="Areas" hint="Neighbourhoods or streets, separated by commas" error={wrong.areas}
        value={form.areas} onChangeText={(areas) => set({ areas })}
        placeholder="Annex, Leslieville" autoCapitalize="words"
      />
      <TextField
        label="Pets" hint="Separated by commas" error={wrong.pets}
        value={form.pets} onChangeText={(pets) => set({ pets })}
        placeholder="cat" autoCapitalize="none"
      />
      <TextField
        label="Must-haves" hint="Separated by commas" error={wrong.must_have}
        value={form.must_have} onChangeText={(must_have) => set({ must_have })}
        placeholder="parking, laundry" autoCapitalize="none"
      />
      <TextField
        label="Move-in date" hint="The first day you could move" error={wrong.move_in}
        value={form.move_in} onChangeText={(move_in) => set({ move_in })}
        placeholder="YYYY-MM-DD" keyboardType="numbers-and-punctuation" maxLength={10}
        autoCapitalize="none" autoCorrect={false}
      />
      <TextField
        label="Email" hint="Where viewing invites go" error={wrong.email}
        value={form.email} onChangeText={(email) => set({ email })}
        placeholder="you@example.com" keyboardType="email-address" autoCapitalize="none" autoCorrect={false}
        autoComplete="email" textContentType="emailAddress"
      />

      <View>
        <Text style={styles.label}>What Landline may tell listing agents</Text>
        {SHAREABLE.map((s) => {
          const on = form.shareable.includes(s);
          return (
            <Pressable
              key={s}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: on }}
              onPress={() => set({ shareable: on ? form.shareable.filter((x) => x !== s) : [...form.shareable, s] })}
              style={styles.check}
            >
              <View style={[styles.box, on && styles.boxOn]}>{on ? <Text style={styles.tick}>✓</Text> : null}</View>
              <Text style={styles.body}>{SHARE_LABEL[s]}</Text>
            </Pressable>
          );
        })}
      </View>

      <View style={styles.switchRow}>
        <View style={styles.switchWords}>
          <Text style={styles.body}>Start my next call from this search</Text>
          <Text style={styles.note}>
            {form.use_saved_search ? "On: your next call starts from it." : "Off: every call starts fresh."}
          </Text>
        </View>
        <Switch
          accessibilityLabel="Start my next call from this search"
          value={form.use_saved_search}
          onValueChange={(on) => set({ use_saved_search: on })}
          trackColor={{ true: color.ink, false: color.ink3 }}
          ios_backgroundColor={color.ink3}
        />
      </View>

      <Button label="Save" onPress={() => void save()} busy={status === "saving"} disabled={!dirty} />
      {/* Always there, so a status never moves the form. */}
      <Text
        accessibilityLiveRegion="polite"
        style={[styles.status, status === "saved" ? { color: color.real } : styles.wrong]}
      >
        {status === "saved" ? "Saved." : status === "failed" ? failedWhy : ""}
      </Text>
    </Screen>
  );
}

/** A labelled text field. An error takes the hint's line, so showing one never moves the form. */
function TextField({ label, hint, error, ...input }: { label: string; hint: string; error?: string } & TextInputProps) {
  return (
    <View>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        accessibilityHint={error ?? hint}
        placeholderTextColor={color.ink3}
        style={[styles.input, error ? { borderColor: color.dead } : null]}
        {...input}
      />
      <Text style={[styles.note, error ? styles.wrong : null]}>{error ?? hint}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 28, fontWeight: "600", color: color.ink },
  label: { fontSize: 13, fontWeight: "600", color: color.ink3, marginBottom: 6 },
  input: {
    minHeight: 44, borderRadius: radius.control, backgroundColor: color.surface, paddingHorizontal: 12,
    fontSize: 16, color: color.ink, borderWidth: StyleSheet.hairlineWidth, borderColor: color.line,
  },
  note: { fontSize: 13, color: color.ink3, marginTop: 4 },
  wrong: { color: color.dead },
  options: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  option: {
    minHeight: 44, paddingHorizontal: 14, justifyContent: "center", borderRadius: radius.control,
    backgroundColor: color.surface, borderWidth: StyleSheet.hairlineWidth, borderColor: color.line,
  },
  optionOn: { backgroundColor: color.ink, borderColor: color.ink },
  optionText: { fontSize: 15, color: color.ink },
  check: { flexDirection: "row", alignItems: "center", gap: 12, minHeight: 44 },
  box: {
    width: 22, height: 22, borderRadius: 6, alignItems: "center", justifyContent: "center",
    backgroundColor: color.surface, borderWidth: 1.5, borderColor: color.ink3,
  },
  boxOn: { backgroundColor: color.ink, borderColor: color.ink },
  tick: { color: color.surface, fontSize: 14, fontWeight: "700" },
  body: { fontSize: 16, color: color.ink },
  switchRow: { flexDirection: "row", alignItems: "center", gap: 16 },
  switchWords: { flex: 1 },
  status: { minHeight: 20, fontSize: 15 },
});
