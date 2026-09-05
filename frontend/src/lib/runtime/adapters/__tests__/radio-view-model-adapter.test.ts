import { createHash } from 'node:crypto';
/**
 * MOR-1065 — the live adapter behind the semantic VFO / RX-TX surfaces.
 *
 * The failure mode this file exists to kill is FABRICATION: an adapter that
 * fills an unobserved receiver with 'MAIN', an unobserved A/B slot with 'A',
 * or an unobserved split with `false` looks perfectly healthy on screen and
 * lies about the radio. Every "unknown" assertion below names the mutation
 * it kills.
 *
 * The contract itself (`semantic/radio-view-model`) cannot be imported by
 * `lib/runtime/**` production code (eslint invariant 1) — test files are
 * exempt, so this is also where the emitted shape is proven to be a real,
 * validator-clean `RadioViewModel`.
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import {
  validateRadioViewModel, type RadioViewModel,
} from '../../../../semantic/radio-view-model';
import { topologyFixtures } from '../../../../semantic/fixtures/topologies';
import { toMemoryPanelProps } from '../../props/panel-props';
import {
  toRadioViewModel, type MetersTxAuthority,
} from '../radio-view-model-adapter';

const DUAL = ['scope', 'audio', 'tx', 'dual_rx'];
const SINGLE = ['scope', 'audio', 'tx'];

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true, capabilities: DUAL,
    receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
    scopeSource: 'hardware', audioFftAvailable: true, ...overrides,
  } as Capabilities;
}

/** One representative capability set per canonical topology fixture. */
const TOPOLOGY_CAPS: Record<string, Capabilities> = {
  '1/single': caps({ vfoScheme: 'single', receivers: 1, capabilities: SINGLE }),
  '1/ab': caps({ vfoScheme: 'ab', receivers: 1, capabilities: SINGLE }),
  '2/ab_shared': caps({ vfoScheme: 'ab_shared', receivers: 2 }),
  '2/main_sub': caps({ vfoScheme: 'main_sub', receivers: 2 }),
};

const fresh: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
};
const stale: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'stale', availability: 'stale',
};

const slot = (freqHz: number, mode = 'USB') => ({
  freqHz, mode, filterNum: 1, dataMode: 0,
});
const receiver = (freqHz: number) => ({
  ...slot(freqHz), vfoA: slot(freqHz), vfoB: slot(freqHz + 50000), activeSlot: 'A',
  filter: 1, att: 0, preamp: 0, nb: false, nr: false, afLevel: 1, rfGain: 1,
  squelch: 0, sMeter: 0,
});

/** A fully observed state: every field this adapter reads is fresh+available. */
function observedState(overrides: Partial<ServerState> = {}): ServerState {
  const paths = [
    'active', 'split', 'dualWatch', 'txTarget', 'main.freqHz', 'main.mode', 'main.filter',
    'sub.freqHz', 'sub.mode', 'sub.filter', 'main.activeSlot', 'sub.activeSlot',
  ];
  for (const key of ['main', 'sub'] as const) {
    for (const vfo of ['vfoA', 'vfoB']) {
      paths.push(`${key}.${vfo}.freqHz`, `${key}.${vfo}.mode`, `${key}.${vfo}.filterNum`);
    }
  }
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    scopeControls: { receiver: 0, dual: false } as ServerState['scopeControls'],
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
    ...overrides,
  } as ServerState;
}

/** Nothing has ever been observed: the payload exists, field status does not. */
const unobservedState = (): ServerState => ({
  ...observedState(), fieldStatus: {},
} as ServerState);

