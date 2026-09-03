/**
 * MOR-1066 compiled registry: count-agnostic registration, lookup by id,
 * duplicate-ID rejection, missing-loader rejection and the sdr-test real
 * registration proof. Each test's doc line names the mutation it exists to
 * kill.
 */
import { describe, it, expect } from 'vitest';
import {
  registerLayout, getLayout, listLayoutIds, LayoutValidationError,
} from '../contract';
import { sdrTestLayout } from '../declarations';
import { validLayoutManifest } from './fixtures';

describe('the sdr-test real registration proof', () => {
  // Kills: declarations.ts never actually calling registerLayout.
  it('registers sdr-test, mounting the live vfo + rxTx zones, with no change to SdrTestSkin.svelte behavior', () => {
    expect(getLayout('sdr-test')).toBe(sdrTestLayout);
    expect(sdrTestLayout.zones).toContainEqual({ id: 'receiver-deck', surfaces: ['vfo'] });
    expect(sdrTestLayout.zones).toContainEqual({ id: 'rx-tx', surfaces: ['rxTx'] });
    expect(typeof sdrTestLayout.loader).toBe('function');
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
    expect(typeof cockpit?.loader).toBe('function');
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

describe('missing loaders', () => {
  // Kills: registerLayout accepting a manifest whose loader isn't a function.
  it('rejects registration when loader is not a function', () => {
    const manifest = { ...validLayoutManifest({ id: 'no-loader-layout' }), loader: undefined };
    expect(() => registerLayout(manifest as never)).toThrow(/compiled Svelte loader/);
    expect(getLayout('no-loader-layout')).toBeUndefined();
  });
});

describe('lookup by id', () => {
  it('returns undefined for an id that was never registered', () => {
    expect(getLayout('never-registered-layout-xyz')).toBeUndefined();
  });
});
