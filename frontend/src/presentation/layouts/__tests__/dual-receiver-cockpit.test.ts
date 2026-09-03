/**
 * MOR-1067 — the dual-receiver-cockpit manifest, exercised through the REAL
 * registry (not a fixture stand-in): registration shape. Each test's doc line
 * names the mutation it exists to kill.
 */
import { describe, it, expect } from 'vitest';
import { getLayout } from '../contract';
// The manifest comes through the BARREL, never from '../dual-receiver-cockpit'
// directly. A direct import would fire `registerLayout` from inside this test
// file and mask a `declarations.ts` that no longer wires the cockpit into the
// app at all — and under the fast pool's `isolate: false` it would leak that
// registration into sibling test files too (the MOR-1092 lesson).
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
      // MOR-1336 (S4): the cockpit's txAux controls gain a zone of their own.
      { id: 'tx-aux', surfaces: ['txAux'] },
    ]);
    expect(dualReceiverCockpitLayout.requiredSemanticSurfaces).toEqual(['vfo', 'rxTx']);
  });

  it('has a compiled Svelte loader', async () => {
    expect(typeof dualReceiverCockpitLayout.loader).toBe('function');
  });
});
