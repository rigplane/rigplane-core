/**
 * AGC / preamp indicator label sourcing (MOR-1529).
 *
 * `AmberCockpit`/`AmberScope` (amber-lcd skin) previously hardcoded an
 * FTX-1-shaped AGC_LABELS dict
 * and an IPO/AMP1/AMP2 preamp ternary, applied to every radio regardless of
 * its declared domain. A live X6200 (whose real AGC domain is
 * OFF/FAST/SLOW/AUTO — index 2 = SLOW not MID, index 3 = AUTO not SLOW,
 * per `rigs/x6200.toml`) would show a mislabeled AGC status token.
 *
 * Both faces are mounted with the real `$lib/state/field-status` resolver,
 * shared AGC read-back projection, and `AmberIndStrip` renderer. Only the
 * runtime/adapter seams and state-dependent `panel-props` slice are mocked;
 * `formatPreLabel`, the preamp-label function under test, remains real.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';

// ── Controlled adapter output ───────────────────────────────────────────────

const panelProps = vi.hoisted(() => ({
  value: {
    radioState: null as ServerState | null,
    caps: null as Capabilities | null,
    hasCapability: (_name: string) => true,
    hasAudioFft: false,
    hasDualReceiver: false,
  },
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAmberScopeProps: () => panelProps.value,
  deriveAmberCockpitProps: () => panelProps.value,
  deriveAmberTelemetryProps: () => ({ vdRaw: null, idRaw: null }),
  getAmberCockpitHandlers: () => ({ onTuningChange: vi.fn() }),
  getVfoHandlers: () => ({ onFreqChange: vi.fn(), onModeChange: vi.fn() }),
  bindVfoTunerContext: () => ({ read: () => ({ view: null }) }),
}));

vi.mock('$lib/runtime/adapters/qsy-history-adapter', () => ({
  deriveQsyRecent: () => [],
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
import AmberCockpit from '../AmberCockpit.svelte';

// ── Helpers ─────────────────────────────────────────────────────────────────

let components: ReturnType<typeof mount>[] = [];

function baseReceiver() {
  return {
    freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 128, rfGain: 255,
    squelch: 0, agc: 2,
  };
}

function mountFace(
  face: typeof AmberScope | typeof AmberCockpit,
  state: ServerState | null,
  caps: Capabilities | null = null,
) {
  panelProps.value = {
    radioState: state,
    caps,
    hasCapability: () => true,
    hasAudioFft: false,
    hasDualReceiver: false,
  };
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(face, { target });
  flushSync();
  components.push(component);
  return target;
}

const mountScope = (state: ServerState | null, caps: Capabilities | null = null) =>
  mountFace(AmberScope, state, caps);
const mountCockpit = (state: ServerState | null, caps: Capabilities | null = null) =>
  mountFace(AmberCockpit, state, caps);

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

// `projects every FTX-1 AUTO read-back to the settable AUTO label` consumes
// this exact mirror of `rigs/ftx1.toml`; read-back-only 5/6 stay absent here.
const FTX1_AGC_LABELS = {
  '0': 'OFF',
  '1': 'FAST',
  '2': 'MID',
  '3': 'SLOW',
  '4': 'AUTO',
};
const FTX1_AGC_CAPS = {
  agcModes: [0, 1, 2, 3, 4],
  agcLabels: FTX1_AGC_LABELS,
  agcReadback: {
    modes: [0, 1, 2, 3, 4, 5, 6],
    autoMode: 4,
    autoSpeedLabels: { '4': 'FAST', '5': 'MID', '6': 'SLOW' },
  },
} as unknown as Capabilities;
const IC7300_AGC_CAPS = {
  agcModes: [1, 2, 3],
  agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
} as unknown as Capabilities;

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

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('projects FTX-1 read-back 5 to AGC AUTO on %s', (_name, mountFace) => {
    const chip = agcChip(mountFace(stateWithAgc(5), FTX1_AGC_CAPS));
    expect(chip?.textContent?.trim()).toBe('AGC AUTO');
  });

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('renders no numeric text for an unprojectable code on %s', (_name, mountFace) => {
    const chip = agcChip(mountFace(stateWithAgc(9), FTX1_AGC_CAPS));
    expect(chip?.textContent?.trim()).toBe('AGC');
    expect(chip?.textContent).not.toContain('9');
  });

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('keeps IC-7300 read-back 2 as AGC MID on %s', (_name, mountFace) => {
    const chip = agcChip(mountFace(stateWithAgc(2), IC7300_AGC_CAPS));
    expect(chip?.textContent?.trim()).toBe('AGC MID');
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
      const target = mountScope(stateWithAgc(mode), FTX1_AGC_CAPS);
      const chip = agcChip(target);
      const text = chip?.textContent?.trim() ?? '';
      expect(text.length).toBeLessThanOrEqual('AGC FAST'.length);
    },
  );

  it('projects every FTX-1 AUTO read-back to the settable AUTO label', () => {
    expect(agcChip(mountScope(stateWithAgc(4), FTX1_AGC_CAPS))?.textContent?.trim()).toBe('AGC AUTO');
    expect(agcChip(mountScope(stateWithAgc(5), FTX1_AGC_CAPS))?.textContent?.trim()).toBe('AGC AUTO');
    expect(agcChip(mountScope(stateWithAgc(6), FTX1_AGC_CAPS))?.textContent?.trim()).toBe('AGC AUTO');
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
