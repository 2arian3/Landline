import { useAuth } from "@clerk/expo";
import { Redirect } from "expo-router";
import { useState } from "react";
import { ActivityIndicator, StyleSheet, Switch, Text, View } from "react-native";

import { Button, Chips, ErrorLine, Field, LargeTitle, NoCallsYet, SaveBar, Screen, Tick } from "@/components/ui";
import { ApiError, getProfile, Refused, saveProfile } from "@/lib/api";
import {
  bedOptions, edit, type Field as FormField, type Form, formFrom, problems, type Problems, type Saved, savedFrom,
  SHAREABLE, type Shareable,
} from "@/lib/search";
import { color, hairline, radius, type } from "@/lib/theme";
import { useLoad } from "@/lib/useLoad";

// The web form's words (web/components/MySearch.tsx).
const SHARE_LABEL: Record<Shareable, string> = {
  name: "Your name",
  move_in: "Your move-in date",
  occupants: "How many people",
  pets: "Your pets",
};

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
      <Screen tabs scroll>
        <LargeTitle title="My search" />
        {profile.newNumber ? (
          <NoCallsYet />
        ) : profile.error ? (
          <>
            <ErrorLine>{profile.error}</ErrorLine>
            <Button label="Try again" kind="secondary" onPress={() => void profile.load()} />
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
      for (const k of Object.keys(patch) as FormField[]) delete next[k];
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
  const changes = Object.keys(change).length;
  const bar = status === "saving" ? { state: "Saving…", tone: "ink" as const }
    : status === "failed" ? { state: failedWhy, tone: "dead" as const }
      : dirty ? { state: `${changes} ${changes === 1 ? "change" : "changes"}`, tone: "ink" as const }
        : status === "saved" ? { state: "Saved", tone: "real" as const }
          : null;
  return (
    <View style={{ flex: 1 }}>
      <Screen scroll tabs>
        <LargeTitle title="My search" sub="Your calls fill this in. Change anything here." />

        <View style={styles.switchRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.headline}>Start my next call from this search</Text>
            <Text style={styles.note}>
              {form.use_saved_search ? "On: your next call starts from it." : "Off: every call starts fresh."}
            </Text>
          </View>
          <Switch
            accessibilityLabel="Start my next call from this search"
            value={form.use_saved_search}
            onValueChange={(on) => set({ use_saved_search: on })}
            trackColor={{ true: color.ink, false: color.off }}
            ios_backgroundColor={color.off}
          />
        </View>

        <Field
          label="Monthly budget" hint="Whole dollars a month" error={wrong.budget} before="$" after="a month"
          value={form.budget} onChangeText={(budget) => set({ budget })}
          keyboardType="number-pad" placeholder="2600" maxLength={8}
        />
        <View>
          <Chips
            label="Bedrooms, the fewest you’d take"
            options={bedOptions(form.beds)}
            value={form.beds}
            onChange={(beds) => set({ beds })}
          />
          {wrong.beds ? <Text style={[styles.note, styles.wrong]}>{wrong.beds}</Text> : null}
        </View>
        <Field
          label="Areas" hint="Neighbourhoods or streets, separated by commas" error={wrong.areas}
          value={form.areas} onChangeText={(areas) => set({ areas })}
          placeholder="Annex, Leslieville" autoCapitalize="words"
        />
        <Field
          label="Pets" hint="Separated by commas" error={wrong.pets}
          value={form.pets} onChangeText={(pets) => set({ pets })}
          placeholder="cat" autoCapitalize="none"
        />
        <Field
          label="Must-haves" hint="Separated by commas" error={wrong.must_have}
          value={form.must_have} onChangeText={(must_have) => set({ must_have })}
          placeholder="parking, laundry" autoCapitalize="none"
        />
        <Field
          label="Move-in date" hint="The first day you could move, as YYYY-MM-DD" error={wrong.move_in}
          value={form.move_in} onChangeText={(move_in) => set({ move_in })}
          placeholder="2026-12-01" keyboardType="numbers-and-punctuation" maxLength={10}
          autoCapitalize="none" autoCorrect={false}
        />
        <Field
          label="Email" hint="Where viewing invites go" error={wrong.email}
          value={form.email} onChangeText={(email) => set({ email })}
          placeholder="you@example.com" keyboardType="email-address" autoCapitalize="none" autoCorrect={false}
          autoComplete="email" textContentType="emailAddress"
        />

        <View style={{ gap: 6 }}>
          <Text style={styles.label}>What Landline may tell listing agents</Text>
          <View style={styles.group}>
            {SHAREABLE.map((s, i) => {
              const on = form.shareable.includes(s);
              return (
                <View key={s} style={i > 0 ? styles.rule : null}>
                  <Tick
                    label={SHARE_LABEL[s]}
                    on={on}
                    onPress={() => set({ shareable: on ? form.shareable.filter((x) => x !== s) : [...form.shareable, s] })}
                  />
                </View>
              );
            })}
          </View>
        </View>
        {/* Room for the save bar, so the last field scrolls clear of it. */}
        {bar ? <View style={{ height: 72 }} /> : null}
      </Screen>
      {bar ? (
        <SaveBar
          state={bar.state}
          tone={bar.tone}
          onSave={() => void save()}
          busy={status === "saving"}
          disabled={!dirty}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  headline: { ...type.headline, color: color.ink },
  label: { ...type.caption, color: color.ink2 },
  note: { ...type.callout, color: color.ink2 },
  wrong: { color: color.dead, fontWeight: "500" },
  switchRow: {
    flexDirection: "row", alignItems: "center", gap: 14, paddingVertical: 14, paddingHorizontal: 16,
    borderRadius: radius.card, backgroundColor: color.surface, ...hairline,
  },
  group: { borderRadius: radius.card, backgroundColor: color.surface, ...hairline },
  rule: { borderTopWidth: 1, borderTopColor: color.line },
});
