/**
 * MOR-2253 slice 1 — `InstrumentGroup` v1 schema, validator, and compiled
 * registry (`../contract.ts`), mirroring `../../layouts/__tests__/
 * manifest-shape.test.ts` and `registry.test.ts` at the scale this slice's
 * four-field schema needs. Each test's doc line names the mutation it
 * exists to kill.
 *
 * Also carries the TEXTUAL half of the "declared once" guard the
 * instrument-group ADR (`docs/plans/2026-09-02-instrument-group-adr.md` §4)
 * requires: a `fixed-native` group's native-canvas property literals must
 * appear only in its declaration (scanning the derived contour of files
 * that could still duplicate a native canvas binding). The STRUCTURAL half — every manifest zone that
 * references a group agrees with that group's own canvas/minScale — lives
 * in `../../layouts/__tests__/segmentline-registration.test.ts` instead (see
 * the comment above that file's own describe block for why).
 */
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { isValidLanguageId } from '../../languages/contract';
import {
  registerGroup, getGroup, listGroupIds, validateInstrumentGroup, GroupValidationError,
  type InstrumentGroup,
} from '../contract';
// Triggers real registration side effects, same pattern `../../layouts/
// __tests__/registry.test.ts` uses (`import { sdrTestLayout } from
// '../declarations'`): importing the barrels is what populates the shared
// registries, not merely defining the manifest/group objects.
import { peerSplitGlassGroup } from '../declarations';

function validGroup(overrides: Partial<InstrumentGroup> = {}): InstrumentGroup {
  return {
    schemaVersion: 1,
    id: 'test-group',
    canvas: { w: 800, h: 600 },
    scaling: { mode: 'fixed-native', minScale: 0.4 },
    ...overrides,
  };
}

describe('naming policy (reused from ../../languages/contract.ts, not reimplemented)', () => {
  // Kills: swapping isValidProductId for a no-op/always-true check.
  it.each(['icom-modern', 'yaesu-field', 'japanese-sdr', 'ic-7610'])(
    'rejects the vendor/geographic-marker id "%s"',
    (id) => {
      expect(() => validateInstrumentGroup(validGroup({ id }))).toThrow(GroupValidationError);
    },
  );

  it('is literally the shared function, not a duplicate', () => {
    expect(isValidLanguageId('japanese-sdr')).toBe(false);
  });
});

describe('versioned (v1) shape', () => {
  // Kills: dropping the schemaVersion check entirely.
  it('rejects a group with the wrong schemaVersion', () => {
    const group = { ...validGroup(), schemaVersion: 2 as 1 };
    expect(() => validateInstrumentGroup(group)).toThrow(/schemaVersion/);
  });

  it('accepts schemaVersion 1', () => {
    expect(() => validateInstrumentGroup(validGroup())).not.toThrow();
  });
});

describe('canvas', () => {
  // Kills: accepting a canvas with a non-positive or non-finite dimension.
  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY])('rejects a canvas with w=%s', (w) => {
    expect(() => validateInstrumentGroup(validGroup({ canvas: { w, h: 600 } }))).toThrow(/canvas/);
  });

  // Kills: accepting an extra key on canvas (e.g. a smuggled `d`/`z` field).
  it('rejects a canvas with an extra key', () => {
    const group = validGroup({ canvas: { w: 800, h: 600, extra: 1 } as unknown as InstrumentGroup['canvas'] });
    expect(() => validateInstrumentGroup(group)).toThrow(/canvas/);
  });
});

describe('scaling', () => {
  it('accepts fixed-native scaling with a positive minScale', () => {
    expect(() => validateInstrumentGroup(validGroup({ scaling: { mode: 'fixed-native', minScale: 0.5 } })))
      .not.toThrow();
  });

  it('accepts reflow scaling', () => {
    expect(() => validateInstrumentGroup(validGroup({ scaling: { mode: 'reflow' } }))).not.toThrow();
  });

  // Kills: dropping the positive-finite check on minScale.
  it('rejects a non-positive minScale', () => {
    const group = validGroup({ scaling: { mode: 'fixed-native', minScale: 0 } });
    expect(() => validateInstrumentGroup(group)).toThrow(/minScale/);
  });

  // Kills: accepting a fixed-native scaling object that also carries a
  // reflow-shaped (or any extra) key.
  it('rejects fixed-native scaling with an extra key', () => {
    const poisoned = { mode: 'fixed-native', minScale: 0.5, extra: true } as unknown as InstrumentGroup['scaling'];
    expect(() => validateInstrumentGroup(validGroup({ scaling: poisoned }))).toThrow(/scaling/);
  });

  // Kills: accepting a reflow scaling object that also carries minScale.
  it('rejects reflow scaling with an extra key', () => {
    const poisoned = { mode: 'reflow', minScale: 0.5 } as unknown as InstrumentGroup['scaling'];
    expect(() => validateInstrumentGroup(validGroup({ scaling: poisoned }))).toThrow(/scaling/);
  });

  it('rejects an unknown scaling.mode', () => {
    const poisoned = { mode: 'stretch' } as unknown as InstrumentGroup['scaling'];
    expect(() => validateInstrumentGroup(validGroup({ scaling: poisoned }))).toThrow(/mode/);
  });
});

