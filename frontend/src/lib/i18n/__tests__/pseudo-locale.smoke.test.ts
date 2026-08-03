/**
 * Pseudo-locale (`qps-ploc`) smoke test (RP-ML-013A).
 *
 * Renders a handful of representative UI keys through the real runtime
 * (`t()` / `tPlural()` / `messageFromReasonCode()`) under the pseudo-locale
 * and asserts the contract documented in `pseudo.ts`:
 *
 *   - Every rendered string is non-empty and bracketed `⟦…⟧`.
 *   - The transformed string expands the substitutable text by >=35%.
 *   - `{placeholder}` runs survive the transform, and the values
 *     substituted at the call site (brand names, radio model identifiers,
 *     frequencies) are NOT diacritic-substituted. This is how the runtime
 *     preserves glossary tokens under pseudo: callers compose them as
 *     params, not as bare substrings of the source template
 *     (see `pseudo.ts` header comment "Glossary tokens and protocol
 *     values are preserved by composing them at the call site").
 *
 * Scope split with RP-ML-006: this smoke test does NOT take screenshots
 * or render Svelte components. Visual pilot-locale QA stays in
 * RP-ML-006's Playwright pack. This file is the cheap CI floor that
 * proves the pseudo-locale path works end-to-end through the runtime.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import { t, tPlural, messageFromReasonCode, setLocale } from '../index';
import { _resetLocale } from '../store.svelte';

const SOURCE = {
  'common.action.save': 'Save',
  'core.statusbar.connection.connectingTo':
    'Connecting to {radio} on {transport}…',
  'core.diagnostics.attachedFiles.other': '{count} files attached',
  'core.toast.licenseExpired':
    'Your license has expired. Reactivate to continue.',
  'core.settings.language.helpText':
    'Choose the language used by the RigPlane UI. Radio mode codes such as {modeToken} stay in English regardless of locale.',
};

function stripBrackets(value: string): string {
  // pseudoize() guarantees `⟦` prefix and `⟧` suffix. Strip the brackets
  // so we can inspect the inner transformed payload.
  return value.replace(/^⟦/, '').replace(/⟧$/, '');
}

/**
 * Compute the length of the substitutable-text portion of a source
 * template — everything except `{placeholder}` runs and `'{'` literal
 * brace escapes. This is the budget the pseudoize() expansion ratio is
 * computed against (see `pseudo.ts#pseudoize`).
 */
function substitutableLength(source: string): number {
  return source
    .replace(/'\{'/g, '')
    .replace(/\{[a-zA-Z][a-zA-Z0-9]*\}/g, '').length;
}

beforeEach(() => {
  localStorage.clear();
  _resetLocale();
  setLocale('qps-ploc');
});

afterEach(() => {
  localStorage.clear();
  // The i18n store is a module singleton and the fast pool runs with
  // ``isolate: false`` (issue #771), so a pseudo-locale left set here leaks
  // into every later fast-pool file — any test asserting on real English text
  // then sees ``⟦…⟧`` instead. Hand the locale back on the way out.
  _resetLocale();
});

describe('pseudo-locale smoke (representative UI keys)', () => {
  it('every representative t() call returns a non-empty bracketed string', () => {
    for (const key of Object.keys(SOURCE)) {
      const rendered = t(key, { radio: 'IC-7610', transport: 'LAN', modeToken: 'USB' });
      expect(rendered.length).toBeGreaterThan(0);
      expect(rendered.startsWith('⟦')).toBe(true);
      expect(rendered.endsWith('⟧')).toBe(true);
    }
  });

  it('expands the substitutable text of every representative string by at least 35%', () => {
    for (const [key, source] of Object.entries(SOURCE)) {
      const rendered = t(key, { radio: 'IC-7610', transport: 'LAN', modeToken: 'USB' });
      const inner = stripBrackets(rendered);
      const budgetSource = substitutableLength(source);
      // The transformed payload includes the diacritic-substituted text
      // (same character count as source substitutable text) plus
      // padding sized for >=35% expansion. We assert against that
      // budget — using the full template length would penalise strings
      // that are mostly placeholders.
      expect(inner.length).toBeGreaterThanOrEqual(Math.ceil(budgetSource * 1.35));
    }
  });

  it('preserves brand and radio-model interpolation values verbatim', () => {
    const rendered = t('core.statusbar.connection.connectingTo', {
      radio: 'IC-7610',
      transport: 'LAN',
    });
    expect(rendered).toContain('IC-7610');
    expect(rendered).toContain('LAN');
    // Sanity: the surrounding prose IS diacritic-substituted, so the
    // raw English "Connecting" should not appear.
    expect(rendered).not.toContain('Connecting');
  });

  it('keeps glossary-style tokens injected via params verbatim under pseudo', () => {
    // Documented contract (pseudo.ts header): glossary tokens and
    // protocol values are preserved by composing them at the call site.
    // We pass a radio-abbreviation token (`USB`) through the `modeToken`
    // placeholder and assert it lands in the rendered output unchanged.
    const rendered = t('core.settings.language.helpText', { modeToken: 'USB' });
    expect(rendered).toContain('USB');
    // The surrounding English prose IS diacritic-substituted under
    // pseudo (this is the layout-stress signal). Confirm by checking
    // the raw English source has been rewritten.
    expect(rendered).not.toContain('Choose the language');
  });

  it('tPlural() renders bracketed and keeps the count interpolation', () => {
    const rendered = tPlural('core.diagnostics.attachedFiles', 5);
    expect(rendered.startsWith('⟦')).toBe(true);
    expect(rendered.endsWith('⟧')).toBe(true);
    expect(rendered).toContain('5');
  });

  it('messageFromReasonCode() renders bracketed for a known code', () => {
    const rendered = messageFromReasonCode('licenseExpired');
    expect(rendered.startsWith('⟦')).toBe(true);
    expect(rendered.endsWith('⟧')).toBe(true);
    // Pseudo transform must have rewritten the English source.
    expect(rendered).not.toContain('Your license has expired.');
  });
});
