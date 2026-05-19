/**
 * Public API for the RigPlane Core i18n runtime.
 *
 * Usage from Svelte components (RP-ML-005 will adopt this; the runtime is
 * delivered independently in RP-ML-003):
 *
 *   import { t, tPlural, messageFromReasonCode, getLocale, setLocale } from '$lib/i18n';
 *   const label = t('core.statusbar.connection.connected');
 *   const status = t('core.statusbar.connection.connectingTo',
 *                    { radio: 'IC-7610', transport: 'LAN' });
 *   const files = tPlural('core.diagnostics.attachedFiles', n);
 *   const toast = messageFromReasonCode(reason.code, reason.params);
 *
 * Design constraints (non-negotiable — see strategy glossary §4, §5, §8 and
 * core-string-inventory "Notes for translators" / "Surprises"):
 *
 *   - Catalogs are plain JSON. Community contributors copy `en-US.json`,
 *     translate, and PR — no TS/Vite tooling required.
 *   - Keys are dot-separated lowerCamelCase, `[a-zA-Z0-9.]`, <=80 chars.
 *   - Placeholders are `{name}`; literal `{` is `'{'`.
 *   - Plural suffixes follow CLDR (`zero/one/two/few/many/other`).
 *   - Missing key in selected locale falls back to en-US silently in prod
 *     (reported via missing-key hook); dev shows `[missing:scope.key]`.
 *   - Glossary tokens (brand names, radio abbreviations, MHz/kHz/dB) MUST
 *     NOT be translated. The lint check is RP-ML-013A's responsibility;
 *     see `CONTRIBUTING.md` for the contract.
 *   - Server-rendered toasts (today: `web/server.py` -> `broadcast_notification`)
 *     will migrate to a reason-code-on-the-wire pattern. The frontend helper
 *     `messageFromReasonCode(code, params)` resolves `core.toast.<code>` and
 *     is the single place a translator edits a toast. Server-side migration
 *     is RP-ML-005's responsibility; this issue (RP-ML-003) lands only the
 *     frontend helper + contract.
 *
 * Performance: catalogs are imported synchronously at module init. No async
 * loading framework is used.
 */

import enUSCatalog from './locales/en-US.json' with { type: 'json' };
import jaJPCatalog from './locales/ja-JP.json' with { type: 'json' };
import ruRUCatalog from './locales/ru-RU.json' with { type: 'json' };

import { registerCatalog, resolve, setMissingKeyHandler } from './runtime';
import { resolvePlural } from './plural';
import { resolvePseudo } from './pseudo';
import { getLocale, setLocale, STORAGE_KEY, SUPPORTED_LOCALES } from './store.svelte';
import { applyContractOnBoot } from './locale-contract';
import {
  PSEUDO_LOCALE,
  SOURCE_LOCALE,
  type LocaleCode,
  type MessageKey,
  type MessageParams,
  type MissingKeyHandler,
} from './types';

// Register bundled catalogs once at module load. `qps-ploc` is computed
// on-the-fly by `resolvePseudo`; no JSON file ships for it.
registerCatalog('en-US', enUSCatalog as Record<string, unknown>);
registerCatalog('ja-JP', jaJPCatalog as Record<string, unknown>);
registerCatalog('ru-RU', ruRUCatalog as Record<string, unknown>);

// Cross-app locale preference contract (RP-ML-012A): if Pro launched this
// Core surface with a `?locale=` query or wrote the shared `proLocale.v1`
// envelope to `localStorage`, apply it once at boot. No-op when there is
// no Pro hint, so standalone Core keeps its existing precedence rules.
// See `docs/i18n/locale-contract.md` and `./locale-contract.ts`.
applyContractOnBoot();

/**
 * Resolve a message key for the active locale, applying `{name}`
 * interpolation. Falls back to en-US silently in production.
 */
export function t(key: MessageKey | string, params?: MessageParams): string {
  const locale = getLocale();
  if (locale === PSEUDO_LOCALE) {
    return resolvePseudo(key, params);
  }
  return resolve(key, locale, params);
}

/**
 * Resolve a plural-form message key. `count` is injected as a parameter
 * named `count` unless the caller passes an explicit one in `params`.
 */
export function tPlural(
  baseKey: string,
  count: number,
  params?: MessageParams,
): string {
  const locale = getLocale();
  if (locale === PSEUDO_LOCALE) {
    // Pseudo plural: pick `.one` / `.other` heuristically from en-US, run
    // through the pseudo transform. We keep this simple — CLDR for en-US.
    const category = count === 1 ? 'one' : 'other';
    const merged: MessageParams = { count, ...(params ?? {}) };
    const primary = resolvePseudo(`${baseKey}.${category}`, merged);
    if (primary.startsWith('[missing:')) {
      return resolvePseudo(`${baseKey}.other`, merged);
    }
    return primary;
  }
  return resolvePlural(baseKey, count, locale, params);
}

/**
 * Resolve a server-supplied reason code to a localized toast/dialog
 * message. The wire contract is:
 *
 *   { code: string, params?: Record<string, string | number> }
 *
 * `code` is a short reason identifier (e.g. `licenseExpired`,
 * `updateAvailable`, `readinessNoRadio`). The frontend looks up
 * `core.toast.<code>`; if the code is unknown, falls back to
 * `core.toast.unknown`.
 *
 * The server is responsible for emitting English-stable reason codes in
 * logs and on the wire; only the rendered toast is localized. See strategy
 * glossary §3.5 ("translatable inside otherwise-stable surfaces") and the
 * core-string-inventory "Surprises" note about `web/server.py` toasts.
 */
export function messageFromReasonCode(
  code: string,
  params?: MessageParams,
): string {
  // Reject obviously malformed codes so a broken server payload cannot
  // tunnel arbitrary catalog keys.
  if (!/^[a-zA-Z][a-zA-Z0-9]*$/.test(code)) {
    return t('core.toast.unknown', params);
  }
  const key = `core.toast.${code}`;
  const locale = getLocale();
  if (locale === PSEUDO_LOCALE) {
    const rendered = resolvePseudo(key, params);
    if (rendered.startsWith('[missing:')) {
      return resolvePseudo('core.toast.unknown', params);
    }
    return rendered;
  }
  const rendered = resolve(key, locale, params);
  // resolve() returns `[missing:KEY]` in dev when even en-US lacks the key.
  if (rendered === `[missing:${key}]` || rendered === key) {
    return resolve('core.toast.unknown', locale, params);
  }
  return rendered;
}

export {
  // Reactive store
  getLocale,
  setLocale,
  SUPPORTED_LOCALES,
  STORAGE_KEY,
  // Constants & types
  SOURCE_LOCALE,
  PSEUDO_LOCALE,
  // Hooks (CI / RP-ML-013A)
  setMissingKeyHandler,
};

export type { LocaleCode, MessageKey, MessageParams, MissingKeyHandler };