describe('qualified VFO display observations', () => {
  const dc = { ...TOPOLOGY_CAPS['1/single'], stateContractVersion: 1, providerGeneration: 1 };
  function ds(isStale = false): ServerState {
    const state = observedState({ stateContractVersion: 1, providerGeneration: 1 });
    state.fieldStatus = Object.fromEntries(Object.keys(state.fieldStatus!).map((path) =>
      [path, { ...(isStale && /\.(freqHz|mode|filter|filterNum)$/.test(path) ? stale : fresh), lastObservedMonotonic: 0 }]));
    return state;
  }
  const display = (state: ServerState | null, c: Capabilities = dc) => toRadioViewModel(state, c)!.vfos[0].display;
  const strictBaselines: Record<string, unknown> = {
    '1/single:false': [
      {"receiver":"MAIN","slot":{"kind":"unslotted"},"label":"MAIN","frequencyHz":14250000,"mode":"USB","filter":"FIL1","isActive":true,"isActiveSlot":true,"isTxTarget":false},
    ],
    '1/single:true': [
      {"receiver":"MAIN","slot":{"kind":"unslotted"},"label":"MAIN","frequencyHz":null,"mode":null,"filter":null,"isActive":true,"isActiveSlot":true,"isTxTarget":false},
    ],
    '1/ab:false': [
      {"receiver":"MAIN","slot":{"kind":"slotted","id":"A"},"label":"MAIN A","frequencyHz":14250000,"mode":"USB","filter":"FIL1","isActive":true,"isActiveSlot":true,"isTxTarget":true},
      {"receiver":"MAIN","slot":{"kind":"slotted","id":"B"},"label":"MAIN B","frequencyHz":14300000,"mode":"USB","filter":"FIL1","isActive":false,"isActiveSlot":false,"isTxTarget":false},
    ],
    '1/ab:true': [
      {"receiver":"MAIN","slot":{"kind":"slotted","id":"A"},"label":"MAIN A","frequencyHz":null,"mode":null,"filter":null,"isActive":true,"isActiveSlot":true,"isTxTarget":true},
      {"receiver":"MAIN","slot":{"kind":"slotted","id":"B"},"label":"MAIN B","frequencyHz":null,"mode":null,"filter":null,"isActive":false,"isActiveSlot":false,"isTxTarget":false},
    ],
    '2/ab_shared:false': [
      {"receiver":"MAIN","slot":{"kind":"unslotted"},"label":"MAIN","frequencyHz":14250000,"mode":"USB","filter":"FIL1","isActive":true,"isActiveSlot":true,"isTxTarget":false},
      {"receiver":"SUB","slot":{"kind":"unslotted"},"label":"SUB","frequencyHz":14300000,"mode":"USB","filter":"FIL1","isActive":false,"isActiveSlot":true,"isTxTarget":false},
    ],
    '2/ab_shared:true': [
      {"receiver":"MAIN","slot":{"kind":"unslotted"},"label":"MAIN","frequencyHz":null,"mode":null,"filter":null,"isActive":true,"isActiveSlot":true,"isTxTarget":false},
      {"receiver":"SUB","slot":{"kind":"unslotted"},"label":"SUB","frequencyHz":null,"mode":null,"filter":null,"isActive":false,"isActiveSlot":true,"isTxTarget":false},
    ],
    '2/main_sub:false': [
      {"receiver":"MAIN","slot":{"kind":"slotted","id":"A"},"label":"MAIN A","frequencyHz":14250000,"mode":"USB","filter":"FIL1","isActive":true,"isActiveSlot":true,"isTxTarget":true},
      {"receiver":"MAIN","slot":{"kind":"slotted","id":"B"},"label":"MAIN B","frequencyHz":14300000,"mode":"USB","filter":"FIL1","isActive":false,"isActiveSlot":false,"isTxTarget":false},
      {"receiver":"SUB","slot":{"kind":"slotted","id":"A"},"label":"SUB A","frequencyHz":14300000,"mode":"USB","filter":"FIL1","isActive":false,"isActiveSlot":true,"isTxTarget":false},
      {"receiver":"SUB","slot":{"kind":"slotted","id":"B"},"label":"SUB B","frequencyHz":14350000,"mode":"USB","filter":"FIL1","isActive":false,"isActiveSlot":false,"isTxTarget":false},
    ],
    '2/main_sub:true': [
      {"receiver":"MAIN","slot":{"kind":"slotted","id":"A"},"label":"MAIN A","frequencyHz":null,"mode":null,"filter":null,"isActive":true,"isActiveSlot":true,"isTxTarget":true},
      {"receiver":"MAIN","slot":{"kind":"slotted","id":"B"},"label":"MAIN B","frequencyHz":null,"mode":null,"filter":null,"isActive":false,"isActiveSlot":false,"isTxTarget":false},
      {"receiver":"SUB","slot":{"kind":"slotted","id":"A"},"label":"SUB A","frequencyHz":null,"mode":null,"filter":null,"isActive":false,"isActiveSlot":true,"isTxTarget":false},
      {"receiver":"SUB","slot":{"kind":"slotted","id":"B"},"label":"SUB B","frequencyHz":null,"mode":null,"filter":null,"isActive":false,"isActiveSlot":false,"isTxTarget":false},
    ],
  };

  it.each([false, true, false])('qualifies first accepted snapshots with stale=%s', (isStale) => {
    expect(display(ds(isStale))).toEqual({
      frequencyHz: { state: isStale ? 'stale' : 'current', value: 14250000 },
      mode: { state: isStale ? 'stale' : 'current', value: 'USB' },
      filter: { state: isStale ? 'stale' : 'current', value: 'FIL1' },
    });
  });
  it('accepts zero frequency/filter and rejects non-scalar or empty values', () => {
    const state = ds(true);
    state.main = { ...state.main, freqHz: 0, filter: 0 };
    expect(display(state)?.frequencyHz).toEqual({ state: 'stale', value: 0 });
    expect(display(state)?.filter).toEqual({ state: 'stale', value: 'FIL0' });
    state.main = { ...state.main, freqHz: NaN, mode: '', filter: Infinity };
    expect(Object.values(display(state)!)).toEqual(Array(3).fill({ state: 'unknown', reason: 'invalid-value' }));
    state.main.freqHz = false as unknown as number;
    expect(display(state)?.frequencyHz).toEqual({ state: 'unknown', reason: 'invalid-value' });
  });
  it.each([
    undefined, { ...fresh, observed: false }, { ...fresh },
    { ...fresh, lastObservedMonotonic: -1 },
    { ...fresh, lastObservedMonotonic: 1, availability: 'unavailable' },
  ])('rejects missing or invalid leaf evidence %#', (status) => {
    const state = ds();
    state.fieldStatus!['main.freqHz'] = status as FieldStatus;
    expect(display(state)?.frequencyHz.state).toBe('unknown');
  });
  it.each(['main', 'main.vfoA'])('honors ancestor veto and ancestor staleness at %s', (path) => {
    const c = { ...dc, vfoScheme: 'ab' as const, vfoReadback: 'absolute' as const };
    const state = ds();
    state.fieldStatus![path] = { ...fresh, observed: false, lastObservedMonotonic: 0 };
    expect(display(state, c)?.frequencyHz).toEqual({ state: 'unknown', reason: 'not-observed' });
    state.fieldStatus![path] = { ...stale, lastObservedMonotonic: 0 };
    expect(display(state, c)?.frequencyHz).toEqual({ state: 'stale', value: 14250000 });
  });
  it('fences capability generation, contract, reset and unresolved slot identity without caching', () => {
    expect(display(ds())?.frequencyHz.state).toBe('current');
    for (const c of [{ ...dc, providerGeneration: 2 }, { ...dc, stateContractVersion: undefined }]) {
      expect(display(ds(), c)?.frequencyHz).toEqual({ state: 'unknown', reason: 'identity-unresolved' });
    }
    expect(display(null)?.frequencyHz.state).toBe('unknown');
    const state = ds();
    state.main = { ...state.main, vfoA: undefined, vfoB: undefined };
    expect(display(state, { ...dc, vfoScheme: 'ab', vfoReadback: 'absolute' })?.frequencyHz)
      .toEqual({ state: 'unknown', reason: 'identity-unresolved' });
    expect(display(ds(true))?.frequencyHz).toEqual({ state: 'stale', value: 14250000 });
  });
  it.each([
    { providerGeneration: undefined }, { providerGeneration: -1 },
    { stateContractVersion: undefined }, { active: 'SUB' as const },
  ])('rejects unresolved state identity %#', (overrides) => {
    expect(display({ ...ds(), ...overrides })?.frequencyHz)
      .toEqual({ state: 'unknown', reason: 'identity-unresolved' });
  });
  it('does not carry a receiver display into a new generation or fabricate a missing value', () => {
    const first = ds(true);
    const next = ds(true);
    next.providerGeneration = 2;
    next.main.freqHz = 7100000;
    const nextCaps = { ...dc, providerGeneration: 2 };
    expect(display(first)?.frequencyHz).toEqual({ state: 'stale', value: 14250000 });
    expect(display(next, nextCaps)?.frequencyHz).toEqual({ state: 'stale', value: 7100000 });
    next.fieldStatus = {};
    expect(display(next, nextCaps)?.frequencyHz).toEqual({ state: 'unknown', reason: 'not-observed' });
    const missing = ds();
    missing.main.freqHz = undefined as unknown as number;
    expect(display(missing)?.frequencyHz).toEqual({ state: 'unknown', reason: 'invalid-value' });
  });
  it.each(Object.keys(TOPOLOGY_CAPS))('uses each %s position path and preserves strict baseline', (id) => {
    const c = { ...TOPOLOGY_CAPS[id], stateContractVersion: 1, providerGeneration: 1 };
    for (const isStale of [false, true]) {
      const state = ds(isStale);
      const view = toRadioViewModel(state, c)!;
      const strict = view.vfos.map(({ display: _display, ...vfo }) => vfo);
      const baseline = topologyFixtures[id as keyof typeof topologyFixtures].vfos;
      // Capture the complete pre-change projection separately from display assertions.
      expect(strict).toEqual(strictBaselines[`${id}:${isStale}`]);
      expect(view.vfos).toHaveLength(baseline.length);
      for (const vfo of view.vfos) {
        const rx = vfo.receiver === 'MAIN' ? state.main : state.sub;
        const raw = vfo.slot.kind === 'slotted' ? rx[vfo.slot.id === 'A' ? 'vfoA' : 'vfoB']! : rx;
        expect(vfo.display?.frequencyHz).toEqual({ state: isStale ? 'stale' : 'current', value: raw.freqHz });
      }
    }
  });
  it('keeps relative selected/unselected provenance separate from absolute A/B', () => {
    const state = ds(true);
    delete state.fieldStatus!['main.activeSlot'];
    state.main.unselectedVfo = slot(7100000, 'LSB');
    for (const leaf of ['freqHz', 'mode', 'filterNum']) {
      state.fieldStatus![`main.unselectedVfo.${leaf}`] = { ...stale, lastObservedMonotonic: 0 };
    }
    const c = { ...dc, vfoScheme: 'ab' as const, vfoReadback: 'selected_unselected' as const };
    const view = toRadioViewModel(state, c)!;
    expect(view.vfos.map((vfo) => [vfo.slot, vfo.display?.frequencyHz, vfo.isActiveSlot])).toEqual([
      [{ kind: 'relative', role: 'selected' }, { state: 'stale', value: 14250000 }, true],
      [{ kind: 'relative', role: 'unselected' }, { state: 'stale', value: 7100000 }, false],
    ]);
    state.main.unselectedVfo = undefined;
    expect(toRadioViewModel(state, c)!.vfos[1].display?.frequencyHz.state).toBe('unknown');
  });
});

