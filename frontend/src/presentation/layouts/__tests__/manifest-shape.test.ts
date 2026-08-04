/**
 * MOR-1066 manifest shape: naming policy (reused from the MOR-1072 design-
 * language denylist), declared zones, the v1 schema version, and the
 * rejection classes the ticket names explicitly — capability-like keys,
 * module-path-shaped values, unknown top-level keys, vendor/geo-marker IDs.
 * Each test's doc line names the mutation it exists to kill.
 */
import { describe, it, expect } from 'vitest';
import { isValidLanguageId } from '../../languages/contract';
import { validateLayoutManifest, LayoutValidationError, type SizingPolicy } from '../contract';
import { validLayoutManifest } from './fixtures';

describe('naming policy (reused from ../languages/contract.ts, not reimplemented)', () => {
  // Kills: swapping isValidProductId for a no-op/always-true check.
  it.each(['icom-modern', 'yaesu-field', 'japanese-sdr', 'american-console', 'ic-7610'])(
    'rejects the vendor/geographic-marker id "%s"',
    (id) => {
      expect(() => validateLayoutManifest(validLayoutManifest({ id }))).toThrow(LayoutValidationError);
    },
  );

  it.each(['dual-receiver-cockpit', 'sdr-test', 'classic-instrument', 'spectrum-first'])(
    'accepts the product-owned id "%s"',
    (id) => {
      expect(() => validateLayoutManifest(validLayoutManifest({ id }))).not.toThrow();
    },
  );

  // Kills: a validator that checks id shape but not fallbackLayoutId shape.
  it('rejects a fallbackLayoutId that fails the same naming policy', () => {
    const manifest = validLayoutManifest({ fallbackLayoutId: 'icom-classic' });
    expect(() => validateLayoutManifest(manifest)).toThrow(/naming policy/);
  });

  it('is literally the shared function, not a duplicate', () => {
    expect(isValidLanguageId('japanese-sdr')).toBe(false);
  });
});

describe('versioned (v1) shape', () => {
  // Kills: dropping the schemaVersion check entirely.
  it('rejects a manifest with the wrong schemaVersion', () => {
    const manifest = { ...validLayoutManifest(), schemaVersion: 2 as 1 };
    expect(() => validateLayoutManifest(manifest)).toThrow(/schemaVersion/);
  });

  it('accepts schemaVersion 1', () => {
    expect(() => validateLayoutManifest(validLayoutManifest())).not.toThrow();
  });
});

describe('declared zones (v3 ADR: which semantic surfaces appear, and where)', () => {
  // Kills: accepting an empty zones array (a layout that mounts nothing).
  it('rejects a manifest with zero zones', () => {
    expect(() => validateLayoutManifest(validLayoutManifest({ zones: [] }))).toThrow(/zone/);
  });

  // Kills: not validating zone.surfaces against SEMANTIC_SURFACE_NAMES.
  it('rejects a zone declaring an unknown semantic surface', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'meterPanel'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });

  // Kills: requiredSemanticSurfaces accepted without checking it is
  // actually mounted by some declared zone (a claimed-but-absent surface).
  it('rejects a requiredSemanticSurfaces entry no zone mounts', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/no zone mounts/);
  });
});

describe('compatible topology classes', () => {
  // Kills: accepting compatibleTopologies outside the four canonical classes.
  it('rejects an unknown topology class', () => {
    const manifest = validLayoutManifest({ compatibleTopologies: ['3/whatever'] as unknown as ['1/single'] });
    expect(() => validateLayoutManifest(manifest)).toThrow(/compatibleTopologies/);
  });

  it('rejects zero compatible topologies', () => {
    expect(() => validateLayoutManifest(validLayoutManifest({ compatibleTopologies: [] }))).toThrow(/topology/);
  });
});

