/**
 * MOR-1072 registry: the two frozen family IDs (`studioline`/`fieldline`,
 * MOR-977 §4.6) are registered as declarations, and the registry does not
 * hardcode a family count — a third, hypothetical family registers and
 * validates the same way. MOR-1073 gave studioline its renderers; fieldline
 * is still renderer-less, which keeps the "zero renderers declared" fallback
 * path under test with a real declaration rather than only a fixture.
 */
import { describe, it, expect } from 'vitest';
import {
  listDesignLanguageIds, getDesignLanguage, registerDesignLanguage, RENDERER_SLOT_NAMES,
} from '../contract';
import { studioline, fieldline } from '../declarations';
import { validManifest } from './fixtures';

describe('the two frozen v3 declarations', () => {
  it('registers studioline and fieldline', () => {
    expect(listDesignLanguageIds()).toEqual(expect.arrayContaining(['studioline', 'fieldline']));
    expect(getDesignLanguage('studioline')).toBe(studioline);
    expect(getDesignLanguage('fieldline')).toBe(fieldline);
  });

  it('studioline declares every renderer slot (MOR-1073); fieldline none yet (MOR-1074)', () => {
    expect(Object.keys(studioline.renderers).sort()).toEqual([...RENDERER_SLOT_NAMES].sort());
    expect(fieldline.renderers).toEqual({});
  });

  it('fieldline clamps out "dense" (MOR-977 §4.4)', () => {
    expect(fieldline.density).toEqual({ kind: 'clamped', supported: ['comfortable', 'compact'] });
  });

  it('fieldline declares itself layout-incompatible with dual-receiver-cockpit as a manifest fact, not a capability check', () => {
    expect(fieldline.layoutCompatibility).toEqual([
      expect.objectContaining({ layoutId: 'dual-receiver-cockpit', compatible: false }),
    ]);
  });

  it('studioline holds all three density steps (MOR-977 §4.2.3)', () => {
    expect(studioline.density).toEqual({ kind: 'clamped', supported: ['comfortable', 'compact', 'dense'] });
  });
});

describe('no hardcoded family count', () => {
  it('registers a third, hypothetical family (fixed-scale, density not-applicable) the same way as studioline/fieldline', () => {
    const before = listDesignLanguageIds().length;
    const thirdFamily = validManifest({ id: 'thirdline', displayName: 'Thirdline', density: { kind: 'not-applicable' } });
    expect(() => registerDesignLanguage(thirdFamily)).not.toThrow();
    expect(listDesignLanguageIds().length).toBe(before + 1);
    expect(getDesignLanguage('thirdline')).toBe(thirdFamily);
  });
});