function model(
  state: ServerState | null, capabilities: Capabilities | null,
  tx?: MetersTxAuthority | null,
): RadioViewModel {
  const view = toRadioViewModel(state, capabilities, tx);
  expect(view).not.toBeNull();
  // The single most important assertion in this file: the adapter's output is
  // a real view model by the contract's OWN validator, cross-field invariants
  // included (no 'allowed' permit under an unknown target, no orphan
  // isTxTarget). `lib/runtime` cannot import this type — the test can.
  return validateRadioViewModel(view);
}

const INDICATOR_CAPS = caps({
  filters: ['FIL1'],
  agcLabels: { '2': 'SLOW' },
  capabilities: [
    ...DUAL, 'agc', 'nb', 'nr', 'notch', 'attenuator', 'preamp',
    'rf_gain', 'digisel', 'ip_plus',
  ],
});
const INDICATOR_LEAVES = [
  'sMeter', 'filterWidth', 'agc', 'nb', 'nr', 'autoNotch', 'manualNotch',
  'att', 'preamp', 'rfGain', 'digisel', 'ipplus',
] as const;
const RECEIVING: MetersTxAuthority = { radioTx: 'off', txRisk: 'none' };

function indicatorState(overrides: Partial<ServerState> = {}): ServerState {
  const base = observedState();
  const fieldStatus = { ...base.fieldStatus } as Record<string, FieldStatus>;
  for (const receiverKey of ['main', 'sub'] as const) {
    for (const leaf of INDICATOR_LEAVES) fieldStatus[`${receiverKey}.${leaf}`] = fresh;
  }
  return {
    ...base,
    main: {
      ...base.main, sMeter: 0, filterWidth: 2400, agc: 0, nb: false, nr: true,
      autoNotch: false, manualNotch: false, att: 0, preamp: 0, rfGain: 0,
      digisel: false, ipplus: true,
    },
    sub: {
      ...base.sub, sMeter: -37, filterWidth: 500, agc: 2, nb: true, nr: false,
      autoNotch: true, manualNotch: false, att: 12, preamp: 2, rfGain: 0.75,
      digisel: true, ipplus: false,
    },
    fieldStatus,
    ...overrides,
  } as ServerState;
}

describe('receiver indicators are structural-receiver addressed (MOR-2299 slice 1)', () => {
  it.each([
    ['1/single', ['MAIN']],
    ['1/ab', ['MAIN']],
    ['2/ab_shared', ['MAIN', 'SUB']],
    ['2/main_sub', ['MAIN', 'SUB']],
  ] as const)('%s emits one row per structural receiver, never per A/B slot', (id, receivers) => {
    const view = model(indicatorState(), TOPOLOGY_CAPS[id]);
    expect(view.receiverIndicators?.map((indicator) => indicator.receiver)).toEqual(receivers);
  });

  it('keeps an unavailable structural SUB present but unknown and disabled', () => {
    const view = model(
      indicatorState(),
      { ...INDICATOR_CAPS, capabilities: INDICATOR_CAPS.capabilities.filter((tag) => tag !== 'dual_rx') },
      RECEIVING,
    );
    const sub = view.receiverIndicators?.find((indicator) => indicator.receiver === 'SUB');
    expect(sub?.availability).toEqual({ structural: true, operational: false });
    expect(sub?.sMeter.reading).toEqual({ status: 'unknown' });
    expect(sub?.nbActive.reading).toEqual({ status: 'unknown' });
  });

  it('keeps deliberately different MAIN/SUB facts distinct across active-receiver switches', () => {
    const before = model(indicatorState({ active: 'MAIN' }), INDICATOR_CAPS, RECEIVING);
    const after = model(indicatorState({ active: 'SUB' }), INDICATOR_CAPS, RECEIVING);
    const signature = (view: RadioViewModel) => view.receiverIndicators?.map((indicator) => ({
      receiver: indicator.receiver,
      s: indicator.sMeter.reading,
      bw: indicator.bandwidthHz.reading,
      agc: indicator.agcMode.reading,
      nb: indicator.nbActive.reading,
      att: indicator.attenuator.reading,
      pre: indicator.preamp.reading,
      rfg: indicator.rfGain.reading,
      digi: indicator.digiSel.reading,
      ip: indicator.ipPlus.reading,
    }));
    expect(signature(after)).toEqual(signature(before));
    expect(signature(before)).toEqual([
      {
        receiver: 'MAIN', s: { status: 'known', value: 0 },
        bw: { status: 'known', value: 2400 }, agc: { status: 'known', value: 0 },
        nb: { status: 'known', value: false }, att: { status: 'known', value: 0 },
        pre: { status: 'known', value: 0 }, rfg: { status: 'known', value: 0 },
        digi: { status: 'known', value: false }, ip: { status: 'known', value: true },
      },
      {
        receiver: 'SUB', s: { status: 'known', value: -37 },
        bw: { status: 'known', value: 500 }, agc: { status: 'known', value: 'SLOW' },
        nb: { status: 'known', value: true }, att: { status: 'known', value: 12 },
        pre: { status: 'known', value: 2 }, rfg: { status: 'known', value: 0.75 },
        digi: { status: 'known', value: true }, ip: { status: 'known', value: false },
      },
    ]);
  });

  it.each([
    ['missing leaf', (statuses: Record<string, FieldStatus>) => { delete statuses['main.sMeter']; }],
    ['unobserved leaf', (statuses: Record<string, FieldStatus>) => {
      statuses['main.sMeter'] = { ...fresh, observed: false };
    }],
    ['stale leaf', (statuses: Record<string, FieldStatus>) => { statuses['main.sMeter'] = stale; }],
    ['stale parent', (statuses: Record<string, FieldStatus>) => { statuses.main = stale; }],
    ['parent-only', (statuses: Record<string, FieldStatus>) => {
      delete statuses['main.sMeter'];
      statuses.main = fresh;
    }],
  ] as const)('%s leaves the S-meter unknown', (_label, mutate) => {
    const state = indicatorState();
    const fieldStatus = { ...state.fieldStatus } as Record<string, FieldStatus>;
    mutate(fieldStatus);
    const main = model({ ...state, fieldStatus }, INDICATOR_CAPS, RECEIVING)
      .receiverIndicators?.find((indicator) => indicator.receiver === 'MAIN');
    expect(main?.sMeter.reading).toEqual({ status: 'unknown' });
    expect(main?.sMeter.availability.operational).toBe(false);
  });

  it('uses one App-authority RF fact only: ptt and assignment cannot override it', () => {
    const pttFalse = indicatorState({ ptt: false });
    const pttTrue = indicatorState({ ptt: true });
    const transmitting = model(
      pttFalse, INDICATOR_CAPS, { radioTx: 'on', txRisk: 'none' },
    ).radioWideIndicators!;
    expect(transmitting.rfState).toBe('transmitting');

    const receiving = model(pttTrue, INDICATOR_CAPS, RECEIVING).radioWideIndicators!;
    expect(receiving.rfState).toBe('receiving');

    const assignmentOnly = model(indicatorState({
      ptt: true,
      txTarget: { status: 'known', receiver: 'SUB', slot: 'A', frequencyHz: 14_300_000 },
    }), INDICATOR_CAPS).radioWideIndicators!;
    expect(assignmentOnly.rfState).toBe('unknown');
  });
});

