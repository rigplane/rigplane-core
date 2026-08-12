import { describe, it, expect } from 'vitest';

import enUS from '../locales/en-US.json' with { type: 'json' };
import jaJP from '../locales/ja-JP.json' with { type: 'json' };
import ruRU from '../locales/ru-RU.json' with { type: 'json' };
import { FACEPLATE_INVARIANT_KEYS } from '../faceplate-invariant-keys';

// MOR-1450: instrument/faceplate vocabulary is locale-invariant (owner
// ruling, walkthrough scenario 10). This is a structural key-equality
// check, not a regex scan: a locale catalog fails the moment any key in
// FACEPLATE_INVARIANT_KEYS diverges from the en-US source, so a future PR
// that translates a faceplate readout (e.g. "SPLIT" -> "СПЛИТ", or
// "OFF" -> "Выключено") turns this test red.
//
// A catalog is also allowed to simply omit an invariant key — the runtime
// falls back to en-US, which trivially satisfies the invariant. Only an
// EXPLICIT divergent value is a failure.

const enUSCatalog = enUS as unknown as Record<string, string>;

const CATALOGS: Record<string, Record<string, string>> = {
  'ja-JP': jaJP as unknown as Record<string, string>,
  'ru-RU': ruRU as unknown as Record<string, string>,
};

describe('faceplate locale invariance (MOR-1450)', () => {
  it('every invariant key exists in the en-US source catalog', () => {
    for (const key of FACEPLATE_INVARIANT_KEYS) {
      expect(enUSCatalog).toHaveProperty(key);
    }
  });

  for (const [locale, catalog] of Object.entries(CATALOGS)) {
    it(`${locale} matches en-US verbatim for every faceplate-invariant key`, () => {
      for (const key of FACEPLATE_INVARIANT_KEYS) {
        const sourceValue = enUSCatalog[key];
        if (!(key in catalog)) continue; // omission falls back to en-US
        expect(
          catalog[key],
          `${locale}["${key}"] must equal the en-US value verbatim ` +
            `(faceplate vocabulary does not translate). ` +
            `en-US: ${JSON.stringify(sourceValue)}, ${locale}: ${JSON.stringify(catalog[key])}`,
        ).toBe(sourceValue);
      }
    });
  }
});
