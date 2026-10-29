import { useAuth, useSignIn, useSignUp } from "@clerk/expo";
import { Redirect, useRouter } from "expo-router";
import { useRef, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { Button, CodeBoxes, ErrorLine, Field, Glyph, LargeTitle, Screen, Wordmark } from "@/components/ui";
import { phoneLabel } from "@/lib/heard";
import { grouped, toE164 } from "@/lib/phone";
import { color, type } from "@/lib/theme";

type Failure = { code?: string; message?: string; longMessage?: string; errors?: { code?: string; longMessage?: string }[] };

/** Clerk's reason for a failure: an API error's first entry, else the error itself. */
const reason = (e: Failure) => e.errors?.[0]?.code ?? e.code;
const said = (e: Failure) => e.errors?.[0]?.longMessage ?? e.longMessage ?? e.message ?? "Something went wrong.";

/**
 * Sign in with the phone number the renter calls Landline from: Clerk texts a code (Core 3 sign-in, or a
 * sign-up when Clerk doesn't know the number yet), and the code signs them in. The code step sends itself
 * once six digits are in, typed or taken from Messages, and Sign in sends the same six again after a failure.
 */
export default function SignIn() {
  const { isSignedIn } = useAuth();
  const { signIn } = useSignIn();
  const { signUp } = useSignUp();
  const router = useRouter();
  const [typed, setTyped] = useState("");
  const [code, setCode] = useState("");
  const [mode, setMode] = useState<"phone" | "sign-in" | "sign-up">("phone");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One request at a time, decided now rather than at the last render: iOS can fill the code twice in one tick,
  // and a second check of a code already used would fail and cover the sign-in that worked.
  const inFlight = useRef(false);

  if (isSignedIn) return <Redirect href="/" />;

  // Ten digits are a Canadian or US number, which the field's "+1" says; one typed with its own "+" is taken as is.
  const phone = toE164(typed);

  async function sendCode() {
    if (!phone) return setError("Enter your phone number, like 416 555 0100.");
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const created = await signIn.create({ identifier: phone });
      if (!created.error) {
        const sent = await signIn.phoneCode.sendCode();
        if (sent.error) return setError(said(sent.error as Failure));
        return setMode("sign-in");
      }
      if (reason(created.error as Failure) !== "form_identifier_not_found") return setError(said(created.error as Failure));
      // A number Clerk hasn't seen: the same code signs them up.
      const made = await signUp.create({ phoneNumber: phone });
      if (made.error) return setError(said(made.error as Failure));
      const sent = await signUp.verifications.sendPhoneCode();
      if (sent.error) return setError(said(sent.error as Failure));
      setMode("sign-up");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function verify(entered: string) {
    if (inFlight.current) return;
    if (!/^\d{6}$/.test(entered)) return setError("Enter the 6-digit code from the text.");
    inFlight.current = true;
    setBusy(true);
    setError(null);
    let signedIn = false;
    try {
      if (mode === "sign-in") {
        const checked = await signIn.phoneCode.verifyCode({ code: entered });
        if (checked.error) return setError(said(checked.error as Failure));
        const done = await signIn.finalize();
        if (done.error) return setError(said(done.error as Failure));
      } else {
        const checked = await signUp.verifications.verifyPhoneCode({ code: entered });
        if (checked.error) return setError(said(checked.error as Failure));
        if (signUp.status !== "complete") return setError("Clerk needs more than a phone number. Check the Clerk settings.");
        const done = await signUp.finalize();
        if (done.error) return setError(said(done.error as Failure));
      }
      signedIn = true;
      router.replace("/");
    } finally {
      // Signed in, it stays closed: a late second fill of the same code mustn't run a check that would fail.
      if (!signedIn) inFlight.current = false;
      setBusy(false);
    }
  }

  if (mode === "phone") {
    return (
      <Screen scroll gap={0}>
        <Wordmark />
        <View style={{ marginTop: 28 }}>
          <LargeTitle title="Sign in with your phone" />
        </View>
        <Text style={styles.lead}>Use the number you call Landline from. We’ll text you a code.</Text>
        <View style={{ marginTop: 24 }}>
          <Field
            big
            label="Phone number"
            hint="Canadian and US numbers"
            error={error ?? undefined}
            before={typed.trim().startsWith("+") ? undefined : "+1"}
            autoComplete="tel"
            autoFocus
            keyboardType="phone-pad"
            onChangeText={(t) => { setTyped(grouped(t)); setError(null); }}
            onSubmitEditing={() => void sendCode()}
            placeholder="416 555 0100"
            textContentType="telephoneNumber"
            value={typed}
          />
        </View>
        <Button
          block
          busy={busy}
          disabled={!phone}
          label="Text me a code"
          onPress={() => void sendCode()}
          style={{ marginTop: 8 }}
        />
      </Screen>
    );
  }

  return (
    <Screen scroll gap={0}>
      <Pressable
        accessibilityRole="button"
        hitSlop={8}
        onPress={() => { setMode("phone"); setCode(""); setError(null); }}
        style={({ pressed }) => [styles.back, pressed && { opacity: 0.6 }]}
      >
        <Glyph name="chevron.left" size={18} />
        <Text style={styles.backLabel}>Use another number</Text>
      </Pressable>
      <View style={{ marginTop: 14 }}>
        <LargeTitle title="Enter the code" />
      </View>
      <Text style={styles.lead}>
        We texted a code to <Text style={styles.number}>{phone ? phoneLabel(phone) : typed}</Text>.
      </Text>
      <View style={{ marginTop: 24 }}>
        <CodeBoxes
          value={code}
          onChange={(c) => { setCode(c); setError(null); }}
          onDone={(c) => void verify(c)}
          error={!!error}
        />
      </View>
      <View style={styles.after}>
        {error ? <ErrorLine>{error}</ErrorLine> : (
          <Text style={styles.hint}>Tap the code above the keyboard, or type it.</Text>
        )}
      </View>
      {/* The code sends itself at six digits; this sends the same six again after a failure (a dropped line,
          Clerk down for a moment), without deleting a digit first. */}
      <Button
        block
        busy={busy}
        disabled={code.length !== 6}
        label="Sign in"
        onPress={() => void verify(code)}
        style={{ marginTop: 16 }}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  lead: { ...type.body, color: color.ink2, marginTop: 8 },
  number: { color: color.ink, fontWeight: "600", fontVariant: ["tabular-nums"] },
  back: {
    alignSelf: "flex-start", flexDirection: "row", alignItems: "center", gap: 2, minHeight: 44, marginLeft: -6,
  },
  backLabel: { fontSize: 17, fontWeight: "600", color: color.ink },
  after: { marginTop: 8, minHeight: 24, justifyContent: "center" },
  hint: { fontSize: 15, lineHeight: 20, color: color.ink2 },
});
