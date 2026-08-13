/**
 * AGC / preamp indicator label sourcing (MOR-1529).
 *
 * `AmberCockpit`/`AmberScope` (amber-lcd skin) previously hardcoded an
 * FTX-1-shaped AGC_LABELS dict (0=OFF/1=FAST/2=MID/3=SLOW/4-6=A-F/A-M/A-S)
 * and an IPO/AMP1/AMP2 preamp ternary, applied to every radio regardless of
 * its declared domain. A live X6200 (whose real AGC domain is
 * OFF/FAST/SLOW/AUTO — index 2 = SLOW not MID, index 3 = AUTO not SLOW,
 * per `rigs/x6200.toml`) would show a mislabeled AGC status token.
 *
 * The fix resolves both labels from the profile-declared capabilities
 * payload (`caps.agcLabels` / `caps.preLabels`, already threaded per
 * MOR-1522/#2437 and MOR-1523/#2441) instead of a hardcoded vendor dict.
 * `AmberScope` is mounted with the real `$lib/state/field-status` resolver
 * and the real `AmberIndStrip` renderer; only the runtime/adapter seams and
 * the state-dependent slice of `panel-props` are mocked (`formatPreLabel`,
 * the function under test for preamp, is left as the real implementation).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';

// ── Controlled adapter output ───────────────────────────────────────────────

const scopeProps = vi.hoisted(() => ({
  value: {
    radioState: null as ServerState | null,
    caps: null as Capabilities | null,
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
    scope: { subscribe: vi.fn(() => vi.fn()), hardwareScopeConnected: false },
    defaultScopeStatus: {
      source: null, available: false, resourceSelected: false, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    },
  },
}));

// Only the state-dependent projections are stubbed; `formatPreLabel` (the
// function under test for the preamp label) keeps its real implementation
// via `importOriginal` so this test exercises production code, not a copy.
vi.mock('$lib/runtime/props/panel-props', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/props/panel-props')>();
  return {
    ...actual,
    toTxProps: () => ({
      txActive: false, voxActive: false, compActive: false, compLevel: 0,
      atuActive: false, atuTuning: false, hasTx: false, hasTuner: false, hasMonitor: false,
    }),
    toRitXitProps: () => ({
      ritActive: false, xitActive: false,
      ritOffset: Number.NaN, xitOffset: Number.NaN,
      hasRit: true, hasXit: true,
    }),
    toVfoOpsProps: () => ({ splitActive: false }),
    toDspProps: () => ({ notchMode: 'off', notchFreq: 0 }),
    toFilterProps: () => ({ filterWidth: Number.NaN, filterWidthMax: 9999, ifShift: 0 }),
  };
});

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

function mountScope(state: ServerState | null, caps: Capabilities | null = null) {
  scopeProps.value = {
    radioState: state,
    caps,
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

function preChip(target: HTMLElement): HTMLElement | undefined {
  return Array.from(target.querySelectorAll<HTMLElement>('.lcd-ind'))
    .find((el) => {
      const text = el.textContent?.trim() ?? '';
      return text === 'IPO' || text === 'OFF' || /^(AMP|P)\d/.test(text)
        || text === 'BOOST'; // custom label used by one test below
    });
}

function stateWithAgc(agc: number, preamp = 0): ServerState {
  return {
    active: 'MAIN',
    main: { ...baseReceiver(), agc, preamp },
    sub: baseReceiver(),
    fieldStatus: {
      'main.agc': {
        storePath: 'receiver.main.operator_controls.agc',
        observed: true, freshness: 'fresh', availability: 'available',
      },
      'main.preamp': {
        storePath: 'receiver.main.operator_controls.preamp',
        observed: true, freshness: 'fresh', availability: 'available',
      },
    },
  } as unknown as ServerState;
}

const X6200_AGC_LABELS = { '0': 'OFF', '1': 'FAST', '2': 'SLOW', '3': 'AUTO' };

beforeEach(() => {
  components = [];
  vi.clearAllMocks();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

// ── Tests ───────────────────────────────────────────────────────────────────

describe('AmberScope AGC label sourcing (MOR-1529)', () => {
  it('labels X6200 AGC=3 as AUTO (profile data), not the hardcoded SLOW', () => {
    const caps = { agcLabels: X6200_AGC_LABELS } as unknown as Capabilities;
    const target = mountScope(stateWithAgc(3), caps);
    const chip = agcChip(target);
    expect(chip?.textContent?.trim()).toBe('AGC AUTO');
  });

  it('labels X6200 AGC=2 as SLOW (profile data), not the hardcoded MID', () => {
    const caps = { agcLabels: X6200_AGC_LABELS } as unknown as Capabilities;
    const target = mountScope(stateWithAgc(2), caps);
    const chip = agcChip(target);
    expect(chip?.textContent?.trim()).toBe('AGC SLOW');
  });

  it('falls back to the plain numeric value when no label is declared', () => {
    const caps = { agcLabels: {} } as unknown as Capabilities;
    const target = mountScope(stateWithAgc(9), caps);
    const chip = agcChip(target);
    expect(chip?.textContent?.trim()).toBe('AGC 9');
  });
});

describe('AmberScope preamp label sourcing (MOR-1529)', () => {
  it('renders a profile-declared preamp label instead of the hardcoded IPO/AMP1/AMP2', () => {
    const caps = {
      preLabels: { '0': 'OFF', '1': 'BOOST' },
    } as unknown as Capabilities;
    const target = mountScope(stateWithAgc(2, 1), caps);
    const chip = preChip(target);
    expect(chip?.textContent?.trim()).toBe('BOOST');
  });

  it('does not fabricate Yaesu IPO/AMP1/AMP2 vocabulary for a radio with no declared preamp labels', () => {
    // ic7300/ic7610/x6200 declare no [preamp.labels] section today — the
    // fallback must be the same generic OFF/P{n} used elsewhere in the
    // codebase (panel-props.ts's formatPreLabel), not the FTX-1-specific
    // "IPO"/"AMP1" vocabulary this file used to hardcode unconditionally.
    const caps = { preLabels: {} } as unknown as Capabilities;
    const target = mountScope(stateWithAgc(2, 1), caps);
    const chip = preChip(target);
    expect(chip?.textContent?.trim()).toBe('P1');
  });
});
