// Finding a critic's quote in the idea's text, the way the panel's own check
// does (src/skeptic/checks.py): case, spacing, quote marks, Markdown emphasis
// and the kind of dash are ignored. Returns positions in the original text.

const DROPPED = new Set(["'", '"', "‘", "’", "‚", "‛", "“", "”", "„", "«", "»", "*", "_", "`"]);
const DASHES = new Set(["‐", "‑", "‒", "–", "—", "−"]);

interface Normalised {
  text: string;
  /** For each character of `text`, its index in the original. */
  map: number[];
}

function normalise(source: string): Normalised {
  let text = "";
  const map: number[] = [];
  let lastWasSpace = true;
  for (let i = 0; i < source.length; i++) {
    let ch = source[i] as string;
    if (DROPPED.has(ch)) continue;
    if (DASHES.has(ch)) ch = "-";
    if (ch === "…") {
      for (let k = 0; k < 3; k++) {
        text += ".";
        map.push(i);
      }
      lastWasSpace = false;
      continue;
    }
    if (/\s/.test(ch)) {
      if (lastWasSpace) continue;
      text += " ";
      map.push(i);
      lastWasSpace = true;
      continue;
    }
    // Lowercasing can lengthen a character (İ becomes i plus a combining dot);
    // every unit it produces maps back to the same original index.
    const lower = ch.toLowerCase();
    text += lower;
    for (let k = 0; k < lower.length; k++) map.push(i);
    lastWasSpace = false;
  }
  return { text, map };
}

export interface Span {
  start: number;
  end: number; // exclusive
}

/** Where a quote sits in the idea, or null when it is not there word for word. */
export function findQuote(idea: string, quote: string): Span | null {
  const haystack = normalise(idea);
  const needle = normalise(quote).text.replace(/^[\s.,:;]+|[\s.,:;]+$/g, "");
  if (!needle) return null;
  let from = 0;
  while (from <= haystack.text.length) {
    const at = haystack.text.indexOf(needle, from);
    if (at < 0) return null;
    const before = haystack.text[at - 1];
    const after = haystack.text[at + needle.length];
    const isWord = (c: string | undefined) => c !== undefined && /[\p{L}\p{N}_]/u.test(c);
    if (!isWord(before) && !isWord(after)) {
      const start = haystack.map[at] ?? 0;
      const last = haystack.map[at + needle.length - 1] ?? start;
      return { start, end: last + 1 };
    }
    from = at + 1;
  }
  return null;
}
