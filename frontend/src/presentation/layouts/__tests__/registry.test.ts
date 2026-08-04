/**
 * MOR-1066 compiled registry: count-agnostic registration, lookup by id,
 * duplicate-ID rejection, missing-loader rejection, the sdr-test real
 * registration proof, topology fallback resolution, and MOR-1160 viewport
 * (fixed-native/minScale) fallback resolution. Each test's doc line names
 * the mutation it exists to kill.
 */
import { describe, it, expect } from 'vitest';
import {
  registerLayout, getLayout, listLayoutIds, resolveLayoutForTopology,
  resolveLayoutForViewport, LayoutValidationError,
} from '../contract';
import { sdrTestLayout } from '../declarations';
import { validLayoutManifest } from './fixtures';

describe('the sdr-test real registration proof', () => {
  // Kills: declarations.ts never actually calling registerLayout.
  it('registers sdr-test, mounting the live vfo + rxTx zones, with no change to SdrTestSkin.svelte behavior', () => {
    expect(getLayout('sdr-test')).toBe(sdrTestLayout);
    expect(sdrTestLayout.zones).toEqual([{ id: 'main', surfaces: ['vfo', 'rxTx'] }]);
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

describe('unsupported topology + fallback resolution (single fallback hop)', () => {
  it('resolves the primary layout when it declares the requested topology', () => {
    registerLayout(validLayoutManifest({ id: 'topo-primary', compatibleTopologies: ['1/single', '2/main_sub'] }));
    expect(resolveLayoutForTopology('topo-primary', '2/main_sub')?.id).toBe('topo-primary');
  });

  // Kills: resolveLayoutForTopology ignoring compatibleTopologies and
  // always returning the primary manifest.
  it('falls back to fallbackLayoutId when the primary does not declare the topology', () => {
    registerLayout(validLayoutManifest({ id: 'topo-fallback-target', compatibleTopologies: ['1/ab'] }));
    registerLayout(validLayoutManifest({
      id: 'topo-fallback-primary', compatibleTopologies: ['1/single'], fallbackLayoutId: 'topo-fallback-target',
    }));
    expect(resolveLayoutForTopology('topo-fallback-primary', '1/ab')?.id).toBe('topo-fallback-target');
  });

  it('returns undefined when unsupported and there is no fallback', () => {
    registerLayout(validLayoutManifest({ id: 'topo-no-fallback', compatibleTopologies: ['1/single'] }));
    expect(resolveLayoutForTopology('topo-no-fallback', '2/main_sub')).toBeUndefined();
  });
});

describe('fallback re-validation (review cycle 1, F1)', () => {
  // Kills: resolveFallback returning getLayout(fallbackLayoutId) without
  // re-applying the criterion. Before the fix this returned `f1-self-ref`
  // itself — a layout that does NOT support the requested topology.
  it('a self-referential fallback under an unsupported topology resolves to undefined, not itself', () => {
    registerLayout(validLayoutManifest({
      id: 'f1-self-ref', compatibleTopologies: ['1/single'], fallbackLayoutId: 'f1-self-ref',
    }));
    expect(resolveLayoutForTopology('f1-self-ref', '2/main_sub')).toBeUndefined();
  });

  // Kills the same bug via a two-hop chain: before the fix, the middle
  // layout (which itself does not support the topology) was returned
  // unvalidated because only the primary's support was ever checked.
  it('a two-hop chain (a -> b -> c) does not return b when only c supports the topology', () => {
    registerLayout(validLayoutManifest({ id: 'f1-chain-c', compatibleTopologies: ['1/ab'] }));
    registerLayout(validLayoutManifest({
      id: 'f1-chain-b', compatibleTopologies: ['1/single'], fallbackLayoutId: 'f1-chain-c',
    }));
    registerLayout(validLayoutManifest({
      id: 'f1-chain-a', compatibleTopologies: ['2/main_sub'], fallbackLayoutId: 'f1-chain-b',
    }));
    // v1 takes exactly one hop, so this must NOT silently return b (which
    // does not support '1/ab') and must NOT chase through to c either.
    const resolved = resolveLayoutForTopology('f1-chain-a', '1/ab');
    expect(resolved?.id).not.toBe('f1-chain-b');
    expect(resolved).toBeUndefined();
  });

  // Kills: resolveLayoutForViewport returning a fixed-native fallback
  // without checking IT against minScale too — defeats the MOR-1160
  // portrait-mobile exclusion for the fallback layout itself.
  it('a fixed-native fallback that itself fails minScale resolves to undefined, not the fallback', () => {
    registerLayout(validLayoutManifest({
      id: 'f1-vp-fallback', sizing: { mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0.5 },
    }));
    registerLayout(validLayoutManifest({
      id: 'f1-vp-primary',
      sizing: { mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0.5 },
      fallbackLayoutId: 'f1-vp-fallback',
    }));
    // Same tiny viewport fails minScale for both primary and fallback.
    const resolved = resolveLayoutForViewport('f1-vp-primary', { width: 100, height: 100 });
    expect(resolved).toBeUndefined();
  });
});

describe('viewport resolution (MOR-1160 sizing)', () => {
  it('a fixed-native layout resolves when the achievable scale meets minScale', () => {
    registerLayout(validLayoutManifest({
      id: 'fixed-fit-test', sizing: { mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0.5 },
    }));
    expect(resolveLayoutForViewport('fixed-fit-test', { width: 1280, height: 540 })?.id).toBe('fixed-fit-test');
  });

  // Kills: fitsViewport matching breakpoints instead of comparing scale to
  // minScale for a fixed-native layout (the MOR-1066 comment's explicit ask).
  it('falls back below minScale — portrait mobile is excluded arithmetically, no special-cased branch', () => {
    registerLayout(validLayoutManifest({
      id: 'fixed-portrait-fallback', sizing: { mode: 'fluid', responsiveBreakpoints: [] },
    }));
    registerLayout(validLayoutManifest({
      id: 'fixed-portrait-primary',
      sizing: { mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0.5 },
      fallbackLayoutId: 'fixed-portrait-fallback',
    }));
    // iPhone-class portrait viewport: min(390/1280, 844/540) ≈ 0.30 < 0.5.
    const resolved = resolveLayoutForViewport('fixed-portrait-primary', { width: 390, height: 844 });
    expect(resolved?.id).toBe('fixed-portrait-fallback');
  });

  it('a fluid layout always fits — breakpoints are reflow hints, not a hard gate', () => {
    registerLayout(validLayoutManifest({
      id: 'fluid-always-fits', sizing: { mode: 'fluid', responsiveBreakpoints: [600] },
    }));
    expect(resolveLayoutForViewport('fluid-always-fits', { width: 100, height: 100 })?.id).toBe('fluid-always-fits');
  });
});
