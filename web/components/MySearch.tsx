"use client";

import { type ReactNode, useEffect, useId, useState } from "react";
import { isMeRefusal, type MeRefusal } from "@/lib/me";
import { loadProfile, saveProfile } from "@/lib/profile";
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
} from "@/lib/searchForm";

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

const control =
  "mt-1.5 block min-h-11 w-full rounded-[var(--radius-control)] bg-ground px-3 text-support text-ink outline-none placeholder:text-ink-3 focus-visible:outline-2 focus-visible:outline-ink";
const hairline = { boxShadow: "0 0 0 1px var(--color-hair)" };

/**
 * "My search" on /me (U-12b): the renter edits the search Landline keeps, chooses what it may tell listing
 * agents, and whether the next call starts from this search. Only what changed is sent (PATCH
 * /api/me/profile); the API checks every field again. The status line keeps its place, so nothing shifts.
 */
export function MySearch({ getToken }: { getToken: () => Promise<string | null> }) {
  const [saved, setSaved] = useState<MyProfile | MeRefusal | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [wrong, setWrong] = useState<Problems>({});
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");

  useEffect(() => {
    const ctl = new AbortController();
    void (async () => {
      const token = await getToken();
      let got: MyProfile | MeRefusal;
      try {
        got = token ? await loadProfile(token, ctl.signal) : "signin";
      } catch {
        return; // left the page
      }
      setSaved(got);
      if (!isMeRefusal(got)) setForm(formFrom(savedFrom(got)));
    })();
    return () => ctl.abort();
    // once per page: getToken comes from the session
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (saved === null) return <p className="text-support text-ink-3">Loading…</p>;
  if (isMeRefusal(saved) || !form) {
    return <p className="text-support text-ink-2">Can&rsquo;t load your search right now.</p>;
  }

  const change = edit(form, savedFrom(saved));
  const dirty = Object.keys(change).length > 0;
  const set = (patch: Partial<Form>) => {
    setForm({ ...form, ...patch });
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
    const token = await getToken();
    const result = token ? await saveProfile(token, change) : { refused: "signin" as const };
    if ("profile" in result) {
      setSaved(result.profile);
      setForm(formFrom(savedFrom(result.profile)));
      setWrong({});
      setStatus("saved");
    } else {
      setWrong("problems" in result ? result.problems : {});
      setStatus("failed");
    }
  };

  const failedWhy = Object.keys(wrong).length
    ? "Check the fields marked above."
    : "Couldn't save. Try again in a moment.";

  return (
    <form
      className="space-y-4"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty && status !== "saving") void save();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Labelled label="Monthly budget" hint="Whole dollars a month" error={wrong.budget}>
          {(id, described) => (
            <input
              id={id}
              aria-describedby={described}
              aria-invalid={!!wrong.budget}
              inputMode="numeric"
              placeholder="2600"
              value={form.budget}
              onChange={(e) => set({ budget: e.target.value })}
              className={`${control} tnum`}
              style={hairline}
            />
          )}
        </Labelled>
        <Labelled label="Bedrooms" hint="The fewest you'd take" error={wrong.beds}>
          {(id, described) => (
            <select
              id={id}
              aria-describedby={described}
              value={form.beds}
              onChange={(e) => set({ beds: e.target.value })}
              className={control}
              style={hairline}
            >
              {BEDS.map(([v, words]) => (
                <option key={v} value={v}>
                  {words}
                </option>
              ))}
            </select>
          )}
        </Labelled>
      </div>

      <Labelled label="Areas" hint="Neighbourhoods or streets, separated by commas" error={wrong.areas}>
        {(id, described) => (
          <input
            id={id}
            aria-describedby={described}
            aria-invalid={!!wrong.areas}
            placeholder="Annex, Leslieville"
            value={form.areas}
            onChange={(e) => set({ areas: e.target.value })}
            className={control}
            style={hairline}
          />
        )}
      </Labelled>

      <div className="grid gap-4 sm:grid-cols-2">
        <Labelled label="Pets" hint="Separated by commas" error={wrong.pets}>
          {(id, described) => (
            <input
              id={id}
              aria-describedby={described}
              aria-invalid={!!wrong.pets}
              placeholder="cat"
              value={form.pets}
              onChange={(e) => set({ pets: e.target.value })}
              className={control}
              style={hairline}
            />
          )}
        </Labelled>
        <Labelled label="Must-haves" hint="Separated by commas" error={wrong.must_have}>
          {(id, described) => (
            <input
              id={id}
              aria-describedby={described}
              aria-invalid={!!wrong.must_have}
              placeholder="parking, laundry"
              value={form.must_have}
              onChange={(e) => set({ must_have: e.target.value })}
              className={control}
              style={hairline}
            />
          )}
        </Labelled>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Labelled label="Move-in date" hint="The first day you could move" error={wrong.move_in}>
          {(id, described) => (
            <input
              id={id}
              type="date"
              aria-describedby={described}
              aria-invalid={!!wrong.move_in}
              value={form.move_in}
              onChange={(e) => set({ move_in: e.target.value })}
              className={control}
              style={hairline}
            />
          )}
        </Labelled>
        <Labelled label="Email" hint="Where viewing invites go" error={wrong.email}>
          {(id, described) => (
            <input
              id={id}
              type="email"
              autoComplete="email"
              aria-describedby={described}
              aria-invalid={!!wrong.email}
              placeholder="you@example.com"
              value={form.email}
              onChange={(e) => set({ email: e.target.value })}
              className={control}
              style={hairline}
            />
          )}
        </Labelled>
      </div>

      <fieldset>
        <legend className="text-meta font-label text-ink-3">What Landline may tell listing agents</legend>
        <div className="mt-1.5 grid gap-x-4 sm:grid-cols-2">
          {SHAREABLE.map((s) => (
            <label key={s} className="flex min-h-11 items-center gap-3 text-support text-ink">
              <input
                type="checkbox"
                className="h-5 w-5 accent-[var(--color-ink)]"
                checked={form.shareable.includes(s)}
                onChange={(e) =>
                  set({ shareable: e.target.checked ? [...form.shareable, s] : form.shareable.filter((x) => x !== s) })
                }
              />
              {SHARE_LABEL[s]}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="flex items-start justify-between gap-4">
        <div>
          <p id="me-next-call" className="text-support text-ink">
            Start my next call from this search
          </p>
          <p className="text-meta text-ink-3">
            {form.use_saved_search ? "On: your next call starts from it." : "Off: every call starts fresh."}
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={form.use_saved_search}
          aria-labelledby="me-next-call"
          onClick={() => set({ use_saved_search: !form.use_saved_search })}
          className="relative mt-0.5 inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
          style={{ background: form.use_saved_search ? "var(--color-ink)" : "var(--color-ink-3)" }}
        >
          <span
            aria-hidden
            className="inline-block h-5 w-5 rounded-full bg-white transition-transform"
            style={{ transform: `translateX(${form.use_saved_search ? 24 : 4}px)` }}
          />
        </button>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={!dirty || status === "saving"}
          className="min-h-11 rounded-[var(--radius-control)] bg-ink px-5 text-support font-label text-white disabled:opacity-40"
        >
          {status === "saving" ? "Saving…" : "Save"}
        </button>
        {/* Always there, so a status never moves the form. */}
        <p role="status" aria-live="polite" className="min-h-5 text-support">
          {status === "saved" && <span className="text-real">Saved.</span>}
          {status === "failed" && <span className="text-dead">{failedWhy}</span>}
        </p>
      </div>
    </form>
  );
}

function Labelled({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint: string; // an error takes the hint's line, so showing one never moves the form
  error?: string;
  children: (id: string, describedBy: string) => ReactNode;
}) {
  const id = useId();
  const described = `${id}-note`;
  return (
    <div>
      <label htmlFor={id} className="block text-meta font-label text-ink-3">
        {label}
      </label>
      {children(id, described)}
      <p id={described} className={`mt-1 text-meta ${error ? "text-dead" : "text-ink-3"}`}>
        {error ?? hint}
      </p>
    </div>
  );
}
