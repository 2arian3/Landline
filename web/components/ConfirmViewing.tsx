"use client";

import { type ReactNode, useEffect, useState } from "react";
import { type Answered, isEmail } from "@/lib/confirmAnswer";
import { answerViewing, loadViewing, type ViewingInvite } from "@/lib/confirmViewing";

const card = "rounded-[var(--radius-card)] bg-surface";
const hairline = { boxShadow: "0 0 0 1px var(--color-hair)" };

/**
 * /v/<token> (O-08d): the listing agent confirms the viewing Landline booked, or says they can't make it. One
 * screen, phone first: the time and address, an email for the calendar invite, two buttons, then a thank-you.
 * The token is the only key, so nothing else about the renter shows.
 */
export function ConfirmViewing({ token }: { token: string }) {
  const [invite, setInvite] = useState<ViewingInvite | "unknown" | "unreachable" | null>(null);
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState<"confirm" | "decline" | null>(null);
  const [result, setResult] = useState<Answered | null>(null);
  const [emailWrong, setEmailWrong] = useState(false);

  useEffect(() => {
    const ctl = new AbortController();
    loadViewing(token, ctl.signal).then(setInvite, () => {});
    return () => ctl.abort();
  }, [token]);

  const send = async (action: "confirm" | "decline") => {
    if (action === "confirm" && !isEmail(email)) {
      setEmailWrong(true);
      return;
    }
    setSending(action);
    const got = await answerViewing(token, action, action === "confirm" ? email.trim() : undefined);
    setSending(null);
    if (got.kind === "bad_email") setEmailWrong(true);
    else setResult(got);
  };

  if (invite === null) return <Screen><p className="text-support text-ink-3">Loading…</p></Screen>;
  if (invite === "unknown" || result?.kind === "unknown") {
    return (
      <Screen>
        <h1 className="text-title font-strong">This link doesn&rsquo;t work</h1>
        <p className="mt-2 text-support text-ink-2">
          It may have been cut short. Open it again from the text Landline sent you.
        </p>
      </Screen>
    );
  }
  if (invite === "unreachable") {
    return (
      <Screen>
        <h1 className="text-title font-strong">Can&rsquo;t reach Landline right now</h1>
        <p className="mt-2 text-support text-ink-2">Try the link again in a moment.</p>
      </Screen>
    );
  }

  const decided = result?.kind === "done" || result?.kind === "already" ? result.status : invite.status;
  const justNow = result?.kind === "done";
  if (decided !== "pending") {
    return (
      <Screen>
        <Viewing invite={invite} />
        <h1 className="mt-5 text-title font-strong">
          {decided === "confirmed" ? (justNow ? "Thanks, it's confirmed" : "Already confirmed") : null}
          {decided === "declined" ? (justNow ? "Thanks for letting us know" : "Already answered") : null}
        </h1>
        <p className="mt-1 text-support text-ink-2">
          {decided === "confirmed"
            ? justNow
              ? result?.kind === "done" && result.inviteSent
                ? `A calendar invite is on its way to ${email.trim()}, and Landline is letting the renter know.`
                : "Landline is letting the renter know."
              : "This viewing was confirmed earlier. There's nothing more to do."
            : justNow
              ? "Landline will tell the renter you can't make this time."
              : "This time was turned down earlier. There's nothing more to do."}
        </p>
      </Screen>
    );
  }

  return (
    <Screen>
      <Viewing invite={invite} />
      <form
        className="mt-5"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (!sending) void send("confirm");
        }}
      >
        <label htmlFor="agent-email" className="block text-meta font-label text-ink-3">
          Your email
        </label>
        <input
          id="agent-email"
          type="email"
          inputMode="email"
          autoComplete="email"
          placeholder="you@example.com"
          aria-describedby="agent-email-note"
          aria-invalid={emailWrong}
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            setEmailWrong(false);
          }}
          className="mt-1.5 block min-h-11 w-full rounded-[var(--radius-control)] bg-ground px-3 text-support text-ink outline-none placeholder:text-ink-3 focus-visible:outline-2 focus-visible:outline-ink"
          style={hairline}
        />
        {/* One line, always there: a mistake replaces the hint instead of pushing the buttons down. */}
        <p id="agent-email-note" className={`mt-1 text-meta ${emailWrong ? "text-dead" : "text-ink-3"}`}>
          {emailWrong ? "That isn't an email address." : "The calendar invite goes here."}
        </p>
        <button
          type="submit"
          disabled={!!sending}
          className="mt-4 min-h-11 w-full rounded-[var(--radius-control)] bg-ink px-4 text-support font-label text-white disabled:opacity-40"
        >
          {sending === "confirm" ? "Confirming…" : "Confirm"}
        </button>
        <button
          type="button"
          disabled={!!sending}
          onClick={() => void send("decline")}
          className="mt-2 min-h-11 w-full rounded-[var(--radius-control)] px-4 text-support font-label text-ink disabled:opacity-40"
          style={hairline}
        >
          {sending === "decline" ? "Sending…" : "Can't make it"}
        </button>
        <p role="status" aria-live="polite" className="mt-3 min-h-5 text-support text-dead">
          {result?.kind === "unreachable" ? "Couldn't send that. Try again in a moment." : null}
        </p>
      </form>
    </Screen>
  );
}

function Viewing({ invite }: { invite: ViewingInvite }) {
  return (
    <div>
      <p className="text-meta font-label text-ink-3">Viewing booked by Landline</p>
      <p className="mt-1 text-title font-strong">{invite.when}</p>
      <p className="mt-0.5 text-fact">{invite.address}</p>
      <p className="mt-2 text-support text-ink-2">
        {invite.renter_first_name
          ? `${invite.renter_first_name}, a renter Landline works for, would like to see it.`
          : "A renter Landline works for would like to see it."}
      </p>
    </div>
  );
}

function Screen({ children }: { children: ReactNode }) {
  return (
    <main
      className="fixed inset-0 overflow-y-auto bg-ground px-4"
      style={{ paddingTop: "max(24px, env(safe-area-inset-top))", paddingBottom: 24 }}
    >
      <div className={`${card} mx-auto w-full max-w-sm p-5`} style={hairline}>
        <p className="mb-4 text-fact font-strong tracking-[-0.01em]">Landline</p>
        {children}
      </div>
    </main>
  );
}