describe('capability-fork and module-path rejection (ADR §3: a new guard needed for a separate group node)', () => {
  // Kills: no capability-shaped-key scan wired into validateInstrumentGroup —
  // the layouts scan (`findCapabilityLikeKey`) only ever runs over a
  // LayoutManifest, never a group.
  it('rejects a group with a top-level capabilities-shaped key', () => {
    const group = { ...validGroup(), capabilities: ['CW'] };
    expect(() => validateInstrumentGroup(group as never)).toThrow(/capability-shaped key/);
  });

  // Kills: findModulePathLikeValue never wired in, or its regex loosened to
  // miss a real relative-import-shaped string riding in `id`.
  it('rejects a module-path-shaped value riding in id', () => {
    const group = validGroup({ id: '../evil/path' });
    expect(() => validateInstrumentGroup(group)).toThrow(/module-path-shaped value/);
  });

  // Kills: unknown-top-level-key rejection missing or checking a subset.
  it('rejects a group with an extra unknown top-level key', () => {
    const group = { ...validGroup(), extraField: 'nope' };
    expect(() => validateInstrumentGroup(group as never)).toThrow(/unknown top-level key/);
  });
});

describe('compiled registry — count-agnostic, same shape as ../../layouts/contract.ts', () => {
  it('registers a hypothetical extra group the same way as peer-split-glass', () => {
    const before = listGroupIds().length;
    registerGroup(validGroup({ id: 'hypothetical-group' }));
    expect(listGroupIds().length).toBe(before + 1);
    expect(getGroup('hypothetical-group')?.id).toBe('hypothetical-group');
  });

  // Kills: registerGroup silently overwriting instead of rejecting a second
  // registration.
  it('rejects re-registering an already-registered id', () => {
    registerGroup(validGroup({ id: 'duplicate-test-group' }));
    expect(() => registerGroup(validGroup({ id: 'duplicate-test-group' }))).toThrow(GroupValidationError);
    expect(() => registerGroup(validGroup({ id: 'duplicate-test-group' }))).toThrow(/already registered/);
  });

  it('returns undefined for an id that was never registered', () => {
    expect(getGroup('never-registered-group-xyz')).toBeUndefined();
  });
});

describe('the peer-split-glass real registration proof', () => {
  // Kills: declarations.ts defining the group but never calling
  // registerGroup — the resolution below would then read undefined.
  it('registers peer-split-glass through the real registry', () => {
    expect(getGroup('peer-split-glass')).toBe(peerSplitGlassGroup);
    expect(peerSplitGlassGroup.scaling.mode).toBe('fixed-native');
  });
});

// The structural half of the "declared once" guard — every manifest zone
// referencing a group agrees with that group's own canvas/minScale — lives
// in `../../layouts/__tests__/segmentline-registration.test.ts` instead of
// here: it must read the layout manifest's own MOR-1160 stage-sizing field,
// and `../../layouts/__tests__/stage-sizing-boundary.test.ts`'s own MOR-1247
// tripwire fails any file OUTSIDE `presentation/layouts/` whose source names
// that field's identifier at all — a file under `presentation/groups/
// __tests__/` is exactly such a file (found by running the check here once
// and reading the tripwire red; module load registers both registries the
// same way either location would).

