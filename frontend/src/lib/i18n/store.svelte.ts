/**
 * Svelte 5 reactive locale store + `localStorage` persistence.
 *
 * Convention matches the rest of `$lib/stores/*.svelte.ts`: top-level
 * `$state` rune, getter + setter functions, no class wrappers.
 *
 * Precedence (per glossary §5.4):
 *   1. Explicit user selection (persisted to `rigplane.i18n.locale`).
 *   2. OS / browser `navigator.language`, narrowed to a supported locale.
 *   3. en-US fallback.
 *
 * The pseudo-locale `qps-ploc` is selectable only when a developer has
 * opted in (either by explicit `setLocale('qps-ploc')` or by stamping the
 * storage value manually) — `navigator.language` should never produce it.
 */

import type { LocaleCode } from './types';
import { PSEUDO_LOCALE, SOURCE_LOCALE } from './types';

const STORAGE_KEY = 'rigplane.i18n.locale';

const SUPPORTED_LOCALES: readonly LocaleCode[] = ['en-US', 'ja-JP', 'ru-RU', 'qps-ploc'];

function isSupported(value: string): value is LocaleCode {
  return (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

/**
 * Detect from `navigator.language` / `navigator.languages`. Pseudo-locale
 * is never returned from detection.
 */
function detectFromBrowser(): LocaleCode {
  if (typeof navigator === 'undefined') return SOURCE_LOCALE;
  const candidates: string[] = [];
  // Prefer `languages` (ordered list) when available, fall back to single.
  const langs = (navigator as Navigator & { languages?: readonly string[] }).languages;
  if (Array.isArray(langs)) candidates.push(...langs);
  if (typeof navigator.language === 'string') candidates.push(navigator.language);

  for (const raw of candidates) {
    // Exact match first.
    if (isSupported(raw) && raw !== PSEUDO_LOCALE) return raw;
    // Language-tag match (e.g. "ja" -> "ja-JP").
    const lang = raw.split('-')[0]?.toLowerCase();
    for (const supported of SUPPORTED_LOCALES) {
      if (supported === PSEUDO_LOCALE) continue;
      if (supported.split('-')[0].toLowerCase() === lang) return supported;
    }
  }
  return SOURCE_LOCALE;
}

function readStoredLocale(): LocaleCode | null {
  if (typeof localStorage === 'undefined') return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw && isSupported(raw)) return raw;
  } catch {
    /* test envs may stub localStorage; treat as unset */
  }
  return null;
}

function initialLocale(): LocaleCode {
  return readStoredLocale() ?? detectFromBrowser();
}

let locale = $state<LocaleCode>(initialLocale());

export function getLocale(): LocaleCode {
  return locale;
}

export function setLocale(next: LocaleCode): void {
  if (!isSupported(next)) return;
  locale = next;
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* persistence is best-effort */
  }
}

/**
 * Forget the explicit user choice and re-derive from the browser.  Used by
 * tests; not exposed in the UI (Settings always writes an explicit value).
 */
export function _resetLocale(): void {
  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }
  locale = detectFromBrowser();
}

/**
 * Set the active locale WITHOUT persisting to `rigplane.i18n.locale`.
 *
 * Reserved for the cross-app locale preference contract
 * (`locale-contract.ts`, RP-ML-012A). The contract module reads a Pro-side
 * hint (URL query or shared `localStorage` envelope) on boot and applies it
 * here so the Pro setting can override the existing Core stored value
 * without stomping it permanently — the next standalone launch without a
 * Pro hint will fall back to the user's stable Core preference.
 *
 * Not part of the public UI surface; do not call from components.
 */
export function _applyExternalLocale(next: LocaleCode): void {
  if (!isSupported(next)) return;
  locale = next;
}

export { SUPPORTED_LOCALES, STORAGE_KEY, isSupported };
