import { describe, it, expect } from 'vitest';

import enUS from '../locales/en-US.json' with { type: 'json' };
import jaJP from '../locales/ja-JP.json' with { type: 'json' };
import ruRU from '../locales/ru-RU.json' with { type: 'json' };
import { FACEPLATE_INVARIANT_KEYS } from '../faceplate-invariant-keys';

// MOR-2715: every catalog key present in the en-US source of truth must
// also be present in every other shipped locale, so no locale silently
// falls back to English for operator-visible sentences. This is a
// structural key-equality check; value parity (faceplate vocabulary) and
// placeholder/glossary lint are enforced by faceplate-invariant.test.ts
// and frontend/scripts/i18n-check.mjs respectively.

const enUSCatalog = enUS as unknown as Record<string, string>;

const CATALOGS: Record<string, Record<string, string>> = {
  'ja-JP': jaJP as unknown as Record<string, string>,
  'ru-RU': ruRU as unknown as Record<string, string>,
};

// Faceplate-invariant keys may be OMITTED from a non-English catalog:
// the en-US fallback renders the identical string, which is the
// documented handling in faceplate-invariant.test.ts.
const INVARIANT_KEYS = new Set(FACEPLATE_INVARIANT_KEYS);

// Known untranslated gaps, listed per locale so each one is visible.
// MOR-2717 translated the last of them, so every locale list is empty.
// An entry may return only together with the key that needs it.
const KNOWN_GAPS: Record<string, string[]> = {
  'ja-JP': [],
  'ru-RU': [],
};

// A listed gap that has since been translated (or no longer exists in
// en-US) is stale and must be removed from KNOWN_GAPS.
const STALE_GAPS = (locale: string): string[] =>
  KNOWN_GAPS[locale].filter(
    (key) => key in CATALOGS[locale] || !(key in enUSCatalog),
  );

describe('locale catalog parity (MOR-2715)', () => {
  it('the en-US source of truth is non-empty', () => {
    expect(Object.keys(enUSCatalog).length).toBeGreaterThan(0);
  });

  for (const [locale, catalog] of Object.entries(CATALOGS)) {
    it(`${locale} covers every en-US key outside the documented gaps`, () => {
      const exempt = new Set([...KNOWN_GAPS[locale], ...INVARIANT_KEYS]);
      const missing = Object.keys(enUSCatalog)
        .filter((key) => key !== '$schema')
        .filter((key) => !(key in catalog))
        .filter((key) => !exempt.has(key));
      expect(missing).toEqual([]);
    });

    it(`${locale} has no stale entries in its known-gaps list`, () => {
      expect(STALE_GAPS(locale)).toEqual([]);
    });
  }
});
