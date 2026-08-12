/**
 * MOR-1409 A15 — the honest active-frequency accessor.
 *
 * `StatusBar.svelte` read the active frequency through
 * `getFrequency()` (`stores/radio.svelte.ts`), whose body is
 * `active?.freqHz ?? 0` — the last fabricated zero in shipped presentation,
 * and the last presentation-layer radio-store import. A15 moves that read to
 * the adapter seam.
 *
 * The whole point of this gate is proving no fabricated default survives, so
 * the new accessor is the one place where re-fabricating a zero would
 * silently defeat A15 at its own choke point. These pins exist to stop that:
 * the canonical unknown reading is `null`, never `0`.
 *
 * The store accessor itself is deliberately untouched and stays `?? 0` —
 * `stores/radio.svelte.ts` is a frozen non-owner at A15 (plan §8.2). A15
 * removes the PRESENTATION caller, not the store.
 */
import { describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ view: null as unknown }));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: { get state() { return null; }, get caps() { return null; } },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => null,
}));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({
  toRadioViewModel: () => state.view,
}));

import { getActiveFrequencyHz } from '../panel-adapters';

const vfo = (over: Record<string, unknown>) => ({
  receiver: 'main', slot: { kind: 'unslotted' }, label: 'VFO',
  frequencyHz: null, mode: null, filter: null,
  isActive: false, isActiveSlot: false, ...over,
});

describe('panel-adapters active-frequency accessor (MOR-1409 A15)', () => {
  // Kills: never adding the accessor. Without it StatusBar has no sanctioned
  // adapter-layer path to the active frequency and the `getFrequency()` edge
  // cannot be removed at all.
  it('reads the active VFO frequency when the radio has been observed', () => {
    state.view = { vfos: [vfo({ frequencyHz: 7100000 }), vfo({ isActive: true, frequencyHz: 14074000 })] };
    expect(getActiveFrequencyHz()).toBe(14074000);
  });

  // THE kill of this gate: `?? 0`. An unobserved frequency is unknown, and
  // `0` is a radio-truth claim the radio never made. If this accessor
  // re-fabricates the zero it was created to remove, A15 has moved the defect
  // rather than closed it.
  it('returns null — never 0 — when the active VFO frequency is unobserved', () => {
    state.view = { vfos: [vfo({ isActive: true, frequencyHz: null })] };
    expect(getActiveFrequencyHz()).toBeNull();
    expect(getActiveFrequencyHz()).not.toBe(0);
  });

  // Kills: synthesising an active VFO. "No VFO is active" is a legitimate
  // reading (unknown active receiver); it is not "the first VFO".
  it('returns null when no VFO is the active one', () => {
    state.view = { vfos: [vfo({ frequencyHz: 7100000 }), vfo({ frequencyHz: 14074000 })] };
    expect(getActiveFrequencyHz()).toBeNull();
  });

  // Kills: dereferencing a null view model. `toRadioViewModel` returns null
  // whenever capabilities are absent, which is the state the UI mounts in.
  it('returns null when the view model itself is unavailable', () => {
    state.view = null;
    expect(getActiveFrequencyHz()).toBeNull();
  });
});
