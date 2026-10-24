/**
 * The line a returning renter's call opens with ("Welcome back, Sara. I booked …"), split for the panel:
 * the greeting, then what the last calls found. With the renter's name known, the greeting is matched
 * whole, so a period inside the name ("Dr. Sara") doesn't end it; with no greeting to match, there's no news
 * rather than a sentence cut in two.
 */
export function splitWelcome(opening: string, name: string | null | undefined): { greeting: string; news: string } {
  const expected = `Welcome back${name ? `, ${name}` : ""}.`;
  const greeting = opening.startsWith(expected) ? expected : /^Welcome back[^.!?]*[.!?]/i.exec(opening)?.[0];
  if (!greeting) return { greeting: expected, news: "" };
  return { greeting, news: opening.slice(greeting.length).trim() };
}
