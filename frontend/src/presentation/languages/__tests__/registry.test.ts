/**
 * MOR-1072 registry: the two frozen family IDs (`studioline`/`fieldline`,
 * MOR-977 §4.6) are registered as declarations, and the registry does not
 * hardcode a family count — the "no hardcoded family count" block below
 * proves that generically with an arbitrary fixture id, and MOR-2149's
 * `segmentline` (below) is a real, non-hypothetical instance of the same
 * claim. MOR-1073 gave studioline its renderers, MOR-1074 gave fieldline its
 * own, and MOR-2149 gave segmentline its own, so all three declared families
 * now fill every slot — the "zero renderers declared" fallback path is
 * exercised by the shared fixture (`validManifest`) and by
 * `renderer-contract.test.ts`.
 */
import { describe, it, expect } from 'vitest';
import {
  listDesignLanguageIds, getDesignLanguage, registerDesignLanguage, RENDERER_SLOT_NAMES,
} from '../contract';
import { studioline, fieldline, segmentline } from '../declarations';
import { validManifest } from './fixtures';

describe('the two frozen v3 declarations', () => {
  it('registers studioline and fieldline', () => {
    expect(listDesignLanguageIds()).toEqual(expect.arrayContaining(['studioline', 'fieldline']));
    expect(getDesignLanguage('studioline')).toBe(studioline);
    expect(getDesignLanguage('fieldline')).toBe(fieldline);
  });

  it('both declared families fill every renderer slot (MOR-1073, MOR-1074)', () => {
    expect(Object.keys(studioline.renderers).sort()).toEqual([...RENDERER_SLOT_NAMES].sort());
    expect(Object.keys(fieldline.renderers).sort()).toEqual([...RENDERER_SLOT_NAMES].sort());
  });

  // The inventory row that makes "second language" mean something: the two
  // families occupy the same slots with DIFFERENT implementations, so no slot
  // is silently shared and no family is a re-export of the other.
  it('no renderer instance is shared between the two families', () => {
    for (const slot of RENDERER_SLOT_NAMES) {
      expect(fieldline.renderers[slot]).toBeDefined();
      expect(fieldline.renderers[slot]).not.toBe(studioline.renderers[slot]);
    }
  });

  it('fieldline clamps out "dense" (MOR-977 §4.4)', () => {
    expect(fieldline.density).toEqual({ kind: 'clamped', supported: ['comfortable', 'compact'] });
  });

  it('fieldline declares itself layout-incompatible with dual-receiver-cockpit as a manifest fact, not a capability check', () => {
    expect(fieldline.layoutCompatibility).toEqual([
      {
        layoutId: 'dual-receiver-cockpit',
        compatible: false,
        reason: 'fieldline cannot serve as the desktop dual-receiver default at 0.6 relative density (MOR-977 §4.4).',
      },
      { layoutId: 'desktop-v2', compatible: true },
    ]);
  });

  it('studioline holds all three density steps (MOR-977 §4.2.3)', () => {
    expect(studioline.density).toEqual({ kind: 'clamped', supported: ['comfortable', 'compact', 'dense'] });
  });
});

describe('segmentline (MOR-2149) fills every slot, exactly as studioline and fieldline do', () => {
  it('registers segmentline alongside studioline and fieldline', () => {
    expect(listDesignLanguageIds()).toEqual(expect.arrayContaining(['segmentline']));
    expect(getDesignLanguage('segmentline')).toBe(segmentline);
  });

  it('fills every renderer slot (MOR-2149)', () => {
    expect(Object.keys(segmentline.renderers).sort()).toEqual([...RENDERER_SLOT_NAMES].sort());
  });

  // The inventory row that makes "third language" mean something: all three
  // families occupy the same slots with DIFFERENT implementations, so no
  // slot is silently shared and no family is a re-export of another.
  it('shares no renderer instance with studioline or fieldline', () => {
    for (const slot of RENDERER_SLOT_NAMES) {
      expect(segmentline.renderers[slot]).toBeDefined();
      expect(segmentline.renderers[slot]).not.toBe(studioline.renderers[slot]);
      expect(segmentline.renderers[slot]).not.toBe(fieldline.renderers[slot]);
    }
  });
});

describe('no hardcoded family count', () => {
  it('registers an arbitrary, hypothetical family (fixed-scale, density not-applicable) the same way as studioline/fieldline', () => {
    const before = listDesignLanguageIds().length;
    const thirdFamily = validManifest({ id: 'thirdline', displayName: 'Thirdline', density: { kind: 'not-applicable' } });
    expect(() => registerDesignLanguage(thirdFamily)).not.toThrow();
    expect(listDesignLanguageIds().length).toBe(before + 1);
    expect(getDesignLanguage('thirdline')).toBe(thirdFamily);
  });
});
