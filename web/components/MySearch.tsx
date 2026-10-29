"use client";

import { type ReactNode, useEffect, useId, useState } from "react";
import { isMeRefusal, type MeRefusal } from "@/lib/me";
import { type SaveResult, saveProfile } from "@/lib/profile";
import {
  edit,
  type Field,
  type Form,
  formFrom,
  type MyProfile,
  type Problems,
  problems,
  savedFrom,
  type Shareable,
  SHAREABLE,
  touched,
} from "@/lib/searchForm";
import { Icon } from "./Icon";

const SHARE_LABEL: Record<Shareable, string> = {
  name: "Your name",
  move_in: "Your move-in date",
  occupants: "How many people",
  pets: "Your pets",
};
const BEDS = [
  ["", "Any"],
  ["0", "Studio+"],
  ["1", "1+"],
  ["2", "2+"],
  ["3", "3+"],
  ["4", "4+"],
] as const;

// The kit's field (Forms): a 52 px pill, ink-ringed while it has focus, red-ringed when it's wrong.
const field = (wrong: boolean) => `input focus-within:shadow-[0_0_0_2px_var(--color-ink)] ${wrong ? "is-error" : ""}`;
// The input fills the pill's height, so the whole 52 px is the tap target.
const bare = "min-w-0 flex-1 self-stretch bg-transparent outline-none placeholder:text-ink-secondary";

/**
 * "My search" on /me (U-12b, W4): the renter edits the search Landline keeps, chooses what it may tell listing
 * agents, and whether the next call starts from this search. Only what changed is sent (PATCH
 * /api/me/profile); the API checks every field again. The save bar's line keeps its room, so nothing shifts,
 * and Undo changes puts back what's saved. The page loads the profile and gets each save back (`onSaved`), so
 * "What you want" beside My listings reads the same.
 */