const RADIO_WIDE_CAPS = caps({
  antennas: 1,
  capabilities: [
    ...DUAL, 'tuner', 'rit', 'xit', 'split', 'dual_watch',
    'vfo_equalize', 'vfo_swap', 'speech',
  ],
});

function radioWideState(overrides: Partial<ServerState> = {}): ServerState {
  const base = observedState();
  const fieldStatus = { ...base.fieldStatus } as Record<string, FieldStatus>;
  for (const path of ['tunerStatus', 'ritOn', 'ritTx', 'ritFreq', 'txAntenna']) {
    fieldStatus[path] = fresh;
  }
  return {
    ...base,
    tunerStatus: 0, ritOn: false, ritTx: true, ritFreq: 0, txAntenna: 1,
    fieldStatus,
    ...overrides,
  } as ServerState;
}

describe('radio-wide indicators and DUAL actions are singleton contract facts (MOR-2309)', () => {
  it('preserves one-port ANT, ATU off, false RIT and zero shared offset as known values', () => {
    const shared = model(radioWideState(), RADIO_WIDE_CAPS).radioWideIndicators!;
    expect(shared.rfState).toBe('unknown');
    expect(shared.antenna).toEqual({
      reading: { status: 'known', value: 1 },
      availability: { structural: true, operational: true },
    });
    expect(shared.atu.reading).toEqual({ status: 'known', value: 'off' });
    expect(shared.ritActive.reading).toEqual({ status: 'known', value: false });
    expect(shared.ritOffset.reading).toEqual({ status: 'known', value: 0 });
    expect(shared.xitActive.reading).toEqual({ status: 'known', value: true });
    expect(shared.xitOffset.reading).toEqual({ status: 'known', value: 0 });
  });

  const sharedLeafCases = [
    ['txAntenna', (shared: NonNullable<RadioViewModel['radioWideIndicators']>) => shared.antenna],
    ['tunerStatus', (shared: NonNullable<RadioViewModel['radioWideIndicators']>) => shared.atu],
    ['ritOn', (shared: NonNullable<RadioViewModel['radioWideIndicators']>) => shared.ritActive],
    ['ritFreq', (shared: NonNullable<RadioViewModel['radioWideIndicators']>) => shared.ritOffset],
    ['ritTx', (shared: NonNullable<RadioViewModel['radioWideIndicators']>) => shared.xitActive],
  ] as const;
  const rejectedStatuses = [
    ['missing', undefined],
    ['observed false', { ...fresh, observed: false }],
    ['stale', stale],
    ['explicit unavailable', { ...fresh, availability: 'missing' as const }],
  ] as const;

  it.each(sharedLeafCases)('%s requires its own observed/fresh/available leaf', (path, select) => {
    for (const [label, rejected] of rejectedStatuses) {
      const state = radioWideState();
      const fieldStatus = { ...state.fieldStatus } as Record<string, FieldStatus>;
      if (rejected === undefined) delete fieldStatus[path];
      else fieldStatus[path] = rejected;
      const field = select(model({ ...state, fieldStatus }, RADIO_WIDE_CAPS).radioWideIndicators!);
      expect(field.reading, label).toEqual({ status: 'unknown' });
      expect(field.availability.operational, label).toBe(false);
    }
  });

  it('the shared RIT offset gate applies independently to both displayed aggregates', () => {
    const state = radioWideState();
    const fieldStatus = { ...state.fieldStatus } as Record<string, FieldStatus>;
    fieldStatus.ritFreq = { ...fresh, observed: false };
    const shared = model({ ...state, fieldStatus }, RADIO_WIDE_CAPS).radioWideIndicators!;
    expect(shared.ritOffset.reading).toEqual({ status: 'unknown' });
    expect(shared.xitOffset.reading).toEqual({ status: 'unknown' });
    expect(shared.ritOffset.availability.operational).toBe(false);
  });

  it('publishes exact primitive action availability and fails composite Quick actions closed', () => {
    const actions = model(radioWideState(), RADIO_WIDE_CAPS).radioWideIndicators!.actions;
    expect(actions).toEqual({
      main: { structural: true, operational: true },
      sub: { structural: true, operational: true },
      equalize: { structural: true, operational: true },
      swap: { structural: true, operational: true },
      quickSplit: { structural: false, operational: false },
      quickDualWatch: { structural: false, operational: false },
      speak: { structural: true, operational: true },
    });

    const unavailableSub = model(
      radioWideState(),
      { ...RADIO_WIDE_CAPS, capabilities: RADIO_WIDE_CAPS.capabilities.filter((tag) => tag !== 'dual_rx') },
    ).radioWideIndicators!.actions;
    expect(unavailableSub.main).toEqual({ structural: true, operational: true });
    expect(unavailableSub.sub).toEqual({ structural: true, operational: false });
    expect(unavailableSub.quickDualWatch).toEqual({ structural: false, operational: false });
  });

  it.each([
    ['vfo_equalize', 'equalize'],
    ['vfo_swap', 'swap'],
    ['speech', 'speak'],
  ] as const)('removing %s removes only the %s action', (capability, action) => {
    const actions = model(radioWideState(), {
      ...RADIO_WIDE_CAPS,
      capabilities: RADIO_WIDE_CAPS.capabilities.filter((tag) => tag !== capability),
    }).radioWideIndicators!.actions;
    expect(actions[action]).toEqual({ structural: false, operational: false });
    const expectedPresent = {
      equalize: action !== 'equalize', swap: action !== 'swap', speak: action !== 'speak',
    } as const;
    expect(actions.equalize).toEqual({ structural: expectedPresent.equalize, operational: expectedPresent.equalize });
    expect(actions.swap).toEqual({ structural: expectedPresent.swap, operational: expectedPresent.swap });
    expect(actions.speak).toEqual({ structural: expectedPresent.speak, operational: expectedPresent.speak });
    expect(actions.main).toEqual({ structural: true, operational: true });
    expect(actions.sub).toEqual({ structural: true, operational: true });
    expect(actions.quickSplit).toEqual({ structural: false, operational: false });
    expect(actions.quickDualWatch).toEqual({ structural: false, operational: false });
  });

  it('publishes an exact per-action absence matrix for unsupported controls', () => {
    const noActions = model(
      radioWideState(),
      caps({ vfoScheme: 'single', receivers: 1, antennas: 1, capabilities: ['tx'] }),
    ).radioWideIndicators!.actions;
    expect(noActions).toEqual({
      main: { structural: false, operational: false },
      sub: { structural: false, operational: false },
      equalize: { structural: false, operational: false },
      swap: { structural: false, operational: false },
      quickSplit: { structural: false, operational: false },
      quickDualWatch: { structural: false, operational: false },
      speak: { structural: false, operational: false },
    });
  });

  it('never emits deprecated receiver RF state in live output', () => {
    const view = model(radioWideState(), RADIO_WIDE_CAPS, RECEIVING);
    expect(view.receiverIndicators).toBeDefined();
    for (const indicator of view.receiverIndicators ?? []) {
      expect(Object.hasOwn(indicator, 'rfState')).toBe(false);
    }
  });
});

