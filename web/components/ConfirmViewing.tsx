"use client";

import { type ReactNode, useEffect, useState } from "react";
import { Icon, type IconName, Mark } from "./Icon";
import { type Answered, isEmail } from "@/lib/confirmAnswer";
import { answerViewing, loadViewing, type ViewingInvite } from "@/lib/confirmViewing";
import { formatWhen } from "@/lib/time";

/**
 * /v/<token> (O-08d): the listing agent confirms the viewing Landline booked, or says they can't make it. One
 * screen, phone first, on Lucent (the canvas's V1–V4): the time as the headline, the address, one email for
 * the calendar invite with its hint line, Confirm and Can't make it, then a thank-you. The token is the only
 * key, so nothing else about the renter shows.
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

  if (invite === null) {
    return (
      <Page>
        <p className="t-callout c-2" role="status">
          Loading…
        </p>
      </Page>
    );
  }
  if (invite === "unknown" || result?.kind === "unknown") {
    return (
      <Page>
        <Notice icon="circle-alert" title="This link doesn’t work">
          It may have been cut short. Open it again from the text Landline sent you.
        </Notice>
      </Page>
    );
  }
  if (invite === "unreachable") {
    return (
      <Page>
        <Notice icon="wifi-off" title="Can’t reach Landline right now">
          Try the link again in a moment.
        </Notice>
      </Page>
    );
  }

  // The time the code wrote from starts_at, as the board writes it; the API's words if it doesn't parse.
  const when = formatWhen(invite.starts_at) ?? invite.when;
  const decided = result?.kind === "done" || result?.kind === "already" ? result.status : invite.status;

  // Answered just now (V2, V3): a mark, a thank-you, and the viewing as it now stands.
  if (result?.kind === "done") {
    const confirmed = result.status === "confirmed";
    return (
      <Page foot="You can close this page.">
        <div role="status" aria-live="polite" className="flex flex-col">
          <span
            aria-hidden
            className={`mt-[18px] grid size-14 place-items-center rounded-full ${confirmed ? "bg-real text-white" : "bg-fill-muted text-ink"}`}
          >
            <Icon name="check" size={24} className={confirmed ? "[stroke-width:2.6]" : undefined} />
          </span>
          <h1 className="mt-5 font-display text-[30px] leading-9 font-semibold tracking-[-0.02em]">
            {confirmed ? "Thanks, it’s confirmed" : "Thanks for letting us know"}
          </h1>
          <p className="mt-2.5 text-[17px] leading-[25px] c-2">
            {confirmed ? (
              result.inviteSent ? (
                <>
                  A calendar invite is on its way to <b className="font-semibold text-ink">{email.trim()}</b>, and
                  Landline is letting the renter know.
                </>
              ) : (
                "Landline is letting the renter know."
              )
            ) : (
              "Landline will tell the renter you can’t make this time."
            )}
          </p>
          <div
            className={`mt-7 grid grid-cols-[24px_minmax(0,1fr)] gap-x-3 rounded-[20px] px-[18px] py-4 ${
              confirmed ? "bg-surface shadow-[0_0_0_1px_var(--color-hairline)]" : "bg-surface-raised"
            }`}
          >
            <Icon name={confirmed ? "calendar-check" : "calendar-x"} className={confirmed ? "c-real" : "c-2"} />
            <div>
              <p className={`tnum text-[19px] leading-6 font-semibold tracking-[-0.01em] ${confirmed ? "" : "c-2 line-through"}`}>
                {when}
              </p>
              <p className="mt-0.5 text-[15px] leading-[21px] c-2">
                {invite.address}
                {confirmed ? (invite.renter_first_name ? ` · ${invite.renter_first_name}` : "") : " · not this time"}
              </p>
            </div>
          </div>
        </div>
      </Page>
    );
  }

  // Opened again after an answer (V4, "Opened twice"): the answer, not the form.
  if (decided !== "pending") {
    return (
      <Page foot="You can close this page.">
        <Viewing invite={invite} when={when} />
        <h1 className="mt-[26px] font-display text-[22px] leading-7 font-semibold tracking-[-0.015em]">
          {decided === "confirmed" ? "Already confirmed" : "Already answered"}
        </h1>
        <p className="mt-2 t-callout c-2">
          {decided === "confirmed"
            ? "This viewing was confirmed earlier. There’s nothing more to do."
            : "This time was turned down earlier. There’s nothing more to do."}
        </p>
      </Page>
    );
  }

  // V1: the viewing, then one email and two answers.
  return (
    <Page foot="Landline is the AI assistant that called you for this renter.">
      <Viewing invite={invite} when={when} />
      <div className="my-6 h-px bg-hairline" />
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (!sending) void send("confirm");
        }}
      >
        <div className="field">
          <label htmlFor="agent-email" className="field-label text-[15px]! leading-5! font-semibold! text-ink!">
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
            readOnly={!!sending}
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setEmailWrong(false);
            }}
            className={`input w-full text-[16px] outline-none placeholder:text-ink-secondary focus:shadow-[0_0_0_2px_var(--color-ink)] ${
              emailWrong ? "is-error" : ""
            } ${sending ? "is-locked" : ""}`}
          />
          {/* One line, always there: a mistake replaces the hint instead of pushing the buttons down. */}
          <p id="agent-email-note" className={`hint text-[15px]! leading-5! ${emailWrong ? "is-error" : ""}`}>
            {emailWrong ? "That isn’t an email address." : "The calendar invite goes here."}
          </p>
        </div>
        <div className="mt-[22px] flex flex-col gap-2.5">
          <button type="submit" disabled={!!sending} className="btn btn-primary btn-block">
            {sending === "confirm" ? "Confirming…" : "Confirm"}
          </button>
          <button
            type="button"
            disabled={!!sending}
            onClick={() => void send("decline")}
            className={`btn btn-secondary btn-block ${sending ? "opacity-45" : ""}`}
          >
            {sending === "decline" ? "Sending…" : "Can’t make it"}
          </button>
        </div>
        <p role="status" aria-live="polite" className="mt-3 min-h-5 t-callout text-dead">
          {result?.kind === "unreachable" ? "Couldn’t send that. Try again in a moment." : null}
        </p>
      </form>
    </Page>
  );
}

