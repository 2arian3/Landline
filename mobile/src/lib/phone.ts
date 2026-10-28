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
