import { useAuth, useSignIn, useSignUp } from "@clerk/expo";
import { Redirect, useRouter } from "expo-router";
import { useState } from "react";
import { StyleSheet, Text, TextInput } from "react-native";

import { Button, ErrorLine, Screen } from "@/components/ui";
import { toE164 } from "@/lib/phone";
import { color, radius } from "@/lib/theme";

type Failure = { code?: string; message?: string; longMessage?: string; errors?: { code?: string; longMessage?: string }[] };

/** Clerk's reason for a failure: an API error's first entry, else the error itself. */
const reason = (e: Failure) => e.errors?.[0]?.code ?? e.code;
const said = (e: Failure) => e.errors?.[0]?.longMessage ?? e.longMessage ?? e.message ?? "Something went wrong.";

/**
 * Sign in with the phone number the renter calls Landline from: Clerk texts a code (Core 3 sign-in, or a
 * sign-up when Clerk doesn't know the number yet), and the code signs them in.
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

  if (isSignedIn) return <Redirect href="/" />;

  async function sendCode() {
    const phone = toE164(typed);
    if (!phone) return setError("Enter your phone number, like 416 555 0100.");
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
      setBusy(false);
    }
  }

  async function verify() {
    if (!/^\d{6}$/.test(code.trim())) return setError("Enter the 6-digit code from the text.");
    setBusy(true);
    setError(null);
    try {
      if (mode === "sign-in") {
        const checked = await signIn.phoneCode.verifyCode({ code: code.trim() });
        if (checked.error) return setError(said(checked.error as Failure));
        const done = await signIn.finalize();
        if (done.error) return setError(said(done.error as Failure));
      } else {
        const checked = await signUp.verifications.verifyPhoneCode({ code: code.trim() });
        if (checked.error) return setError(said(checked.error as Failure));
        if (signUp.status !== "complete") return setError("Clerk needs more than a phone number. Check the Clerk settings.");
        const done = await signUp.finalize();
        if (done.error) return setError(said(done.error as Failure));
      }
      router.replace("/");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <Text style={styles.brand}>Landline</Text>
      <Text style={styles.title}>{mode === "phone" ? "Sign in with your phone" : "Enter the code"}</Text>
      <Text style={styles.body}>
        {mode === "phone"
          ? "Use the number you call Landline from. We’ll text you a code."
          : `We texted a code to ${toE164(typed)}.`}
      </Text>
      {mode === "phone" ? (
        <TextInput
          accessibilityLabel="Phone number"
          autoComplete="tel"
          keyboardType="phone-pad"
          onChangeText={setTyped}
          onSubmitEditing={() => void sendCode()}
          placeholder="416 555 0100"
          placeholderTextColor={color.ink3}
          style={styles.input}
          textContentType="telephoneNumber"
          value={typed}
        />
      ) : (
        <TextInput
          accessibilityLabel="Code from the text"
          autoComplete="sms-otp"
          keyboardType="number-pad"
          maxLength={6}
          onChangeText={setCode}
          onSubmitEditing={() => void verify()}
          placeholder="123456"
          placeholderTextColor={color.ink3}
          style={styles.input}
          textContentType="oneTimeCode"
          value={code}
        />
      )}
      {error ? <ErrorLine>{error}</ErrorLine> : null}
      {mode === "phone" ? (
        <Button busy={busy} label="Text me a code" onPress={() => void sendCode()} />
      ) : (
        <>
          <Button busy={busy} label="Sign in" onPress={() => void verify()} />
          <Button label="Use another number" onPress={() => { setMode("phone"); setCode(""); setError(null); }} quiet />
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  brand: { fontSize: 17, fontWeight: "600", color: color.ink },
  title: { fontSize: 28, fontWeight: "600", color: color.ink },
  body: { fontSize: 16, lineHeight: 22, color: color.ink2 },
  input: {
    height: 52, borderRadius: radius.control, paddingHorizontal: 16, fontSize: 20, color: color.ink,
    backgroundColor: color.surface, borderWidth: StyleSheet.hairlineWidth, borderColor: color.line,
  },
});
