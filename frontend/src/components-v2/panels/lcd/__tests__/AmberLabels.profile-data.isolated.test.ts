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

// MOR-1547 — mirrors `rigs/ftx1.toml`'s `[agc.labels]`. Modes 4/5/6 (the
// auto-selected speeds) were shortened from "A-FAST"/"A-MID"/"A-SLOW" to
// "A-F"/"A-M"/"A-S": the 6-character body pushed the "AGC "-prefixed
// `AmberIndStrip` chip to 10 characters — wider than any other chip sharing
// the strip (the next-widest, "DIGI-SEL", has no "AGC "-style prefix and
// tops out at 8) — which wrapped the DSP zone to a second row that the
// strip's `overflow: hidden` then clipped. "A-F"/"A-M"/"A-S" keeps the
// "Auto-" disambiguator (avoids colliding with the overloaded ham-radio
// abbreviations AM = Amplitude Modulation / AF = Audio Frequency that a bare
// "AF"/"AM"/"AS" would invite) while landing the widest FTX-1 label body at
// 3 characters — inside the existing FAST/SLOW (4-character body) budget
// every other shipped `[agc.labels]` table already fits within.
const FTX1_AGC_LABELS = {
  '0': 'OFF',
  '1': 'FAST',
  '2': 'MID',
  '3': 'SLOW',
  '4': 'A-F',
  '5': 'A-M',
  '6': 'A-S',
};

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

describe('AmberScope AGC chip width budget (MOR-1547)', () => {
  // AmberIndStrip's DSP-zone strip is a `flex-wrap: wrap` row with
  // `overflow: hidden` (AmberIndStrip.svelte:68-128) — a chip wide enough to
  // push the strip past its available width wraps a second row that
  // `overflow: hidden` then silently clips. "AGC FAST"/"AGC SLOW" (8
  // characters) is the established budget every currently-shipped
  // `[agc.labels]` table (ic7300/ic7610/ic705/ic9700/x6200/ftx1 modes 0-3)
  // already renders inside without wrapping; this pins that FTX-1's own
  // auto-mode labels (4/5/6) stay within it too.
  it.each([0, 1, 2, 3, 4, 5, 6])(
    'keeps the AGC chip for FTX-1 mode %i within the single-row width budget',
    (mode) => {
      const caps = { agcLabels: FTX1_AGC_LABELS } as unknown as Capabilities;
      const target = mountScope(stateWithAgc(mode), caps);
      const chip = agcChip(target);
      const text = chip?.textContent?.trim() ?? '';
      expect(text.length).toBeLessThanOrEqual('AGC FAST'.length);
    },
  );

  it('renders the exact shortened auto-mode labels (A-F/A-M/A-S), not the old A-FAST/A-MID/A-SLOW', () => {
    const caps = { agcLabels: FTX1_AGC_LABELS } as unknown as Capabilities;
    expect(agcChip(mountScope(stateWithAgc(4), caps))?.textContent?.trim()).toBe('AGC A-F');
    expect(agcChip(mountScope(stateWithAgc(5), caps))?.textContent?.trim()).toBe('AGC A-M');
    expect(agcChip(mountScope(stateWithAgc(6), caps))?.textContent?.trim()).toBe('AGC A-S');
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
