/**
 * MOR-1070 — the browser fixture catalog.
 *
 * Every state/capability shape below is lifted from the fixtures the merged
 * component tests already use
 * (`src/skins/dual-receiver-cockpit/__tests__/DualReceiverCockpit.component.test.ts`),
 * so a browser capture and the jsdom behavior pins describe the same radio.
 * The extra entries (`connection-loss-*`, `caps-unloaded`, the four TX phases,
 * `zoneless-controls`) are the states the ticket's Evidence line names but the
 * component tests do not enumerate as separate fixtures.
 *
 * `expect` is the BEHAVIOR ASSERTION contract for the fixture — it runs in the
 * page before any screenshot is taken (MOR-1070 acceptance: "behavior
 * assertions pass before screenshot comparison"). It is intentionally written
 * as the DERIVED shape (strip/tile/select counts, zone order, operational
 * flags), never as a copy of the fixture input, so an adapter or wiring
 * regression breaks the assertion rather than silently re-baselining a picture.
 */
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { IDLE_TX, type ModGuardProps, type TxSnapshot } from './harness-state';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };

type FieldStatusMap = Record<string, unknown>;
const statuses = (paths: readonly string[], entry: unknown = fresh): FieldStatusMap =>
  Object.fromEntries(paths.map((p) => [p, entry]));

const RADIO_WIDE = ['active', 'split', 'dualWatch', 'txTarget'] as const;

/** 2/main_sub: MAIN and SUB each carry A/B slots (4 vfo tiles total). */
function mainSubState(active: 'MAIN' | 'SUB' = 'MAIN', entry: unknown = fresh): ServerState {
  const paths: string[] = [...RADIO_WIDE];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const slot = (hz: number) => ({ freqHz: hz, mode: 'USB', filterNum: 1 });
  const receiver = (hz: number) => ({ vfoA: slot(hz), vfoB: slot(hz + 30000), activeSlot: 'A' });
  return {
    active, split: true, dualWatch: true, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(21295000),
    fieldStatus: statuses(paths, entry),
  } as unknown as ServerState;
}

/** 2/main_sub with SUB never observed at all — the startup window. */
function mainSubSubUnobserved(): ServerState {
  const paths: string[] = [...RADIO_WIDE, 'main.activeSlot'];
  for (const v of ['vfoA', 'vfoB']) {
    paths.push(`main.${v}.freqHz`, `main.${v}.mode`, `main.${v}.filterNum`);
  }
  const base = mainSubState('MAIN') as unknown as Record<string, unknown>;
  const { sub: _absent, ...rest } = base;
  return { ...rest, fieldStatus: statuses(paths) } as unknown as ServerState;
}

/** 2/ab_shared: MAIN and SUB are each a single unslotted VFO (2 vfo tiles). */
function abSharedState(): ServerState {
  const paths = [...RADIO_WIDE, 'main.freqHz', 'main.mode', 'main.filter',
    'sub.freqHz', 'sub.mode', 'sub.filter'];
  const receiver = (hz: number) => ({ freqHz: hz, mode: 'CW', filter: 1 });
  return {
    active: 'SUB', split: false, dualWatch: true, ptt: false,
    txTarget: { status: 'known', receiver: 'SUB', slot: null, frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14250000),
    fieldStatus: statuses(paths),
  } as unknown as ServerState;
}

/** 1/single: ONE receiver, one unslotted VFO. */
function singleState(): ServerState {
  const paths = [...RADIO_WIDE, 'main.freqHz', 'main.mode', 'main.filter'];
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    main: { freqHz: 14195000, mode: 'USB', filter: 1 },
    fieldStatus: statuses(paths),
  } as unknown as ServerState;
}

/** 1/ab: ONE receiver carrying A/B slots. */
function abState(): ServerState {
  const paths = [...RADIO_WIDE, 'main.activeSlot',
    'main.vfoA.freqHz', 'main.vfoA.mode', 'main.vfoA.filterNum',
    'main.vfoB.freqHz', 'main.vfoB.mode', 'main.vfoB.filterNum'];
  return {
    active: 'MAIN', split: true, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14195000 },
    main: {
      vfoA: { freqHz: 14195000, mode: 'USB', filterNum: 1 },
      vfoB: { freqHz: 14225000, mode: 'USB', filterNum: 2 },
      activeSlot: 'A',
    },
    fieldStatus: statuses(paths),
  } as unknown as ServerState;
}