describe('sizing (MOR-1160)', () => {
  it('accepts fluid sizing with breakpoints', () => {
    const manifest = validLayoutManifest({ stageSizing: { mode: 'fluid', responsiveBreakpoints: [600, 900] } });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('accepts fixed-native sizing with a positive minScale', () => {
    const manifest = validLayoutManifest({ stageSizing: { mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0.5 } });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  // Kills: dropping the mutual-exclusivity check between fixed-native and
  // responsiveBreakpoints (MOR-1066 comment, MOR-1160).
  it('rejects a fixed-native layout that also declares responsiveBreakpoints', () => {
    const poisoned = {
      mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0.5, responsiveBreakpoints: [600],
    } as unknown as SizingPolicy;
    expect(() => validateLayoutManifest(validLayoutManifest({ stageSizing: poisoned }))).toThrow(/mutually exclusive/);
  });

  it('rejects a non-positive minScale', () => {
    const manifest = validLayoutManifest({ stageSizing: { mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0 } });
    expect(() => validateLayoutManifest(manifest)).toThrow(/positive finite/);
  });
});

describe('capability-fork and module-path rejection', () => {
  // Kills: findCapabilityLikeKey never wired into validateLayoutManifest.
  it('rejects a manifest with a top-level capabilities-shaped key', () => {
    const manifest = { ...validLayoutManifest(), capabilities: ['CW', 'SSB'] };
    expect(() => validateLayoutManifest(manifest)).toThrow(/capability-shaped key/);
  });

  it('rejects a vendor key nested inside a zone', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo'], vendor: 'test-vendor' } as unknown as { id: string; surfaces: ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/capability-shaped key/);
  });

  // Kills: findModulePathLikeValue never wired in, or its regex loosened
  // to miss a real relative-import-shaped string. displayName is free text
  // with no naming-policy check of its own, so this proves the scan (not
  // the id-shape check) is what catches it.
  it('rejects a module-path-shaped value riding in a free-text field (displayName)', () => {
    const manifest = { ...validLayoutManifest(), displayName: './SomeLayout.svelte' };
    expect(() => validateLayoutManifest(manifest)).toThrow(/module-path-shaped value/);
  });

  it('does not flag the compiled loader itself — JSON.stringify never sees inside a function', () => {
    expect(() => validateLayoutManifest(validLayoutManifest())).not.toThrow();
  });

  // Kills: unknown-top-level-key rejection missing or checking a subset of keys.
  it('rejects a manifest with an extra unknown top-level key', () => {
    const manifest = { ...validLayoutManifest(), extraField: 'nope' };
    expect(() => validateLayoutManifest(manifest)).toThrow(/unknown top-level key/);
  });

  // Kills: F3 — the module-path scan only recognizing './'/'../' and missing
  // this codebase's actual alias/bare/absolute forms.
  it.each([
    ['the $lib alias', '$lib/skins/foo/Bar.svelte'],
    ['a bare src/ reference', 'src/skins/foo/Bar.svelte'],
    ['an absolute path', '/abs/skins/foo/Bar.svelte'],
  ])('rejects a module-path-shaped value using %s', (_label, value) => {
    const manifest = { ...validLayoutManifest(), displayName: value };
    expect(() => validateLayoutManifest(manifest)).toThrow(/module-path-shaped value/);
  });

  // Kills: F4 — an eagerly-computed derived value (e.g. mountedSurfaces)
  // reading `manifest.zones` before the capability/module-path scan has had
  // a chance to short-circuit, turning a doctrine violation into a raw
  // TypeError instead of the documented LayoutValidationError.
  it('a capability-shaped key wins over malformed/missing zones — no raw TypeError', () => {
    const manifest = { ...validLayoutManifest(), capabilities: ['CW'], zones: undefined };
    expect(() => validateLayoutManifest(manifest as never)).toThrow(/capability-shaped key/);
  });
});

describe('exact-key discipline pins (MOR-1072 N1 lesson, review cycle 1 F2)', () => {
  // Kills: hasExactPlainKeys downgraded to a plain Object.keys check, which
  // drops the `proto !== Object.prototype` guard — a class instance (or any
  // object with a non-plain prototype) carries its extra keys on the
  // prototype chain, invisible to Object.keys/Reflect.ownKeys on the
  // instance itself, but must still be rejected outright.
  it('rejects a manifest whose prototype is not plain Object.prototype (class-instance smuggling)', () => {
    class PoisonedManifest {
      schemaVersion = 1 as const;
      id = 'testlayout';
      displayName = 'Test Layout';
      loader = () => Promise.resolve({ default: {} as never });
      zones = [{ id: 'main', surfaces: ['vfo', 'rxTx'] as const }];
      compatibleTopologies = ['1/single'] as const;
      requiredSemanticSurfaces = ['vfo', 'rxTx'] as const;
      stageSizing = { mode: 'fluid' as const, responsiveBreakpoints: [] as number[] };
      fallbackLayoutId = null;
      // Prototype getter: an own-keys/Object.keys walk of the instance never
      // sees this — only the explicit prototype check catches it.
      get capabilities(): string[] { return ['CW']; }
    }
    expect(() => validateLayoutManifest(new PoisonedManifest() as unknown as never)).toThrow(LayoutValidationError);
  });

  // Kills: hasExactPlainKeys downgraded to Object.keys, which enumerates
  // string keys only — a symbol key is invisible to Object.keys but visible
  // to Reflect.ownKeys, so only the latter catches a smuggled symbol key.
  it('rejects a manifest with a smuggled symbol key', () => {
    const manifest: Record<PropertyKey, unknown> = { ...validLayoutManifest() };
    manifest[Symbol('capabilities')] = ['CW'];
    expect(() => validateLayoutManifest(manifest as never)).toThrow(/unknown top-level key/);
  });
});
