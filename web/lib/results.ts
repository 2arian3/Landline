/**
 * The results text as one line per place, for the panel: a sentence that names no other place stays with
 * the one before it ("It moves up to first.", "Its agent asked, …"), and the booking leads, the way Landline
 * says it ("I booked …"). `addresses` are the board's: they say which place a sentence is about.
 */
export function resultsByPlace(results: string, addresses: string[]): string[] {
  // The longest address first, so a sentence about "112 King St W" isn't put with "12 King St W".
  const places = [...new Set(addresses)].sort((a, b) => b.length - a.length);
  const lines: { text: string; place?: string }[] = [];
  for (const sentence of sentences(results)) {
    const place = places.find((a) => names(sentence, a));
    const last = lines.at(-1);
    if (last && (!place || place === last.place)) last.text += ` ${sentence}`;
    else lines.push({ text: sentence, place });
  }
  // Only the booking itself leads, not any line with "booked" in it ("fully booked this week").
  const booking = (l: { text: string }) => Number(l.text.startsWith("I booked "));
  return lines.sort((a, b) => booking(b) - booking(a)).map((l) => l.text);
}

/** Whether `sentence` names `address` whole: "12 King St W" isn't named in "112 King St W". */
function names(sentence: string, address: string): boolean {
  for (let at = sentence.indexOf(address); at >= 0; at = sentence.indexOf(address, at + 1)) {
    const around = (sentence[at - 1] ?? " ") + (sentence[at + address.length] ?? " ");
    if (!/[0-9A-Za-z]/.test(around)) return true;
  }
  return false;
}

/**
 * The text's sentences, not split after "St." ("64 St. Clair Avenue West"). No lookbehind: Safari before
 * 16.4 can't parse one, and the whole panel would fail to load.
 */
function sentences(text: string): string[] {
  const out: string[] = [];
  let start = 0;
  for (const m of text.matchAll(/[.!?]\s+(?=[A-Z0-9“"])/g)) {
    const end = m.index + 1;
    if (/\bSt\.$/.test(text.slice(start, end))) continue;
    out.push(text.slice(start, end));
    start = m.index + m[0].length;
  }
  out.push(text.slice(start));
  return out.map((s) => s.trim()).filter(Boolean);
}