describe('topology is derived from real capabilities', () => {
  it.each(Object.keys(TOPOLOGY_CAPS))('%s is reachable and validator-clean', (id) => {
    const view = model(observedState(), TOPOLOGY_CAPS[id]);
    expect(view.topologyId).toBe(id);
    expect(view.vfoScheme).toBe(topologyFixtures[id as keyof typeof topologyFixtures].vfoScheme);
  });

  it('emits the structural VFO positions each scheme implies', () => {
    const shape = (id: string) => model(observedState(), TOPOLOGY_CAPS[id]).vfos
      .map((v) => `${v.receiver}:${v.slot.kind === 'slotted' ? v.slot.id : v.slot.kind}`);
    expect(shape('1/single')).toEqual(['MAIN:unslotted']);
    expect(shape('1/ab')).toEqual(['MAIN:A', 'MAIN:B']);
    expect(shape('2/ab_shared')).toEqual(['MAIN:unslotted', 'SUB:unslotted']);
    expect(shape('2/main_sub')).toEqual(['MAIN:A', 'MAIN:B', 'SUB:A', 'SUB:B']);
  });

  it('renders nothing rather than guessing when capabilities are absent or contradictory', () => {
    expect(toRadioViewModel(observedState(), null)).toBeNull();
    // receivers=2 under a single-receiver scheme is `invalid-topology`; a
    // guessed topology here would silently mis-address every TX decision.
    expect(toRadioViewModel(observedState(), caps({ vfoScheme: 'ab', receivers: 2 }))).toBeNull();
  });
});

