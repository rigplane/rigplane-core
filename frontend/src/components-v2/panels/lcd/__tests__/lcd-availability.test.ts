/**
 * LCD availability-gating regression tests (MOR-429).
 *
 * Proves the amber-LCD panels honour `fieldStatus.availability`: an
 * indicator (at minimum AGC) is NOT presented as confirmed/active when its
 * backing field is missing/stale. AmberScope is mounted with the real
 * `$lib/state/field-status` resolver and the real `AmberIndStrip` renderer so
 * the gating path is exercised end to end; only the runtime/adapter seams are
 * mocked to feed a controlled `radioState`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ServerState } from '$lib/types/state';

// ── Controlled adapter output ───────────────────────────────────────────────

const scopeProps = vi.hoisted(() => ({
  value: {
    radioState: null as ServerState | null,
    caps: null,
    hasCapability: (_name: string) => true,
    hasAudioFft: false,
    hasDualReceiver: false,
  },
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAmberScopeProps: () => scopeProps.value,
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    scope: { subscribe: vi.fn(() => vi.fn()) },
  },
}));

// state-adapter is a presentation-only seam here; AGC gating is driven by the
// real field-status resolver, not these adapters.
vi.mock('../../../wiring/state-adapter', () => ({
  toTxProps: () => ({ txActive: false, voxActive: false, compActive: false, compLevel: 0, atuActive: false, atuTuning: false }),
  toRitXitProps: () => ({ ritActive: false, xitActive: false, ritOffset: 0 }),
  toVfoOpsProps: () => ({ splitActive: false }),
  toDspProps: () => ({ notchMode: 'off', notchFreq: 0 }),
  toFilterProps: () => ({ filterWidth: 2400, filterWidthMax: 4000, ifShift: 0 }),
}));

import AmberScope from '../AmberScope.svelte';

// ── Helpers ─────────────────────────────────────────────────────────────────

let components: ReturnType<typeof mount>[] = [];

function baseReceiver() {
  return {
    freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 128, rfGain: 255,
    squelch: 0, agc: 2,
  };
}

function mountScope(state: ServerState | null) {
  scopeProps.value = {
    radioState: state,
    caps: null,
    hasCapability: () => true,
    hasAudioFft: false,
    hasDualReceiver: false,
  };
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(AmberScope, { target });
  flushSync();
  components.push(component);
  return target;
}

function agcChip(target: HTMLElement): HTMLElement | undefined {
  return Array.from(target.querySelectorAll<HTMLElement>('.lcd-ind'))
    .find((el) => el.textContent?.trim().startsWith('AGC'));
}

beforeEach(() => {
  components = [];
  vi.clearAllMocks();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

// ── Tests ───────────────────────────────────────────────────────────────────

describe('AmberScope availability gating (MOR-429)', () => {
  it('renders AGC as active when the field is observed/available', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      fieldStatus: {
        'main.agc': {
          storePath: 'receiver.main.operator_controls.agc',
          observed: true,
          freshness: 'fresh',
          availability: 'available',
        },
      },
    } as unknown as ServerState;

    const target = mountScope(state);
    const chip = agcChip(target);
    expect(chip).toBeDefined();
    expect(chip?.classList.contains('active')).toBe(true);
  });

  it('suppresses the AGC indicator entirely when agc is missing', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      fieldStatus: {
        'main.agc': {
          storePath: 'receiver.main.operator_controls.agc',
          observed: false,
          freshness: 'unknown',
          availability: 'missing',
        },
      },
    } as unknown as ServerState;

    const target = mountScope(state);
    // No AGC chip → the default MID reading is never presented as confirmed.
    expect(agcChip(target)).toBeUndefined();
  });

  it('suppresses the AGC indicator when agc is stale', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      fieldStatus: {
        'main.agc': {
          storePath: 'receiver.main.operator_controls.agc',
          observed: true,
          freshness: 'stale',
          availability: 'stale',
        },
      },
    } as unknown as ServerState;

    const target = mountScope(state);
    expect(agcChip(target)).toBeUndefined();
  });
});
