/**
 * MOR-1066 manifest registry: count-agnostic registration, lookup by id,
 * duplicate-ID rejection, executable-loader rejection and the sdr-test real
 * registration proof. Each test's doc line names the mutation it exists to
 * kill.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  registerLayout, registerLayouts, getLayout, listLayoutIds, LayoutValidationError,
} from '../contract';
import { sdrTestLayout } from '../declarations';
import { validLayoutManifest } from './fixtures';

describe('the sdr-test real registration proof', () => {
  // Kills: declarations.ts never actually calling registerLayout.
  it('registers sdr-test, mounting the live vfo + rxTx zones, with no change to SdrTestSkin.svelte behavior', () => {
    expect(getLayout('sdr-test')).toBe(sdrTestLayout);
    expect(sdrTestLayout.zones).toContainEqual({ id: 'receiver-deck', surfaces: ['vfo'] });
    expect(sdrTestLayout.zones).toContainEqual({ id: 'rx-tx', surfaces: ['rxTx'] });
  });
});

describe('the dual-receiver-cockpit registration barrel proof (MOR-1067)', () => {
  // Kills: declarations.ts dropping the dual-receiver-cockpit line — the only
  // thing that wires that manifest into the app-wide registration barrel.
  // This file deliberately imports ONLY '../declarations' (never
  // '../dual-receiver-cockpit'), so the layout can be found here if and only
  // if the barrel itself pulls the manifest module in. Without this, deleting
  // the line leaves the whole suite green while the app silently loses the
  // layout — the surviving mutant review cycle 1 found (F3).
  it('registers dual-receiver-cockpit through the barrel, not through a test-file import', () => {
    const cockpit = getLayout('dual-receiver-cockpit');
    expect(cockpit).toBeDefined();
    expect(cockpit?.id).toBe('dual-receiver-cockpit');
  });
});

describe('count-agnostic registration', () => {
  // Kills: a registry that hardcodes a family count instead of accepting
  // any manifest that passes validation.
  it('registers a hypothetical extra layout the same way as sdr-test', () => {
    const before = listLayoutIds().length;
    registerLayout(validLayoutManifest({ id: 'hypothetical-layout' }));
    expect(listLayoutIds().length).toBe(before + 1);
    expect(getLayout('hypothetical-layout')?.id).toBe('hypothetical-layout');
  });

  it('registers a validated batch in input order', () => {
    const before = listLayoutIds().length;
    const first = validLayoutManifest({ id: 'batch-order-first' });
    const second = validLayoutManifest({ id: 'batch-order-second' });

    registerLayouts([first, second]);

    expect(listLayoutIds().slice(before)).toEqual(['batch-order-first', 'batch-order-second']);
    expect(getLayout(first.id)).toBe(first);
    expect(getLayout(second.id)).toBe(second);
  });
});

describe('atomic batch registration', () => {
  it('keeps single-item registration on the batch path', () => {
    const source = readFileSync('src/presentation/layouts/contract.ts', 'utf8');
    expect(source).toMatch(
      /export function registerLayout\(manifest: LayoutManifest\): void \{\s*registerLayouts\(\[manifest\]\);\s*\}/,
    );
  });

  it('does not commit an earlier valid item when a later manifest is invalid', () => {
    const valid = validLayoutManifest({ id: 'batch-before-invalid' });
    const invalid = validLayoutManifest({
      id: 'batch-invalid',
      compatibleTopologies: [],
    });

    expect(() => registerLayouts([valid, invalid])).toThrow(LayoutValidationError);
    expect(getLayout(valid.id)).toBeUndefined();
    expect(getLayout(invalid.id)).toBeUndefined();
  });

  it('checks collisions with the current registry before committing any batch item', () => {
    const existing = validLayoutManifest({ id: 'batch-existing' });
    const fresh = validLayoutManifest({ id: 'batch-fresh-before-existing' });
    registerLayout(existing);

    expect(() => registerLayouts([fresh, existing])).toThrow(/already registered/);
    expect(getLayout(existing.id)).toBe(existing);
    expect(getLayout(fresh.id)).toBeUndefined();
  });

  it('checks collisions inside the batch before committing any item', () => {
    const first = validLayoutManifest({ id: 'batch-duplicate' });
    const duplicate = validLayoutManifest({ id: 'batch-duplicate' });

    expect(() => registerLayouts([first, duplicate])).toThrow(/more than once in the batch/);
    expect(getLayout(first.id)).toBeUndefined();
  });
});

describe('duplicate IDs', () => {
  // Kills: registerLayout silently overwriting (the design-language
  // registry's semantics) instead of rejecting a second registration.
  it('rejects re-registering an already-registered id', () => {
    registerLayout(validLayoutManifest({ id: 'duplicate-test-layout' }));
    expect(() => registerLayout(validLayoutManifest({ id: 'duplicate-test-layout' }))).toThrow(LayoutValidationError);
    expect(() => registerLayout(validLayoutManifest({ id: 'duplicate-test-layout' }))).toThrow(/already registered/);
  });
});

describe('retired manifest loaders', () => {
  // Kills: keeping the old executable layout-loader boundary as an accepted
  // manifest key after production has standardized on skins/registry.loadSkin.
  it('rejects the old loader field instead of registering a second runtime authority', () => {
    const manifest = {
      ...validLayoutManifest({ id: 'legacy-loader-layout' }),
      loader: () => Promise.resolve({ default: {} }),
    };
    expect(() => registerLayout(manifest as never)).toThrow(/unknown top-level key/);
    expect(getLayout('legacy-loader-layout')).toBeUndefined();
  });
});

describe('lookup by id', () => {
  it('returns undefined for an id that was never registered', () => {
    expect(getLayout('never-registered-layout-xyz')).toBeUndefined();
  });
});
