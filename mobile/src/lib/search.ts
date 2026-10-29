/**
 * "My search" (U-13d): what the renter typed, checked here first, and only what changed sent to PATCH
 * /api/me/profile (U-12a), which checks every field again in code. The same checks, limits and words as the
 * web's form on /me (web/lib/searchForm.ts, U-12b): keep the two in step.
 */

/** What Landline may tell listing agents about the renter. */
export const SHAREABLE = ["name", "move_in", "occupants", "pets"] as const;
export type Shareable = (typeof SHAREABLE)[number];

/** GET or PATCH /api/me/profile (U-12a): the renter's profile. */
export type MyProfile = {
  phone: string;
  email?: string | null;
  use_saved_search: boolean;
  shareable: string[];
  preferences: {
    max_monthly?: number | null;
    min_beds?: number | null;
    areas?: string[];
    pets?: string[];
    must_have?: string[];
    move_in?: string | null;
  };
  updated_at?: string | null;
};

/** The saved values the form starts from. */
export type Saved = {
  max_monthly?: number | null;
  min_beds?: number | null;
  areas: string[];
  pets: string[];
  must_have: string[];
  move_in?: string | null;
  email?: string | null;
  shareable: Shareable[];
  use_saved_search: boolean;
};

/** The form as typed: text for the free fields, so a half-typed value never jumps. */
export type Form = {
  budget: string;
  beds: string; // "" (any) or "0" to "5"
  areas: string;
  pets: string;
  must_have: string;
  move_in: string; // YYYY-MM-DD or ""
  email: string;
  shareable: Shareable[];
  use_saved_search: boolean;
};

/** The edit PATCH /api/me/profile gets: only what changed. A cleared value is null or []. */
export type Edit = Partial<{
  max_monthly: number | null;
  min_beds: number | null;
  areas: string[];
  pets: string[];
  must_have: string[];
  move_in: string | null;
  email: string | null;
  shareable: Shareable[];
  use_saved_search: boolean;
}>;

export type Field = keyof Form;
export type Problems = Partial<Record<Field, string>>;

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function savedFrom(p: MyProfile): Saved {
  const prefs = p.preferences ?? {};
  return {
    max_monthly: prefs.max_monthly ?? null,
    min_beds: prefs.min_beds ?? null,
    areas: prefs.areas ?? [],
    pets: prefs.pets ?? [],
    must_have: prefs.must_have ?? [],
    move_in: prefs.move_in ?? null,
    email: p.email ?? null,
    shareable: SHAREABLE.filter((s) => p.shareable.includes(s)),
    use_saved_search: p.use_saved_search,
  };
}

export function formFrom(saved: Saved): Form {
  return {
    budget: saved.max_monthly != null ? String(saved.max_monthly) : "",
    beds: saved.min_beds != null ? String(saved.min_beds) : "",
    areas: saved.areas.join(", "),
    pets: saved.pets.join(", "),
    must_have: saved.must_have.join(", "),
    move_in: saved.move_in && ISO_DATE.test(saved.move_in) ? saved.move_in : "", // words ("December 1") aren't a date
    email: saved.email ?? "",
    shareable: [...saved.shareable],
    use_saved_search: saved.use_saved_search,
  };
}

/** "$2,600" → 2600; "" → null; anything that isn't a whole number of dollars → undefined. */
export function parseBudget(text: string): number | null | undefined {
  const t = text.replace(/[$,\s]/g, "");
  if (!t) return null;
  return /^\d{1,6}$/.test(t) ? Number(t) : undefined;
}

/** "Annex, Leslieville ," → ["Annex", "Leslieville"]. */
export const splitList = (text: string): string[] =>
  text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