describe('unobserved facts survive as the explicit unknown branch', () => {
  it('projects fresh relative values without fabricating A/B identity', () => {
    const base = observedState();
    const fieldStatus = {
      ...base.fieldStatus,
      'main.freqHz': fresh,
      'main.mode': fresh,
      'main.filter': fresh,
      'main.unselectedVfo.freqHz': fresh,
      'main.unselectedVfo.mode': fresh,
      'main.unselectedVfo.filterNum': fresh,
    } as Record<string, FieldStatus>;
    delete fieldStatus['main.activeSlot'];
    const view = model({
      ...base,
      main: {
        ...base.main,
        unselectedVfo: slot(7_100_000, 'LSB'),
      },
      fieldStatus,
    } as ServerState, caps({
      vfoScheme: 'ab', receivers: 1, capabilities: SINGLE,
      vfoReadback: 'selected_unselected',
    }));

    expect(view.vfos.map((vfo) => [vfo.slot, vfo.frequencyHz, vfo.label])).toEqual([
      [{ kind: 'relative', role: 'selected' }, 14_250_000, 'Selected VFO'],
      [{ kind: 'relative', role: 'unselected' }, 7_100_000, 'Unselected VFO'],
    ]);
    expect(view.vfos.filter((vfo) => vfo.isActive)).toHaveLength(1);
    expect(view.vfos.some((vfo) => vfo.slot.kind === 'slotted')).toBe(false);
  });

  it('keeps selected-only relative readback neutral while the unselected tuple is unavailable', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, FieldStatus>;
    delete fieldStatus['main.activeSlot'];
    const capabilities = caps({
      vfoScheme: 'ab', receivers: 1, capabilities: SINGLE,
      vfoReadback: 'selected_unselected',
    });
    const state = {
      ...base,
      main: { ...base.main, unselectedVfo: undefined },
      fieldStatus,
    } as ServerState;

    const view = model(state, capabilities);
    expect(view.vfos.map((vfo) => [vfo.slot, vfo.frequencyHz])).toEqual([
      [{ kind: 'relative', role: 'selected' }, 14_250_000],
      [{ kind: 'relative', role: 'unselected' }, null],
    ]);
    expect(view.vfos.some((vfo) => vfo.slot.kind === 'slotted')).toBe(false);
    expect(toMemoryPanelProps(state, capabilities).vfoIdentityKnown).toBe(false);
  });

  it('keeps an older selected-only payload neutral without local persistence guesses', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, FieldStatus>;
    delete fieldStatus['main.activeSlot'];
    for (const slotKey of ['vfoA', 'vfoB']) {
      for (const leaf of ['freqHz', 'mode', 'filterNum', 'dataMode']) {
        delete fieldStatus[`main.${slotKey}.${leaf}`];
      }
    }
    const capabilities = caps({
      vfoScheme: 'ab', receivers: 1, capabilities: SINGLE,
    });
    const state = {
      ...base,
      main: {
        ...base.main, vfoA: undefined, vfoB: undefined, unselectedVfo: undefined,
      },
      fieldStatus,
    } as ServerState;

    const view = model(state, capabilities);
    expect(view.vfos.map((vfo) => [vfo.slot, vfo.frequencyHz])).toEqual([
      [{ kind: 'relative', role: 'selected' }, 14_250_000],
      [{ kind: 'relative', role: 'unselected' }, null],
    ]);
    expect(toMemoryPanelProps(state, capabilities).vfoIdentityKnown).toBe(false);
  });

  it('leaves an explicitly absolute single-RX A/B contract literal', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, FieldStatus>;
    delete fieldStatus['main.activeSlot'];
    const view = model({ ...base, fieldStatus } as ServerState, caps({
      vfoScheme: 'ab', receivers: 1, capabilities: SINGLE, vfoReadback: 'absolute',
    }));
    expect(view.vfos.map((vfo) => vfo.slot)).toEqual([
      { kind: 'slotted', id: 'A' }, { kind: 'slotted', id: 'B' },
    ]);
  });

  // MUTATION KILLED: `activeReceiver: { status: 'known', receiver: state.active ?? 'MAIN' }`
  // — the classic "default to MAIN" that MOR-988 §3.2 forbids.
  it('never fabricates an active receiver, and marks no VFO active', () => {
    const view = model(unobservedState(), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.activeReceiver).toEqual({ status: 'unknown' });
    expect(view.vfos.some((v) => v.isActive)).toBe(false);
    expect(view.disabledReasons).toContainEqual({
      field: 'activeReceiver', code: 'field-not-observed',
    });
  });

  // MUTATION KILLED: `split: { status: 'known', value: state.split ?? false }`.
  it.each(['split', 'dualWatch'] as const)('never fabricates %s as off', (field) => {
    expect(model(unobservedState(), TOPOLOGY_CAPS['2/main_sub'])[field])
      .toEqual({ status: 'unknown' });
  });

  // MUTATION KILLED: enumerating `['A', 'B']` for a slotted scheme whose slot
  // view was never sent — one fabricated 'A' position and the operator reads a
  // slot identity the radio never reported.
  it('degrades an unobserved A/B slot view to slot.kind "unknown", not to "A"', () => {
    const view = model(observedState({
      main: { ...receiver(14250000), vfoA: undefined, vfoB: undefined },
    } as Partial<ServerState>), TOPOLOGY_CAPS['1/ab']);
    expect(view.vfos).toHaveLength(1);
    expect(view.vfos[0].slot).toEqual({ kind: 'unknown' });
    expect(view.vfos.some((v) => v.slot.kind === 'slotted')).toBe(false);
  });

  // MUTATION KILLED: reading `<rx>.activeSlot` ungated. The backend DEFAULTS
  // that field (`state_schema.py`: `activeSlot: str = "A"`), so an ungated
  // read highlights MAIN A as active on evidence the radio never provided —
  // a fabricated fact on an operator display, in a file whose header promises
  // every radio fact is field-status gated.
  it('marks no slot active when <rx>.activeSlot was never observed', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, unknown>;
    delete fieldStatus['main.activeSlot'];
    delete fieldStatus['sub.activeSlot'];
    const view = model(
      { ...base, fieldStatus } as unknown as ServerState, TOPOLOGY_CAPS['2/main_sub'],
    );

    expect(view.vfos.filter((v) => v.isActive)).toEqual([]);
    // The slot IDENTITIES stay structurally known (the slot view WAS observed);
    // only "which one is active" is unknown. The two must not collapse.
    expect(view.vfos.map((v) => v.slot.kind))
      .toEqual(['slotted', 'slotted', 'slotted', 'slotted']);
  });

  it('marks the observed slot active once activeSlot IS reported', () => {
    const view = model(observedState(), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.vfos.filter((v) => v.isActive).map((v) => v.label)).toEqual(['MAIN A']);
  });

  it('projects each newer coherent bound alias revision without client arbitration', () => {
    const capabilities = caps({
      vfoScheme: 'ab', receivers: 1, capabilities: SINGLE,
      vfoReadback: 'selected_unselected',
    });
    for (const frequencyHz of [14_164_000, 14_130_000, 14_123_000]) {
      const base = observedState();
      const view = model({
        ...base,
        main: {
          ...base.main,
          freqHz: frequencyHz,
          vfoA: slot(frequencyHz),
          vfoB: slot(14_076_000),
          activeSlot: 'A',
        },
      } as ServerState, capabilities);
      expect(view.vfos.map((vfo) => [vfo.label, vfo.frequencyHz])).toEqual([
        ['MAIN A', frequencyHz],
        ['MAIN B', 14_076_000],
      ]);
    }
  });

  // ── MOR-1335 (G4): the per-receiver active slot ─────────────────────────
  //
  // `isActive` answers "is this the ACTIVE RECEIVER's active VFO" and is
  // therefore globally unique — which is why the VFO surface, gating tuning on
  // it, left SUB untunable on `2/main_sub`. `isActiveSlot` answers the
  // per-receiver question, so each receiver names the VFO its own
  // receiver-scoped `set_freq` would write.

  // MUTATION KILLED: deriving `isActiveSlot` from the ACTIVE RECEIVER (i.e.
  // aliasing `isActive`) — SUB would report no active slot at all and the
  // parity gap this fact exists to close would silently persist.
  it('names each receiver\'s own active slot, including the receiver that is NOT active', () => {
    const view = model(observedState({
      sub: { ...receiver(14300000), activeSlot: 'B' },
    } as Partial<ServerState>), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.activeReceiver).toEqual({ status: 'known', receiver: 'MAIN' });
    expect(view.vfos.filter((v) => v.isActiveSlot).map((v) => v.label))
      .toEqual(['MAIN A', 'SUB B']);
    // ...and the radio-wide fact is unchanged by it: still exactly one.
    expect(view.vfos.filter((v) => v.isActive).map((v) => v.label)).toEqual(['MAIN A']);
  });

  // MUTATION KILLED: `slot.id === (rx.activeSlot ?? 'A')` — the backend
  // DEFAULTS activeSlot to "A", so an ungated read would hand the surface a
  // tunable MAIN A / SUB A on evidence the radio never provided.
  it('marks NO active slot for a receiver whose activeSlot was never observed', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, unknown>;
    delete fieldStatus['sub.activeSlot'];
    const view = model(
      { ...base, fieldStatus } as unknown as ServerState, TOPOLOGY_CAPS['2/main_sub'],
    );
    expect(view.vfos.filter((v) => v.receiver === 'SUB' && v.isActiveSlot)).toEqual([]);
    // Non-vacuous: MAIN's observed reading still names its slot.
    expect(view.vfos.filter((v) => v.isActiveSlot).map((v) => v.label)).toEqual(['MAIN A']);
  });

  // An unslotted position IS its receiver's active slot — there is no other
  // VFO on that receiver for `set_freq` to write. Kills a derivation keyed to
  // a slotted id, which would leave every `single`/`ab_shared` position
  // untunable.
  it.each(['1/single', '2/ab_shared'] as const)('%s: every unslotted position is its receiver\'s active slot', (id) => {
    const view = model(observedState(), TOPOLOGY_CAPS[id]);
    expect(view.vfos.every((v) => v.isActiveSlot)).toBe(true);
  });

  // The decomposition, stated once over the whole matrix: `isActive` is
  // exactly "this receiver is the active one AND this is its active slot".
  it.each(Object.keys(TOPOLOGY_CAPS))('%s: isActive === active receiver AND isActiveSlot', (id) => {
    const view = model(observedState(), TOPOLOGY_CAPS[id]);
    for (const vfo of view.vfos) {
      const receiverActive = view.activeReceiver.status === 'known'
        && view.activeReceiver.receiver === vfo.receiver;
      expect(vfo.isActive, vfo.label).toBe(receiverActive && vfo.isActiveSlot);
    }
  });

  // MUTATION KILLED: reading `state.txTarget` without the freshness gate — a
  // stale target keys the wrong VFO.
  it('reports a stale TX target as unknown/stale and blocks the permit', () => {
    const view = model(observedState({
      fieldStatus: { ...observedState().fieldStatus, txTarget: stale },
    }), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.txTarget).toEqual({ status: 'unknown', reason: 'stale' });
    expect(view.txPermit.status).not.toBe('allowed');
    expect(view.vfos.some((v) => v.isTxTarget)).toBe(false);
  });

  // MUTATION KILLED: `frequencyHz: rx.freqHz ?? 14074000` (the legacy
  // `toVfoProps` default) — a plausible-looking frequency for an unread radio.
  it('nulls unobserved frequency / mode / filter instead of defaulting them', () => {
    const view = model(unobservedState(), TOPOLOGY_CAPS['1/single']);
    expect(view.vfos[0]).toMatchObject({ frequencyHz: null, mode: null, filter: null });
  });

  it('keeps observed readings when the field status backs them', () => {
    const view = model(observedState(), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.vfos[0]).toMatchObject({
      receiver: 'MAIN', frequencyHz: 14250000, mode: 'USB', filter: 'FIL1',
      isActive: true, isTxTarget: true,
    });
  });
});