/**
 * MOR-1273 — raw meter samples overlaid on a fixture state so the browser can
 * see the semantic meters surface. FIXTURE CODE ONLY: no production module
 * changes, and no capability is added, so
 *
 *   - the cockpit gains no new focusable control (the meters surface is a
 *     readout — R9), and every existing behavior assertion, focus order and
 *     zone-less-control count is therefore untouched;
 *   - `compressorOn` is deliberately NOT set and `compressor` stays out of the
 *     capability list, so the MOR-1244 `txAux` group is still absent. That is
 *     the browser proof of the COMP gate: a fully-present compression METER
 *     with the compressor fact unavailable renders NO COMP tile.
 *
 * Fixtures WITHOUT this overlay carry no `meters` group at all and so render
 * no meters surface — the self-gating half, provable in the same capture run.
 */
const METER_PATHS = ['powerMeter', 'swrMeter', 'alcMeter', 'compMeter',
  'vdMeter', 'idMeter', 'main.sMeter', 'sub.sMeter'];

function withMeters(state: ServerState): ServerState {
  const s = state as unknown as Record<string, unknown>;
  const rx = (v: unknown, sMeter: number) =>
    (v === undefined ? v : { ...(v as Record<string, unknown>), sMeter });
  return {
    ...s,
    powerMeter: 120, swrMeter: 30, alcMeter: 40, compMeter: 20, vdMeter: 200, idMeter: 90,
    main: rx(s.main, -12), sub: rx(s.sub, -30),
    fieldStatus: { ...(s.fieldStatus as FieldStatusMap), ...statuses(METER_PATHS) },
  } as unknown as ServerState;
}

const baseCaps = (): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'dual_rx'], receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

const mainSubCaps = baseCaps;
const abSharedCaps = (): Capabilities =>
  ({ ...baseCaps(), vfoScheme: 'ab_shared' } as unknown as Capabilities);
const singleCaps = (): Capabilities => ({
  ...baseCaps(), receivers: 1, vfoScheme: 'single', capabilities: ['audio', 'tx'],
} as unknown as Capabilities);
const abCaps = (): Capabilities => ({
  ...baseCaps(), receivers: 1, vfoScheme: 'ab', capabilities: ['audio', 'tx'],
} as unknown as Capabilities);
/** The ticket's orthogonal condition: scope=false + audioFft=true. */
const audioOnlyScopeCaps = (): Capabilities =>
  ({ ...baseCaps(), audioFftAvailable: true } as unknown as Capabilities);
/** Structurally dual, no `dual_rx` tag → `dual-rx-unavailable` (MOR-1256). */
const dualRxUnavailableCaps = (): Capabilities =>
  ({ ...baseCaps(), capabilities: ['audio', 'tx'] } as unknown as Capabilities);

const tx = (over: Partial<TxSnapshot>): TxSnapshot => ({ ...IDLE_TX, ...over });

export interface Expectation {
  /** Rendered `data-zone-id` values, in DOM order. */
  zones: readonly string[];
  strips: number;
  /** Per-strip receiver ids, in DOM order. */
  stripReceivers: readonly string[];
  /** Per-strip `data-strip-operational`, in DOM order. */
  stripOperational: readonly boolean[];
  /** Per-strip `data-strip-active`, in DOM order. */
  stripActive: readonly boolean[];
  tiles: number;
  selectsEnabled: number;
  selectsDisabled: number;
  /** `[data-vfo-split]` / `[data-vfo-dual-watch]` disabled state. */
  radioWideSwitchesDisabled: boolean;
  /** `[data-testid="rx-tx-key"]` disabled state. */
  keyDisabled: boolean;
  rfLabel: string | null;
  sessionLabel: string | null;
  /** `[data-testid="tx-fault-reset"]` present. */
  faultResetPresent: boolean;
  modInputWarningPresent: boolean;
  /** Controls whose `closest('[data-zone-id]')` is null (acceptance gate (b)). */
  zonelessControls: number;
}

