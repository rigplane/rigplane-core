/**
 * MOR-1679 — AmberFilterGhost width normalization on the corrected FTX-1
 * Table 5 bounds, driven through the REAL props derivation
 * (`toFilterProps` on the GENERATED fixture — see
 * `lib/runtime/adapters/__tests__/fixtures/ftx1-profile.ts`). The ghost
 * itself takes raw Hz numbers; what must not regress is the bound the
 * props layer feeds it: 4000 for SSB/CW on the FTX-1, never the retired
 * 4500-era ceiling. Isolated pool: real mounted component.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import AmberFilterGhost from '../AmberFilterGhost.svelte';
import { toFilterProps } from '$lib/runtime/props/panel-props';
import type { ServerState } from '$lib/types/state';
import { FTX1_CAPABILITIES } from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';

function ftx1State(mode: string, filterWidth: number): ServerState {
  const rx = {
    freqHz: 14_074_000, mode, filter: 1, dataMode: 0, filterWidth,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 0.5, rfGain: 128,
    squelch: 0, sMeter: 0,
  };
  return {
    stateContractVersion: 1, providerGeneration: 1, active: 'MAIN',
    main: rx, sub: { ...rx },
  } as unknown as ServerState;
}

let components: ReturnType<typeof mount>[] = [];

function mountGhost(mode: string, filterWidth: number): string {
  const props = toFilterProps(ftx1State(mode, filterWidth), FTX1_CAPABILITIES);
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(AmberFilterGhost, {
    target,
    props: { filterWidth: props.filterWidth, filterWidthMax: props.filterWidthMax },
  }));
  flushSync();
  return target.querySelector('polyline.passband')?.getAttribute('points') ?? '';
}

afterEach(() => {
  components.forEach((component) => unmount(component));
  components = [];
  document.body.innerHTML = '';
});

describe('MOR-1679 AmberFilterGhost normalization on FTX-1 Table 5 bounds', () => {
  it('normalizes the passband against the 4000 Hz SSB table end, not 4500', () => {
    const props = toFilterProps(ftx1State('USB', 2400), FTX1_CAPABILITIES);
    expect(props.filterWidthMax).toBe(4000);
    expect(props.filterWidthMax).not.toBe(4500);
    expect(mountGhost('USB', 2400))
      .toBe('8,40 19.200000000000003,40 33.2,0 66.8,0 80.8,40 92,40');
  });

  it('opens fully at the 4000 Hz table end', () => {
    expect(mountGhost('USB', 4000)).toBe('8,40 8,40 22,0 78,0 92,40 92,40');
  });

  it('uses the CW-family 50..4000 bounds identically', () => {
    expect(mountGhost('CW-L', 4000)).toBe('8,40 8,40 22,0 78,0 92,40 92,40');
    expect(mountGhost('CW-L', 50)).toBe('8,40 34.4,40 48.4,0 51.6,0 65.6,40 92,40');
  });
});