/**
 * MOR-1421 — `active` is structurally impossible to positively observe on
 * some single-receiver radios: the live IC-7300 stand's `fieldStatus.active`
 * reads observed:false/availability:'missing' FOREVER (confirmed against a
 * live `/api/v1/state` probe, 2026-08-10 — `main.activeSlot` is equally
 * unobserved). The old `seen(state, 'active')` gate therefore left
 * `activeReceiver: unknown` permanently on that class of radio, which cascaded
 * into `scope-adapter`'s spectrum authority (requires `activeReceiver.status
 * === 'known'`), band select / frequency entry (`SemanticRadioSurfaces`'s
 * `selectBand`/`enterFrequency`, both hard-gated on a known active receiver),
 * and the selected VFO tile's `.is-active` highlight — none of it ever worked
 * on a single-receiver radio.
 *
 * The fix is capability-aware, not state-aware: a capabilities payload that
 * DECLARES exactly one receiver (`receivers: 1`, MAIN-only topology) leaves
 * `active` no question to answer — MAIN is the only receiver that could ever
 * be active — so `activeReceiver` resolves to `{status: 'known', receiver:
 * 'MAIN'}` regardless of the `active` field's observedness or raw value. This
 * is the tautology MOR-988 §3.2 permits (there is no live alternative to
 * guess among), not the forbidden fabricated default.
 */
describe('single-receiver active-receiver resolves structurally, not from the active field (MOR-1421)', () => {
  const SINGLE_RX_CAPS = caps({
    vfoScheme: 'ab', receivers: 1, capabilities: SINGLE, vfoReadback: 'selected_unselected',
  });

  /** The live-stand shape: `active` and `main.activeSlot` never observed;
   *  `main.freqHz`/`main.mode`/`main.filter` fresh — exactly the IC-7300 probe. */
  function liveStandState(): ServerState {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, FieldStatus>;
    delete fieldStatus.active;
    delete fieldStatus['main.activeSlot'];
    return { ...base, fieldStatus } as ServerState;
  }

  it('resolves activeReceiver to MAIN though `active` was never observed', () => {
    const view = model(liveStandState(), SINGLE_RX_CAPS);
    expect(view.activeReceiver).toEqual({ status: 'known', receiver: 'MAIN' });
    expect(view.disabledReasons).not.toContainEqual({
      field: 'activeReceiver', code: 'field-not-observed',
    });
  });

  it('marks the selected VFO tile isActive — the tile the operator sees highlighted', () => {
    const view = model(liveStandState(), SINGLE_RX_CAPS);
    const selected = view.vfos.find(
      (vfo) => vfo.slot.kind === 'relative' && vfo.slot.role === 'selected',
    );
    expect(selected).toBeDefined();
    expect(selected!.isActive).toBe(true);
    expect(selected!.isActiveSlot).toBe(true);
  });

  // MUTATION KILLED: reading `state.active` for the single-receiver branch
  // instead of ignoring it — the whole point is that the CAPABILITIES answer
  // the question, not a raw field this class of radio never confirms.
  it('is a tautology, not a fabrication — holds regardless of the raw active VALUE', () => {
    const view = model(
      { ...liveStandState(), active: 'SUB' as unknown as ServerState['active'] },
      SINGLE_RX_CAPS,
    );
    expect(view.activeReceiver).toEqual({ status: 'known', receiver: 'MAIN' });
  });

  // Dual-RX guard (byte-identical to pre-MOR-1421 behaviour): the tautology
  // is single-receiver-only — a radio whose capabilities declare a second
  // receiver still needs a genuinely OBSERVED `active` reading.
  it('leaves a dual-receiver radio unaffected — activeReceiver stays unknown when active is unobserved', () => {
    const base = observedState();
    const fieldStatus = { ...base.fieldStatus } as Record<string, FieldStatus>;
    delete fieldStatus.active;
    const view = model({ ...base, fieldStatus } as ServerState, TOPOLOGY_CAPS['2/main_sub']);
    expect(view.activeReceiver).toEqual({ status: 'unknown' });
    expect(view.vfos.some((v) => v.isActive)).toBe(false);
    expect(view.disabledReasons).toContainEqual({
      field: 'activeReceiver', code: 'field-not-observed',
    });
  });
});

describe('TX identity and permit fail closed', () => {
  it('marks exactly the VFO a known target names', () => {
    const view = model(observedState(), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.vfos.filter((v) => v.isTxTarget).map((v) => v.label)).toEqual(['MAIN A']);
    expect(view.txPermit).toEqual({ status: 'allowed', band: '20m' });
  });

  // MUTATION KILLED: keeping a target whose slot contradicts the scheme (a
  // slot-less target under `main_sub`) instead of collapsing it to unknown.
  it('collapses a target that contradicts the capability scheme', () => {
    const view = model(observedState({
      txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14250000 },
    }), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.txTarget).toEqual({ status: 'unknown', reason: 'contradiction' });
    expect(view.txPermit.status).toBe('unknown');
  });

  it('denies an out-of-band target rather than leaving the permit open', () => {
    const view = model(observedState({
      txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 1000 },
    }), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.txPermit).toEqual({ status: 'denied', reason: 'outside-configured-ranges' });
    expect(view.disabledReasons).toContainEqual({ field: 'txPermit', code: 'out-of-band' });
  });

  it('reports unconfigured TX ranges as unknown, never as allowed', () => {
    const view = model(observedState(), caps({ txBands: null }));
    expect(view.txPermit).toEqual({ status: 'unknown', reason: 'ranges-unconfigured' });
  });
});

