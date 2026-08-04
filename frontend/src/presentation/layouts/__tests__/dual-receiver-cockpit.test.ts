/**
 * MOR-1067 — the dual-receiver-cockpit manifest, exercised through the REAL
 * registry (not a fixture stand-in): registration shape, topology gating
 * (single-receiver topologies must fall back, never mount the cockpit), and
 * the MOR-1160 chrome-fluid sizing axis. Each test's doc line names the
 * mutation it exists to kill.
 */
import { describe, it, expect } from 'vitest';
import { fitsViewport, getLayout, resolveLayoutForTopology, resolveLayoutForViewport } from '../contract';
// The manifest comes through the BARREL, never from '../dual-receiver-cockpit'
// directly. A direct import would fire `registerLayout` from inside this test
// file and mask a `declarations.ts` that no longer wires the cockpit into the
// app at all — and under the fast pool's `isolate: false` it would leak that
// registration into sibling test files too (the MOR-1092 lesson). Importing
// the barrel also registers 'sdr-test', this manifest's fallbackLayoutId
// target, which the fallback tests below need.
import { dualReceiverCockpitLayout } from '../declarations';

describe('the dual-receiver-cockpit real registration', () => {
  it('registers with the id the DL handshake test already pins (MOR-977 §4.4)', () => {
    expect(getLayout('dual-receiver-cockpit')).toBe(dualReceiverCockpitLayout);
  });

  // MOR-1068 added `global`: the radio-wide half of the `vfo` surface (split
  // / dual-watch / active receiver) moved out of the first strip into its own
  // zone. Declaration order is rendered order — the end-to-end binding is
  // pinned in the shell's component test (MOR-1067 verification F6).
  it('declares primary/secondary VFO zones, the global row, and one shared RX/TX zone', () => {
    expect(dualReceiverCockpitLayout.zones).toEqual([
      { id: 'primary-vfo', surfaces: ['vfo'] },
      { id: 'secondary-vfo', surfaces: ['vfo'] },
      { id: 'global', surfaces: ['vfo'] },
      { id: 'rx-tx', surfaces: ['rxTx'] },
    ]);
    expect(dualReceiverCockpitLayout.requiredSemanticSurfaces).toEqual(['vfo', 'rxTx']);
  });

  it('has a compiled Svelte loader', async () => {
    expect(typeof dualReceiverCockpitLayout.loader).toBe('function');
  });
});

describe('topology gating (manifest topology classes + fallback resolution)', () => {
  // Kills: compatibleTopologies including a single-receiver class, or the
  // manifest omitting a safe fallback — either way a single-receiver radio
  // would end up mounting a shell with an empty/fabricated second strip.
  it.each(['1/single', '1/ab'] as const)(
    'does NOT mount for single-receiver topology "%s" — falls back to sdr-test instead',
    (topology) => {
      const resolved = resolveLayoutForTopology('dual-receiver-cockpit', topology);
      expect(resolved?.id).not.toBe('dual-receiver-cockpit');
      expect(resolved?.id).toBe('sdr-test');
    },
  );

  it.each(['2/ab_shared', '2/main_sub'] as const)(
    'mounts for its declared dual-receiver topology "%s"',
    (topology) => {
      expect(resolveLayoutForTopology('dual-receiver-cockpit', topology)?.id).toBe('dual-receiver-cockpit');
    },
  );
});

describe('MOR-1160 sizing axis: chrome stays fluid', () => {
  // This shell composes only VFO/RX-TX status text today — no fixed-native
  // instrument glass — so it must fit any viewport, unlike a fixed-native
  // layout that fails below minScale.
  it('fits an arbitrarily small viewport (fluid never gates on viewport)', () => {
    expect(fitsViewport(dualReceiverCockpitLayout, { width: 1, height: 1 })).toBe(true);
  });

  it('resolveLayoutForViewport returns the cockpit itself, never a fallback, at any size', () => {
    expect(resolveLayoutForViewport('dual-receiver-cockpit', { width: 1, height: 1 })?.id)
      .toBe('dual-receiver-cockpit');
  });
});