/** The viewing as the headline: who booked it, when (the code's time), where, and for whom. */
function Viewing({ invite, when }: { invite: ViewingInvite; when: string }) {
  return (
    <div className="flex flex-col">
      <p className="flex items-center gap-[7px] text-[15px] leading-5 font-medium c-2">
        <Icon name="calendar" size={16} />
        Viewing booked by Landline
      </p>
      <h1 className="tnum mt-2.5 font-display text-[30px] leading-9 font-semibold tracking-[-0.02em]">{when}</h1>
      <p className="mt-1.5 text-[19px] leading-6 font-semibold tracking-[-0.01em]">{invite.address}</p>
      <p className="mt-3.5 text-[17px] leading-6 c-2">
        {invite.renter_first_name
          ? `${invite.renter_first_name}, a renter Landline works for, would like to see it.`
          : "A renter Landline works for would like to see it."}
      </p>
    </div>
  );
}

/** A page that matches no viewing, or can't load one: what happened and what to do, in one sentence. */
function Notice({ icon, title, children }: { icon: IconName; title: string; children: ReactNode }) {
  return (
    <div role="status" className="flex flex-col">
      <span aria-hidden className="mb-4 grid size-11 place-items-center rounded-full bg-fill-muted">
        <Icon name={icon} size={20} />
      </span>
      <h1 className="font-display text-[22px] leading-7 font-semibold tracking-[-0.015em]">{title}</h1>
      <p className="mt-2 t-callout c-2">{children}</p>
    </div>
  );
}

/** The page: the wordmark, the content in a phone-wide column, and a quiet line at the foot. */
function Page({ children, foot }: { children: ReactNode; foot?: string }) {
  return (
    <div
      className="fixed inset-0 flex flex-col overflow-y-auto bg-ground"
      style={{ paddingTop: "env(safe-area-inset-top)", paddingBottom: "max(28px, env(safe-area-inset-bottom))" }}
    >
      <div className="mx-auto flex w-full max-w-[420px] flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center px-6">
          <p className="wordmark text-[19px]! gap-[9px]!">
            <Mark />
            Landline
          </p>
        </header>
        <main className="flex flex-1 flex-col px-6 pt-3 pb-6">{children}</main>
        {foot && <p className="px-6 text-center text-[13px] leading-[18px] c-2">{foot}</p>}
      </div>
    </div>
  );
}