describe('scope availability separates structural from operational', () => {
  it('holds the hardware scope structurally present but not operational without controls', () => {
    const view = model(observedState({ scopeControls: undefined }), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.scope.hardwareScope).toEqual({ structural: true, operational: false });
    expect(view.disabledReasons).toContainEqual({
      field: 'scope.hardwareScope', code: 'field-not-observed',
    });
  });

  it('reports an absent audio FFT as capability-unavailable', () => {
    const view = model(observedState(), caps({ audioFftAvailable: false }));
    expect(view.scope.audioFftScope).toEqual({ structural: false, operational: false });
    expect(view.disabledReasons).toContainEqual({
      field: 'scope.audioFftScope', code: 'capability-unavailable',
    });
  });
});

// MOR-1256: `operationalReceivers` (presentation-capabilities.ts) had zero
// consumers — a `dual-rx-unavailable` radio (structurally dual, no `dual_rx`
// tag) kept SUB fully enabled. `vfos` correctly stays derived from
// `structuralReceivers` (MOR-977: the strip must still be PRESENT); the gap
// was that nothing fed `operationalReceivers` anywhere, so no CONSUMER ever
// disabled it. This closes the gap the same way `scope.hardwareScope` /
// `scope.audioFftScope` already report degraded-but-structural facts: one
// `disabledReasons` entry per structurally-present, operationally-absent
// receiver, read back by `dual-receiver-strips.ts`'s `isOperationalStrip`.
describe('operational receiver availability separates structural from operational (MOR-1256)', () => {
  const dualRxUnavailableCaps = caps({ capabilities: SINGLE });

  // MUTATION KILLED: dropping the `topology.operationalReceivers` loop
  // entirely — SUB stays structurally present (correct) but nothing ever
  // marks it disabled, reproducing the exact bug this ticket exists to fix.
  it('marks the structurally-present, operationally-unavailable SUB receiver disabled', () => {
    const view = model(observedState(), dualRxUnavailableCaps);
    expect(view.topologyId).toBe('2/main_sub');
    expect(view.vfos.some((v) => v.receiver === 'SUB')).toBe(true);
    expect(view.disabledReasons).toContainEqual({
      field: 'receiver.SUB', code: 'capability-unavailable',
    });
  });

  // MUTATION KILLED: pushing the reason for every structural receiver
  // instead of only the ones missing from `operationalReceivers` — MAIN
  // would falsely disable too.
  it('never marks MAIN unavailable — only the receiver that failed the capability check', () => {
    const view = model(observedState(), dualRxUnavailableCaps);
    expect(view.disabledReasons.some((r) => r.field === 'receiver.MAIN')).toBe(false);
  });

  it('emits no receiver disabledReason when the radio is fully dual-capable', () => {
    const view = model(observedState(), TOPOLOGY_CAPS['2/main_sub']);
    expect(view.disabledReasons.some((r) => r.field.startsWith('receiver.'))).toBe(false);
  });
});

describe('the emitted model carries only contract data', () => {
  it('survives a JSON round-trip unchanged — no functions, classes or live objects', () => {
    for (const id of Object.keys(TOPOLOGY_CAPS)) {
      const view = model(observedState(), TOPOLOGY_CAPS[id]);
      expect(JSON.parse(JSON.stringify(view))).toEqual(view);
    }
  });

  // MUTATION KILLED: passing the capability object (or a component/module
  // path) through onto the view model so a surface can "just read caps" —
  // exactly the manufacturer/runtime leak the contract exists to prevent.
  it('leaks no capability object and no module path', () => {
    const view = model(observedState(), TOPOLOGY_CAPS['2/main_sub']);
    const strings: string[] = [];
    const walk = (value: unknown, path: string): void => {
      expect(typeof value).not.toBe('function');
      if (typeof value === 'string') strings.push(value);
      if (value && typeof value === 'object') {
        for (const [key, child] of Object.entries(value)) walk(child, `${path}.${key}`);
      }
    };
    walk(view, '$');
    for (const value of strings) {
      // `topologyId` is legitimately `<count>/<scheme>`, so bare '/' is not the
      // tell — module-ish segments and file extensions are.
      expect(value).not.toMatch(
        /\.svelte|\.ts$|\$lib|node_modules|(^|\/)(src|lib|skins|semantic|components-v2)(\/|$)/,
      );
    }
    expect(Object.keys(view)).not.toContain('capabilities');
    // The validator rejects extra keys, so this is belt-and-braces on shape.
    expect(Object.keys(view).sort()).toEqual([
      'activeReceiver', 'disabledReasons', 'dualWatch', 'radioWideIndicators',
      'receiverIndicators', 'scope', 'scopeControls', 'split',
      'topologyId', 'txPermit', 'txTarget', 'vfoScheme', 'vfos',
    ]);
  });
});


describe('RF gain additive display observation', () => {
  const displayCaps = { ...INDICATOR_CAPS, stateContractVersion: 1, providerGeneration: 1 };
  function displayState(stale = false) {
    const state = indicatorState({ stateContractVersion: 1, providerGeneration: 1 });
    state.fieldStatus!['main.rfGain'] = {
      ...fresh, lastObservedMonotonic: 310658.42975425,
      ...(stale ? { freshness: 'stale' as const, availability: 'stale' as const } : {}),
    };
    return state;
  }
  it.each([false, true])('preserves every strict model member for stale=%s', (stale) => {
    const view = toRadioViewModel(displayState(stale), displayCaps, RECEIVING)!;
    const strictJson = JSON.stringify(view, (key, value) => key === 'display' ? undefined : value);
    const digest = createHash('sha256').update(strictJson).digest('hex');
    expect(digest).toBe(stale ? 'b4b5cff2b85557e39baa48e4d73c756e12ff026488ffa05a1ef558ca0b3f0507' : '379a5f00e3bebae780e4215af4e014351df2a07fc067d412f97df6aeadca840f');
  });
  it.each([false, true])('projects explicit display without admitting stale RFgain, stale=%s', (stale) => {
    const view = model(displayState(stale), displayCaps, RECEIVING);
    expect(view.receiverIndicators![0].rfGain).toEqual({
      reading: stale ? { status: 'unknown' } : { status: 'known', value: 0 },
      availability: { structural: true, operational: !stale },
      display: { state: stale ? 'stale' : 'current', value: 0 },
    });
  });
  it('does not classify a fresh but nonoperational SUB as stale', () => {
    const state = displayState();
    state.dualWatch = false;
    state.fieldStatus!['sub.rfGain'] = { ...fresh, lastObservedMonotonic: 310658.42975425 };
    const view = model(state, { ...displayCaps, capabilities: displayCaps.capabilities.filter((cap) => cap !== 'dual_rx') }, RECEIVING);
    expect(view.receiverIndicators![1].rfGain.availability.operational).toBe(false);
    expect(view.receiverIndicators![1].rfGain.display).toEqual({ state: 'current', value: 0.75 });
  });
});
