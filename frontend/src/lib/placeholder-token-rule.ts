/**
 * MOR-2677 — the placeholder-token rule for the whole-page placeholder guard
 * (MOR-2651 part 2). A PURE module: no DOM, no Svelte, no runtime callers —
 * its only callers are this module's vitest suite and the Playwright page
 * scan (`tests/e2e/placeholder-guard/page-scan.spec.ts`). It lives under
 * `src/lib/` solely because vitest's `fast` project includes only
 * `src/**\/*.test.ts` (`vite.config.ts`); no production module imports it, so
 * there is no behaviour change to the app.
 *
 * The rule (MOR-2677, "Token rule"):
 *
 *  1. Dash placeholder — the whole string is a dash run, optionally followed
 *     by one unit word, optionally followed by another dash run. Matches
 *     "—", "---", "--- Hz", "-------", "-- empty --". A dash inside a
 *     sentence does not match.
 *  2. Question token — a whitespace-delimited token that is exactly "?" (or
 *     "?" with trailing punctuation). "ATU: ?" hits; "Turn OFF the radio?"
 *     does not.
 *  3. Word tokens — standalone, case-insensitive "unknown", "n/a", "nan",
 *     "null", "undefined".
 *  4. Title allowance — rules 2–3 are skipped for a `title` that is a
 *     sentence of three words or more (the owner's disabled-reason
 *     allowance). Rule 1 always applies.
 */

export type PlaceholderTokenKind =
  | 'dash-run'
  | 'question-token'
  | 'placeholder-word';

export interface PlaceholderToken {
  readonly kind: PlaceholderTokenKind;
  /** The offending string: the whole string for a dash run, else the token. */
  readonly token: string;
}

export interface FindPlaceholderTokensOptions {
  /**
   * `true` for a `title` attribute value: rules 2–3 are skipped when the
   * value is a sentence of three words or more (the disabled-reason
   * allowance). Rule 1 (dash run) always applies.
   */
  readonly isTitle?: boolean;
}

const DASH_PLACEHOLDER = /^\s*[-–—]+(\s*[A-Za-zµ%]+\s*)?\s*[-–—]*\s*$/;
const QUESTION_TOKEN = /^\?[.,;:!?]*$/;
const PLACEHOLDER_WORDS: ReadonlySet<string> = new Set([
  'unknown', 'n/a', 'nan', 'null', 'undefined',
]);

export function findPlaceholderTokens(
  text: string,
  options: FindPlaceholderTokensOptions = {},
): PlaceholderToken[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  if (DASH_PLACEHOLDER.test(trimmed)) {
    return [{ kind: 'dash-run', token: trimmed }];
  }
  if (options.isTitle === true && trimmed.split(/\s+/).length >= 3) {
    return [];
  }
  const hits: PlaceholderToken[] = [];
  for (const token of trimmed.split(/\s+/)) {
    if (QUESTION_TOKEN.test(token)) {
      hits.push({ kind: 'question-token', token });
    } else if (PLACEHOLDER_WORDS.has(token.toLowerCase())) {
      hits.push({ kind: 'placeholder-word', token });
    }
  }
  return hits;
}