export function MySearch({
  saved,
  getToken,
  onSaved,
}: {
  saved: MyProfile | MeRefusal | null;
  getToken: () => Promise<string | null>;
  onSaved: (profile: MyProfile) => void;
}) {
  const [form, setForm] = useState<Form | null>(null);
  const [wrong, setWrong] = useState<Problems>({});
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const [signedOut, setSignedOut] = useState(false); // the last save was refused for sign-in

  // The form starts from the saved search once it's loaded; a save hands it back the new one.
  const loaded = saved && !isMeRefusal(saved) ? saved : null;
  useEffect(() => {
    if (loaded && form === null) setForm(formFrom(savedFrom(loaded)));
  }, [loaded, form]);

  if (saved === null || (loaded && !form)) return <p className="t-callout c-2">Loading…</p>;
  if (!loaded || !form) return <p className="t-callout c-2">Can&rsquo;t load your search right now.</p>;

  const base = savedFrom(loaded);
  const change = edit(form, base);
  const found = problems(form, touched(form, base)); // shown on Save, so a typo there never goes unnoticed
  const changes = Object.keys(change).length;
  const dirty = changes > 0 || Object.keys(found).length > 0;
  const set = (patch: Partial<Form>) => {
    setForm({ ...form, ...patch });
    setWrong((w) => {
      const next = { ...w };
      for (const k of Object.keys(patch) as Field[]) delete next[k];
      return next;
    });
    if (status !== "saving") setStatus("idle");
  };
  const undo = () => {
    setForm(formFrom(base));
    setWrong({});
    setStatus("idle");
  };

  const save = async () => {
    if (Object.keys(found).length) {
      setWrong(found);
      setStatus("failed");
      return;
    }
    setStatus("saving");
    const sent = form;
    let result: SaveResult;
    try {
      const token = await getToken();
      result = token ? await saveProfile(token, change) : { refused: "signin" };
    } catch {
      result = { refused: "unreachable" }; // Clerk couldn't give a token
    }
    if ("profile" in result) {
      onSaved(result.profile);
      // What the renter typed while it saved stays, and is still a change from the new saved values.
      setForm((now) => (now === sent ? formFrom(savedFrom(result.profile)) : now));
      setWrong({});
      setStatus("saved");
    } else {
      setWrong("problems" in result ? result.problems : {});
      setSignedOut("refused" in result && result.refused === "signin");
      setStatus("failed");
    }
  };

  const failedWhy = Object.keys(wrong).length
    ? "Check the fields marked above."
    : signedOut
      ? "Your sign-in has ended. Sign out, then sign in again."
      : "Couldn't save. Try again in a moment.";
  // A bedroom count a call saved that the chips don't have (1.5, 6) is shown as it is, not as "Any".
  const beds: readonly (readonly [string, string])[] = BEDS.some(([v]) => v === form.beds)
    ? BEDS
    : [...BEDS, [form.beds, `${form.beds}+`]];

  return (
    <form
      className="rounded-[28px] bg-surface px-5 pb-5 pt-6 shadow-[0_0_0_1px_var(--color-hairline)] md:px-7 md:pt-7"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty && status !== "saving") void save();
      }}
    >
      <div className="grid gap-x-7 gap-y-[18px] md:grid-cols-2">
        <Labelled label="Monthly budget" hint="Whole dollars a month" error={wrong.budget}>
          {(id, described) => (
            <div className={field(!!wrong.budget)}>
              <span className="pre">$</span>
              <input
                id={id}
                aria-describedby={described}
                aria-invalid={!!wrong.budget}
                inputMode="numeric"
                placeholder="2600"
                value={form.budget}
                onChange={(e) => set({ budget: e.target.value })}
                className={`${bare} tnum`}
              />
              <span className="suf">a month</span>
            </div>
          )}
        </Labelled>
        <Chips label={"Bedrooms, the fewest you’d take"} error={wrong.beds}>
          {beds.map(([v, words]) => (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={form.beds === v}
              className={form.beds === v ? "is-on" : undefined}
              onClick={() => set({ beds: v })}
            >
              {words}
            </button>
          ))}
        </Chips>
        <Labelled label="Areas" hint="Neighbourhoods or streets, separated by commas" error={wrong.areas} wide>
          {(id, described) => (
            <div className={field(!!wrong.areas)}>
              <input
                id={id}
                aria-describedby={described}
                aria-invalid={!!wrong.areas}
                placeholder="Annex, Leslieville"
                value={form.areas}
                onChange={(e) => set({ areas: e.target.value })}
                className={bare}
              />
            </div>
          )}
        </Labelled>
        <Labelled label="Pets" hint="Separated by commas" error={wrong.pets}>
          {(id, described) => (
            <div className={field(!!wrong.pets)}>
              <input
                id={id}
                aria-describedby={described}
                aria-invalid={!!wrong.pets}
                placeholder="cat"
                value={form.pets}
                onChange={(e) => set({ pets: e.target.value })}
                className={bare}
              />
            </div>
          )}
        </Labelled>
        <Labelled label="Must-haves" hint="Separated by commas" error={wrong.must_have}>
          {(id, described) => (
            <div className={field(!!wrong.must_have)}>
              <input
                id={id}
                aria-describedby={described}
                aria-invalid={!!wrong.must_have}
                placeholder="parking, laundry"
                value={form.must_have}
                onChange={(e) => set({ must_have: e.target.value })}
                className={bare}
              />
            </div>
          )}
        </Labelled>
        <Labelled label="Move-in date" hint="The first day you could move" error={wrong.move_in}>
          {(id, described) => (
            <div className={field(!!wrong.move_in)}>
              <input
                id={id}
                type="date"
                aria-describedby={described}
                aria-invalid={!!wrong.move_in}
                value={form.move_in}
                onChange={(e) => set({ move_in: e.target.value })}
                className={`${bare} tnum min-h-[44px]`}
              />
            </div>
          )}
        </Labelled>
        <Labelled label="Email" hint="Where viewing invites go" error={wrong.email}>
          {(id, described) => (
            <div className={field(!!wrong.email)}>
              <input
                id={id}
                type="email"
                autoComplete="email"
                aria-describedby={described}
                aria-invalid={!!wrong.email}
                placeholder="you@example.com"
                value={form.email}
                onChange={(e) => set({ email: e.target.value })}
                className={bare}
              />
            </div>
          )}
        </Labelled>
      </div>

      <fieldset className="mt-7">
        <legend className="t-headline">What Landline may tell listing agents</legend>
        <div className="mt-2.5 grid gap-x-7 md:grid-cols-2">
          {SHAREABLE.map((s) => {
            const on = form.shareable.includes(s);
            return (
              <label
                key={s}
                className="flex min-h-12 cursor-pointer items-center gap-3 border-t border-hairline text-[17px] font-medium leading-[22px]"
              >
                <input
                  type="checkbox"
                  className="peer sr-only"
                  checked={on}
                  onChange={(e) =>
                    set({ shareable: e.target.checked ? [...form.shareable, s] : form.shareable.filter((x) => x !== s) })
                  }
                />
                {/* The kit's tick; the real checkbox under it takes the keyboard, so its focus shows here. */}
                <span
                  aria-hidden
                  className={`tick peer-focus-visible:outline peer-focus-visible:outline-[3px] peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ink ${on ? "is-on" : ""}`}
                >
                  <Icon name="check" size={16} />
                </span>
                {SHARE_LABEL[s]}
              </label>
            );
          })}
        </div>
      </fieldset>

      <div className="mt-5 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 border-t border-hairline py-[18px]">
        <div>
          <p id="me-next-call" className="t-headline">
            Start my next call from this search
          </p>
          <p className="t-callout c-2">
            {form.use_saved_search ? "On: your next call starts from it." : "Off: every call starts fresh."}
          </p>
        </div>
        {/* The kit's switch is 31 px; the button around it is the 44 px target. */}
        <button
          type="button"
          role="switch"
          aria-checked={form.use_saved_search}
          aria-labelledby="me-next-call"
          onClick={() => set({ use_saved_search: !form.use_saved_search })}
          className="-mr-1 grid h-11 w-[60px] place-items-center rounded-full"
        >
          <span aria-hidden className={`switch ${form.use_saved_search ? "is-on" : ""}`} />
        </button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-hairline pt-[18px]">
        {/* Always there, so a status never moves the form. */}
        <p
          role="status"
          aria-live="polite"
          className="flex min-h-11 items-center gap-2 text-[15px] font-medium leading-[21px]"
        >
          {status === "saving" ? (
            <span className="c-2">Saving…</span>
          ) : status === "saved" ? (
            <span className="c-real inline-flex items-center gap-2">
              <Icon name="check" size={18} />
              Saved
            </span>
          ) : status === "failed" ? (
            <span className="c-dead inline-flex items-center gap-2">
              <Icon name="circle-alert" size={18} />
              {failedWhy}
            </span>
          ) : changes > 0 ? (
            <span className="c-2 tnum">
              {changes} {changes === 1 ? "change" : "changes"}
            </span>
          ) : (
            <span className="c-2">Every change is checked as you save</span>
          )}
        </p>
        <div className="ml-auto flex items-center gap-1">
          {dirty && status !== "saving" && (
            <button type="button" onClick={undo} className="btn btn-quiet hover:bg-[var(--color-accent-tint)]">
              Undo changes
            </button>
          )}
          <button type="submit" disabled={!dirty || status === "saving"} className="btn btn-primary disabled:opacity-45">
            Save
          </button>
        </div>
      </div>
    </form>
  );
}

function Labelled({
  label,
  hint,
  error,
  wide = false,
  children,
}: {
  label: string;
  hint: string; // an error takes the hint's line, so showing one never moves the form
  error?: string;
  wide?: boolean;
  children: (id: string, describedBy: string) => ReactNode;
}) {
  const id = useId();
  const described = `${id}-note`;
  return (
    <div className={`field ${wide ? "md:col-span-2" : ""}`}>
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      {children(id, described)}
      <p id={described} className={`hint ${error ? "is-error" : ""}`}>
        {error ?? hint}
      </p>
    </div>
  );
}

/** One choice from a few, as the kit's chips (Forms, W4): a radio group the keyboard reaches chip by chip. */
function Chips({ label, error, children }: { label: string; error?: string; children: ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <p id={id} className="field-label">
        {label}
      </p>
      <div role="radiogroup" aria-labelledby={id} className="reason-chips">
        {children}
      </div>
      <p className={`hint ${error ? "is-error" : ""}`}>{error ?? ""}</p>
    </div>
  );
}
