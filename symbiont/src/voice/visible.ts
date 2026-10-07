/**
 * Signed content is shown exactly as signed -- never trimmed, rewritten or
 * cut -- but nothing in it may hide: control characters and invisible format
 * characters (line breaks, tabs, bidirectional overrides, zero-width joiners)
 * are drawn as visible marks next to what they do.
 */

const NAMED: Record<string, string> = {
  '\n': '⏎\n',
  '\r': '␍',
  '\t': '⇥',
  '\u007f': '␡',
};

/** At most this many characters of signed content on one card. */
export const MAX_QUOTE = 4000;

function mark(ch: string): string {
  const cp = ch.codePointAt(0)!;
  if (NAMED[ch] !== undefined) return NAMED[ch];
  if (cp < 0x20) return String.fromCodePoint(0x2400 + cp); // ␀ .. ␟
  return `⟨U+${cp.toString(16).toUpperCase().padStart(4, '0')}⟩`;
}

/** `text` with every \p{Cc} and \p{Cf} character made visible. */
export function visible(text: string): string {
  return text.replace(/[\p{Cc}\p{Cf}]/gu, mark);
}

/** Whether `text` holds anything that {@link visible} had to mark. */
export function hasHidden(text: string): boolean {
  return /\p{Cf}|(?![\n\t])\p{Cc}/u.test(text);
}