const realDate = (iso: string): boolean => {
  if (!ISO_DATE.test(iso)) return false;
  const d = new Date(`${iso}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === iso;
};

// The limits U-12a checks in code, so the form says so before sending.
const LISTS: [Field, number, number][] = [["areas", 10, 80], ["pets", 10, 40], ["must_have", 20, 60]];

/** What's wrong with the form, by field; empty when it can be sent. */
export function problems(form: Form): Problems {
  const out: Problems = {};
  const budget = parseBudget(form.budget);
  if (budget === undefined || (budget != null && (budget < 1 || budget > 20000))) {
    out.budget = "Use a whole number of dollars, from 1 to 20,000.";
  }
  for (const [field, most, longest] of LISTS) {
    const items = splitList(form[field] as string);
    if (items.length > most) out[field] = `Up to ${most}, separated by commas.`;
    else if (items.some((x) => x.length > longest)) out[field] = `Keep each one under ${longest} characters.`;
  }
  if (form.move_in && !realDate(form.move_in)) out.move_in = "That isn't a date.";
  if (form.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) {
    out.email = "That isn't an email address.";
  }
  return out;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Only what changed from the saved values; {} when nothing did. Call it after `problems` is empty. */
export function edit(form: Form, saved: Saved): Edit {
  const now: Required<Edit> = {
    max_monthly: parseBudget(form.budget) ?? null,
    min_beds: form.beds === "" ? null : Number(form.beds),
    areas: splitList(form.areas),
    pets: splitList(form.pets),
    must_have: splitList(form.must_have),
    move_in: form.move_in || null,
    email: form.email.trim() || null,
    shareable: SHAREABLE.filter((s) => form.shareable.includes(s)),
    use_saved_search: form.use_saved_search,
  };
  const before: Required<Edit> = {
    max_monthly: saved.max_monthly ?? null,
    min_beds: saved.min_beds ?? null,
    areas: saved.areas,
    pets: saved.pets,
    must_have: saved.must_have,
    move_in: saved.move_in && ISO_DATE.test(saved.move_in) ? saved.move_in : null,
    email: saved.email ?? null,
    shareable: SHAREABLE.filter((s) => saved.shareable.includes(s)),
    use_saved_search: saved.use_saved_search,
  };
  const out: Edit = {};
  for (const k of Object.keys(now) as (keyof Edit)[]) {
    if (!same(now[k], before[k])) (out as Record<string, unknown>)[k] = now[k];
  }
  return out;
}

/** A server message in the form's words: Pydantic's "Value error, " prefix dropped, the first letter capitalised. */
function said(msg: string | undefined): string {
  const words = (msg ?? "").replace(/^value error,\s*/i, "").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "That isn't valid.";
}

/** Bedrooms, the fewest the renter would take: the web's range (web/components/MySearch.tsx, up to 5, which is
 *  the API's limit), in the app's short words. A saved value between them (1.5) gets its own chip. */
export function bedOptions(current: string): (readonly [string, string])[] {
  const base: (readonly [string, string])[] = [
    ["", "Any"], ["0", "Studio+"], ["1", "1+"], ["2", "2+"], ["3", "3+"], ["4", "4+"], ["5", "5+"],
  ];
  return base.some(([v]) => v === current) ? base : [...base, [current, `${current}+`]];
}

/** A form moved onto new saved values: each field the renter changed from `before` (what the form started from,
 *  or what a save sent) keeps what they typed, and every other field takes `after`. So a reload never sends old
 *  values back over what a call just learned, and a save keeps what was typed while it ran. */
export function rebase(typed: Form, before: Form, after: Form): Form {
  const out: Form = { ...after };
  for (const k of Object.keys(after) as Field[]) {
    if (!same(typed[k], before[k])) (out as Record<string, unknown>)[k] = typed[k];
  }
  return out;
}

// The server's names for the form's two renamed fields.
const FROM_API: Record<string, Field> = { max_monthly: "budget", min_beds: "beds" };

/** A 422 from PATCH /api/me/profile ({detail: [{loc: ["body", field, …], msg}]}) as the form's problems. */
export function problemsFrom422(body: unknown): Problems {
  const out: Problems = {};
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(detail)) return out;
  for (const d of detail as { loc?: unknown[]; msg?: string }[]) {
    const name = typeof d.loc?.[1] === "string" ? (d.loc[1] as string) : null;
    const field = name ? FROM_API[name] ?? (name as Field) : null;
    if (field && !out[field]) out[field] = said(d.msg);
  }
  return out;
}
