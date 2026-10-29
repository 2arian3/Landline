/**
 * A phone number as typed, in E.164 for Clerk: "(416) 555-0100" and "416 555 0100" become "+14165550100".
 * Ten digits are North American; anything else needs its own "+" and country code. Null when it can't be one.
 */
export function toE164(typed: string): string | null {
  const digits = typed.replace(/\D/g, "");
  if (typed.trim().startsWith("+")) return digits.length >= 8 && digits.length <= 15 ? `+${digits}` : null;
  if (digits.length === 10) return `+1${digits}`;
  if (digits.length === 11 && digits.startsWith("1")) return `+${digits}`;
  return null;
}

/**
 * A North American number as the renter types it, grouped as they go: "4165550" reads "416 555 0", and ten digits
 * "416 555 0100". A leading "1" is kept apart ("1 416 555 0100"). A number typed with its own "+" is left alone.
 */
export function grouped(typed: string): string {
  if (typed.trim().startsWith("+")) return typed;
  const all = typed.replace(/\D/g, "");
  const one = all.length > 10 && all.startsWith("1");
  const d = (one ? all.slice(1) : all).slice(0, 10);
  const parts = [d.slice(0, 3), d.slice(3, 6), d.slice(6)].filter(Boolean);
  return (one ? "1 " : "") + parts.join(" ");
}
