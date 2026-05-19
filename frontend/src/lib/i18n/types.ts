/**
 * Typed lookup helpers for the i18n catalog.
 *
 * The English source catalog (`locales/en-US.json`) is the schema authority:
 * its key set is the canonical universe of message keys, and other locales
 * are subsets that fall back to en-US for missing entries.
 *
 * `MessageKey` is derived structurally from the imported JSON so adding a key
 * to `en-US.json` immediately surfaces it as a valid argument to `t()`,
 * `tPlural()`, etc.  No `.d.ts` generation step is required for the runtime
 * to be typed — the JSON itself is the source of truth and TypeScript reads
 * it directly via `resolveJsonModule`.
 *
 * The catalog wrapper key `$schema` carries a human-readable doc comment for
 * community contributors and is stripped at lookup time, so it is excluded
 * from `MessageKey`.
 */

import enUS from './locales/en-US.json' with { type: 'json' };

/** All locale codes the bundled runtime ships with. */
export type LocaleCode = 'en-US' | 'ja-JP' | 'ru-RU' | 'qps-ploc';

/** BCP-47 source-of-truth locale. Fallback target for every other locale. */
export const SOURCE_LOCALE: LocaleCode = 'en-US';

/** Pseudo-locale tag (Microsoft convention, BCP-47 private-use). */
export const PSEUDO_LOCALE: LocaleCode = 'qps-ploc';

/**
 * Discriminated keys of the English catalog, minus the documentation wrapper
 * key `$schema`. This drives `t(key)` autocompletion.
 */
export type MessageKey = Exclude<keyof typeof enUS, '$schema'> & string;

/** Shape of a single locale file at runtime (after `$schema` is stripped). */
export type Catalog = Record<string, string>;

/** Parameters passed to {@link t} / interpolation. Values are coerced to text. */
export type MessageParams = Record<string, string | number>;

/**
 * Hook called whenever a key is missing in the selected locale AND in the
 * en-US fallback. CI tooling (RP-ML-013A) wires into this. The hook MUST be
 * side-effect-light: it is invoked during render.
 */
export type MissingKeyHandler = (info: {
  key: string;
  locale: LocaleCode;
  /** Best-effort source location. Undefined when called from runtime code. */
  source?: string;
}) => void;