export interface Fixture {
  id: string;
  /** One line, for the manifest. */
  what: string;
  state: () => ServerState | null;
  caps: () => Capabilities | null;
  tx: TxSnapshot;
  modGuard?: ModGuardProps;
  expect: Expectation;
}

const DUAL_ZONES = ['primary-vfo', 'secondary-vfo', 'global', 'rx-tx'] as const;
const SINGLE_ZONES = ['primary-vfo', 'global', 'rx-tx'] as const;

/** Shared shape of every healthy `2/main_sub` fixture — only TX state varies. */
const mainSubExpect = (over: Partial<Expectation> = {}): Expectation => ({
  zones: DUAL_ZONES, strips: 2, stripReceivers: ['MAIN', 'SUB'],
  stripOperational: [true, true], stripActive: [true, false],
  tiles: 4, selectsEnabled: 3, selectsDisabled: 0,
  radioWideSwitchesDisabled: false, keyDisabled: false,
  rfLabel: 'RX', sessionLabel: 'ready',
  faultResetPresent: false, modInputWarningPresent: false, zonelessControls: 0,
  ...over,
});

export const FIXTURES: readonly Fixture[] = [
  {
    id: 'topology-1-single',
    what: '1/single — one receiver, one unslotted VFO; the cockpit degrades to one strip.',
    state: () => withMeters(singleState()), caps: singleCaps, tx: tx({}),
    expect: {
      zones: SINGLE_ZONES, strips: 1, stripReceivers: ['MAIN'],
      stripOperational: [true], stripActive: [true],
      tiles: 1, selectsEnabled: 0, selectsDisabled: 0,
      radioWideSwitchesDisabled: false, keyDisabled: false,
      rfLabel: 'RX', sessionLabel: 'ready',
      faultResetPresent: false, modInputWarningPresent: false, zonelessControls: 0,
    },
  },
  {
    id: 'topology-1-ab',
    what: '1/ab — one receiver carrying A/B slots; still one strip, no SUB anywhere.',
    state: abState, caps: abCaps, tx: tx({}),
    expect: {
      zones: SINGLE_ZONES, strips: 1, stripReceivers: ['MAIN'],
      stripOperational: [true], stripActive: [true],
      tiles: 2, selectsEnabled: 1, selectsDisabled: 0,
      radioWideSwitchesDisabled: false, keyDisabled: false,
      rfLabel: 'RX', sessionLabel: 'ready',
      faultResetPresent: false, modInputWarningPresent: false, zonelessControls: 0,
    },
  },
  {
    id: 'topology-2-ab-shared',
    what: '2/ab_shared — two receivers, one unslotted VFO each; SUB is the active receiver.',
    state: abSharedState, caps: abSharedCaps, tx: tx({}),
    expect: {
      zones: DUAL_ZONES, strips: 2, stripReceivers: ['MAIN', 'SUB'],
      stripOperational: [true, true], stripActive: [false, true],
      tiles: 2, selectsEnabled: 1, selectsDisabled: 0,
      radioWideSwitchesDisabled: false, keyDisabled: false,
      rfLabel: 'RX', sessionLabel: 'ready',
      faultResetPresent: false, modInputWarningPresent: false, zonelessControls: 0,
    },
  },
  {
    id: 'topology-2-main-sub',
    what: '2/main_sub — the reference dual state: 4 tiles across 2 strips, MAIN A active.',
    state: () => withMeters(mainSubState('MAIN')), caps: mainSubCaps, tx: tx({}),
    expect: mainSubExpect(),
  },
  {
    id: 'audio-only-scope',
    what: 'scope=false + audioFft=true on 2/main_sub — the cockpit must claim nothing either way.',
    state: () => mainSubState('MAIN'), caps: audioOnlyScopeCaps, tx: tx({}),
    expect: mainSubExpect(),
  },
  {
    id: 'sub-unobserved',
    what: 'startup window: SUB never observed — strip present, one explicit unknown slot, select disabled.',
    state: mainSubSubUnobserved, caps: mainSubCaps, tx: tx({}),
    expect: mainSubExpect({ tiles: 3, selectsEnabled: 1, selectsDisabled: 1 }),
  },
  {
    id: 'dual-rx-unavailable',
    what: 'structural dual, operationally degraded (MOR-1256) — SUB present, its selects really disabled.',
    state: () => mainSubState('MAIN'), caps: dualRxUnavailableCaps, tx: tx({}),
    expect: mainSubExpect({
      stripOperational: [true, false], selectsEnabled: 1, selectsDisabled: 2,
    }),
  },
  {
    id: 'tx-phase-rx',
    what: 'TX idle — RF receiving, session ready, key enabled, unkey ungated.',
    state: () => withMeters(mainSubState('MAIN')), caps: mainSubCaps, tx: tx({}),
    expect: mainSubExpect(),
  },
  {
    id: 'tx-phase-pending',
    what: 'TX keying in progress — RF uncertain, session pending, key blocked, unkey still live.',
    state: () => withMeters(mainSubState('MAIN')), caps: mainSubCaps,
    tx: tx({
      phase: 'key-confirm-pending', intent: 'latched', guard: { leaseId: 'L1' },
      radioTx: 'off', txRisk: 'uncertain', mayOwnKey: true,
    }),
    expect: mainSubExpect({ keyDisabled: true, rfLabel: 'TX?', sessionLabel: 'keying' }),
  },
  {
    id: 'tx-phase-tx',
    what: 'transmitting — RF TX, session key down, key blocked, unkey the only way out.',
    state: () => withMeters(mainSubState('MAIN')), caps: mainSubCaps,
    tx: tx({
      phase: 'active', intent: 'latched', guard: { leaseId: 'L1' },
      radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true,
    }),
    expect: mainSubExpect({ keyDisabled: true, rfLabel: 'TX', sessionLabel: 'key down' }),
  },
  {
    id: 'tx-phase-fault',
    what: 'TX fault — session fault, fault line shown, the App-owned fault reset affordance renders.',
    state: () => withMeters(mainSubState('MAIN')), caps: mainSubCaps,
    tx: tx({ phase: 'failed', radioTx: 'unknown', txRisk: 'uncertain', fault: 'audio-failed' }),
    expect: mainSubExpect({
      keyDisabled: true, rfLabel: 'TX?', sessionLabel: 'fault',
      faultResetPresent: true, zonelessControls: 0,
    }),
  },
  {
    id: 'connection-loss-stale',
    what: 'radio link lost, values retained but every field STALE — every fact degrades to unknown.',
    state: () => mainSubState('MAIN', stale), caps: mainSubCaps, tx: tx({}),
    expect: mainSubExpect({
      stripActive: [false, false], selectsEnabled: 4, selectsDisabled: 0,
      radioWideSwitchesDisabled: true, keyDisabled: true,
    }),
  },
  {
    id: 'connection-loss-state-null',
    what: 'reconnect window — capabilities known, no state payload at all; everything present and inert.',
    state: () => null, caps: mainSubCaps, tx: tx({}),
    expect: mainSubExpect({
      stripActive: [false, false], tiles: 2, selectsEnabled: 0, selectsDisabled: 2,
      radioWideSwitchesDisabled: true, keyDisabled: true,
    }),
  },
  {
    id: 'caps-unloaded',
    what: 'no capabilities yet — the shell renders its inert placeholders and claims nothing.',
    state: () => null, caps: () => null, tx: tx({}),
    expect: {
      zones: ['rx-tx'], strips: 0, stripReceivers: [], stripOperational: [], stripActive: [],
      tiles: 0, selectsEnabled: 0, selectsDisabled: 0,
      radioWideSwitchesDisabled: false, keyDisabled: false,
      rfLabel: null, sessionLabel: null,
      faultResetPresent: false, modInputWarningPresent: false, zonelessControls: 0,
    },
  },
  {
    id: 'zoneless-controls',
    what: 'acceptance gate (b): the three conditional controls that render OUTSIDE every declared zone.',
    state: () => mainSubState('MAIN'), caps: mainSubCaps,
    tx: tx({ phase: 'failed', radioTx: 'unknown', txRisk: 'uncertain', fault: 'audio-failed' }),
    modGuard: { visible: true, sourceLabel: 'MIC' },
    expect: mainSubExpect({
      keyDisabled: true, rfLabel: 'TX?', sessionLabel: 'fault',
      faultResetPresent: true, modInputWarningPresent: true, zonelessControls: 0,
    }),
  },
];

export const fixtureById = (id: string): Fixture | undefined =>
  FIXTURES.find((f) => f.id === id);
