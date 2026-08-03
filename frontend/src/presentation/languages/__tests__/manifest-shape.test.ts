/**
 * MOR-1072 manifest shape: naming policy (MOR-977 §4.6, MOR-1071), density
 * clamps (MOR-1072 review note), layout-compatibility declarations
 * (MOR-977 §4.4), and the capability-fork rejection that makes
 * "manufacturer inspiration never becomes a capability fork" enforceable.
 */
import { describe, it, expect } from 'vitest';
import { isValidLanguageId, validateManifest, DesignLanguageValidationError, type LayoutCompatibilityDeclaration } from '../contract';
import { validManifest } from './fixtures';

describe('naming policy', () => {
  it.each(['icom-modern', 'yaesu-field', 'kenwood-classic', 'japanese-sdr', 'american-console'])(
    'rejects the vendor/geographic-marker id "%s"',
    (id) => {
      expect(isValidLanguageId(id)).toBe(false);
    },
  );

  // Review cycle 1, B2: the marker check used to split on '-' and compare
  // whole segments, so a marker fused into a longer segment (no hyphen)
  // slipped through. Fixed to substring-match the flattened id.
  it.each(['flexradio', 'icom7610', 'icomclassic', 'kenwoodstyle', 'yaesuline', 'ic-7610'])(
    'rejects the fused/embedded vendor-marker id "%s" (B2)',
    (id) => {
      expect(isValidLanguageId(id)).toBe(false);
    },
  );

  // Review cycle 2, N3 — process note: the B2 fix in cycle 1 added a bare
  // `ic` marker to catch `ic-7610`, which over-corrected: it also rejected
  // `classic-instrument`, a real, doctrine-preserved future family name
  // (MOR-977 §4.4 — `meridian`, the "classic precision-instrument grammar",
  // stays revivable). Instead of noticing the regression, cycle 1 silently
  // swapped `classic-instrument` out of this list for `quiet-bench`, which
  // hid the failure rather than surfacing it — exactly the failure mode
  // independent review exists to catch. `classic-instrument` is restored
  // here, `ic-7610` is now caught by MODEL_NUMBER_PATTERN instead of the
  // bare `ic` marker (see contract.ts), and three more of the reviewer's
  // legitimate names are added so this doesn't regress silently again.
  it.each(['studioline', 'fieldline', 'contest-console', 'classic-instrument', 'atomic-rail', 'optical-bench', 'physics-desk'])(
    'accepts the product-owned id "%s"',
    (id) => {
      expect(isValidLanguageId(id)).toBe(true);
    },
  );

  it('rejects non-kebab-case ids', () => {
    expect(isValidLanguageId('Studioline')).toBe(false);
    expect(isValidLanguageId('studio_line')).toBe(false);
  });

  it('registerDesignLanguage rejects a vendor-marker id via validateManifest', () => {
    const manifest = validManifest({ id: 'icom-reference' });
    expect(() => validateManifest(manifest)).toThrow(DesignLanguageValidationError);
  });

  // Honest documented gap (B2): the list is a denylist of named markers,
  // not a geography classifier — an unlisted place name still passes.
  // Catching that class is a human naming-review call, not a string match.
  it('does not catch every geographic reference — "nippon-line" is a known, accepted gap', () => {
    expect(isValidLanguageId('nippon-line')).toBe(true);
  });
});

describe('density clamp', () => {
  it('accepts "not-applicable" for a fixed-scale language (e.g. an amber-LCD-style family)', () => {
    const manifest = validManifest({ density: { kind: 'not-applicable' } });
    expect(() => validateManifest(manifest)).not.toThrow();
  });

  it('accepts a clamped subset of density levels (e.g. dense excluded)', () => {
    const manifest = validManifest({ density: { kind: 'clamped', supported: ['comfortable', 'compact'] } });
    expect(() => validateManifest(manifest)).not.toThrow();
    expect(manifest.density.kind === 'clamped' && manifest.density.supported).not.toContain('dense');
  });
});

describe('layout compatibility', () => {
  it('is a manifest declaration, not a capability check — a language may declare itself incompatible with a layout', () => {
    const manifest = validManifest({
      layoutCompatibility: [
        { layoutId: 'dual-receiver-cockpit', compatible: false, reason: 'runs at reduced relative density' },
      ],
    });
    expect(() => validateManifest(manifest)).not.toThrow();
    expect(manifest.layoutCompatibility[0]).toMatchObject({ layoutId: 'dual-receiver-cockpit', compatible: false });
  });
});

describe('capability-fork rejection', () => {
  it('rejects a manifest with a top-level capabilities-shaped key', () => {
    const manifest = { ...validManifest(), capabilities: ['CW', 'SSB', 'FM'] };
    expect(() => validateManifest(manifest)).toThrow(/capability-shaped key/);
  });

  it('rejects a manifest referencing a radio model deep inside a token group', () => {
    const manifest = validManifest();
    const poisoned = { ...manifest, tokens: { ...manifest.tokens, meters: { ...manifest.tokens.meters, radioModel: 'test-radio' } } };
    expect(() => validateManifest(poisoned)).toThrow(/capability-shaped key/);
  });

  it('rejects a manifest referencing a vendor key anywhere in layoutCompatibility', () => {
    const poisoned = {
      layoutId: 'dual-receiver-cockpit', compatible: true, vendor: 'test-vendor',
    } as unknown as LayoutCompatibilityDeclaration;
    const manifest = validManifest({ layoutCompatibility: [poisoned] });
    expect(() => validateManifest(manifest)).toThrow(/capability-shaped key/);
  });
});