describe('the native canvas is declared exactly once in production source (ADR §4)', () => {
  const SRC_ROOT = 'src';
  // The two objects the ADR (§4) names as entitled to their own literal:
  // `LCD_NATIVE_STAGE` (a coincidence with the glass's numbers, not a shared
  // declaration — `lcd-cockpit`/`lcd-scope` are not groups yet) and this
  // module's own declaration file (where the literal legitimately lives).
  const ENTITLED = new Set([
    path.join(SRC_ROOT, 'presentation', 'layouts', 'lcd-declarations.ts'),
    path.join(SRC_ROOT, 'presentation', 'groups', 'declarations.ts'),
  ]);
  // Restrict this to native-canvas declarations and bindings: ordinary CSS
  // alpha values are not alternate stage declarations.
  const NATIVE_CANVAS_LITERAL = /\b(?:nativeW|nativeH|minScale)\s*(?::|=)\s*\{?\s*(?:1280|540|0\.5)\b/;

  function collectSourceFiles(dir: string): string[] {
    const out: string[] = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) out.push(...collectSourceFiles(full));
      else if (full.endsWith('.ts') || full.endsWith('.svelte')) out.push(full);
    }
    return out;
  }

  // Excludes `__tests__` from the derivation, same reason `skins/__tests__/
  // mor1099-unmounted-svelte-census.test.ts` excludes it from its own
  // importer-candidate set: a test fixture can carry the same numbers for an
  // unrelated reason (`../../layouts/__tests__/manifest-shape.test.ts`'s
  // `minScale: 0` negative case; `registry.test.ts`'s four viewport-fallback
  // fixtures) and would read identically to a real declaration under a
  // text-level scan, hiding a real duplicate behind its own test's prose.
  function isProductionFile(file: string): boolean {
    return !file.split(path.sep).includes('__tests__');
  }

  function derivedContour(): string[] {
    return collectSourceFiles(SRC_ROOT)
      .filter(isProductionFile)
      .filter((file) => !ENTITLED.has(file))
      .filter((file) => {
        const text = readFileSync(file, 'utf8');
        return /import ScaledStage/.test(text) || /mode:\s*'fixed-native'/.test(text);
      })
      .sort();
  }

  // A file entering or leaving this set changes what the textual scan below
  // covers — pinned so the set cannot silently drift out from under it.
  // Four files, not the three `9a737997` (pre-this-slice) derived: `../
  // contract.ts` matches the same way `../../layouts/contract.ts` already
  // did — via `GroupScaling`'s own `readonly mode: 'fixed-native'` TYPE
  // literal, not a value — and carries zero 1280/540/0.5 literals, same as
  // its layouts sibling.
  const HAND_WRITTEN_CONTOUR = [
    path.join(SRC_ROOT, 'presentation', 'groups', 'contract.ts'),
    path.join(SRC_ROOT, 'presentation', 'layouts', 'contract.ts'),
    path.join(SRC_ROOT, 'presentation', 'layouts', 'segmentline-declarations.ts'),
    path.join(SRC_ROOT, 'skins', 'segmentline', 'PeerSplitLayout.svelte'),
  ].sort();

  // Kills: the derivation rule (import ScaledStage OR declare a layout's own
  // `fixed-native` stage-sizing value, minus the entitled files) drifting
  // away from what this suite actually scans below.
  it('the derivation rule yields exactly the hand-written contour', () => {
    expect(derivedContour()).toEqual(HAND_WRITTEN_CONTOUR);
  });

  // Kills: PeerSplitLayout.svelte or segmentline-declarations.ts reverting a
  // native-canvas binding to a literal instead of reading the group by reference —
  // MUTATION TARGET (see the PR body for the observed red/green cycle).
  it.each(HAND_WRITTEN_CONTOUR)('%s declares no duplicate native-canvas literal', (file) => {
    expect(readFileSync(file, 'utf8')).not.toMatch(NATIVE_CANVAS_LITERAL);
  });
});

// MOR-2253 slice 1 F1 (verifier BLOCKED, fixed in b994382e): `fixtures/
// main.ts` mounts `PeerSplitLayout` directly, the harness's own registration
// path outside every inventory test and outside both tsconfigs — it has no
// automated guard at all, and its correctness rested on two manual browser
// measurements. This pin closes that: a textual check that the mount
// sources canvasW/canvasH from `peerSplitGlassGroup`, never from a literal,
// since a literal here is exactly what would be invisible to the "declared
// once" contour scan above (`fixtures/` sits outside `src/`, where that scan
// roots).
describe('the fixture harness sources the peer-split canvas from the group (ADR §4 / F1)', () => {
  // Comments stripped first — same idiom as `presentation/workspace/
  // __tests__/purity.isolated.test.ts: code()` and `skins/segmentline/
  // __tests__/PeerSplitLayout.component.test.ts`'s CSS stripper: a naive
  // text search would match a comment DESCRIBING the sourcing rule instead
  // of code that actually obeys it — the exact class of mistake that pulled
  // `LcdLayout.svelte` into this file's own derived contour two cycles ago.
  const fixtureSource = readFileSync('fixtures/main.ts', 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
  // Narrowed to the `mount(PeerSplitLayout, ...)` call itself, not the whole
  // file: a `1280`/`540` written for an unrelated reason elsewhere in this
  // file (there is none today, but nothing stops one) must not mask what
  // this call site actually passes.
  const mountCall = fixtureSource.match(/mount\(PeerSplitLayout[\s\S]*?\}\);/)?.[0];

  it('sources canvasW/canvasH from peerSplitGlassGroup.canvas, never a literal', () => {
    // Kills: the `mount(PeerSplitLayout, ...)` call site disappearing or
    // being renamed past what this pin's own regex recognizes — silent
    // green from a pin that stopped finding anything would be worse than a
    // loud failure.
    expect(mountCall, 'no mount(PeerSplitLayout, ...) call found in fixtures/main.ts').toBeDefined();
    // Kills: MUTATION 1 (verifier-specified) — dropping `props` from that
    // mount call entirely, reproducing the exact defect a verifier caught
    // live (canvasW/canvasH undefined, ScaledStage's guard cannot catch NaN).
    expect(mountCall).toMatch(/canvasW:\s*peerSplitGlassGroup\.canvas\.w\b/);
    expect(mountCall).toMatch(/canvasH:\s*peerSplitGlassGroup\.canvas\.h\b/);
    // Kills: MUTATION 2 (verifier-specified, "the one that matters") —
    // reverting the sourced values to literals (`canvasW: 1280, canvasH:
    // 540`). Already implied by the two matches above failing to match a
    // literal, but checked directly here too, as its own named condition
    // with its own failure mode: a literal at this call site is precisely
    // what the "declared once" guard above cannot see.
    expect(mountCall).not.toMatch(/\b1280\b|\b540\b/);
  });
});
