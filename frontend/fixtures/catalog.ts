/**
 * MOR-1070 — the browser fixture catalog.
 *
 * Every state/capability shape below is lifted from the fixtures the merged
 * component tests already use
 * (`src/skins/dual-receiver-cockpit/__tests__/DualReceiverCockpit.component.test.ts`),
 * so a browser capture and the jsdom behavior pins describe the same radio.
 * The extra entries (`connection-loss-*`, `caps-unloaded`, the four TX phases,
 * `tx-adjacent-alerts`) are the states the ticket's Evidence line names but the
 * component tests do not enumerate as separate fixtures.
 *
 * `expect` is the BEHAVIOR ASSERTION contract for the fixture — it runs in the
 * page before any screenshot is taken (MOR-1070 acceptance: "behavior
 * assertions pass before screenshot comparison"). It is intentionally written
 * as the DERIVED shape (strip/tile/select counts, zone order, operational
 * flags), never as a copy of the fixture input, so an adapter or wiring
 * regression breaks the assertion rather than silently re-baselining a picture.
 */
import type { Capabilities, MeterCalPoint } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import {
  IDLE_TX, type AudioRuntimeState, type ModGuardProps, type TxSnapshot,
} from './harness-state';

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

/**
 * 2/ab_shared: MAIN and SUB are each a single unslotted VFO (2 vfo tiles).
 * `active` defaults to `'SUB'` (the ORIGINAL `topology-2-ab-shared` baseline,
 * unchanged) — MOR-1085's two new ab_shared states pass `'MAIN'` explicitly
 * so the degraded/unobserved receiver (SUB) is never simultaneously the
 * "active" one, matching `mainSubState`/`mainSubSubUnobserved`'s convention.
 */
function abSharedState(active: 'MAIN' | 'SUB' = 'SUB'): ServerState {
  const paths = [...RADIO_WIDE, 'main.freqHz', 'main.mode', 'main.filter',
    'sub.freqHz', 'sub.mode', 'sub.filter'];
  const receiver = (hz: number) => ({ freqHz: hz, mode: 'CW', filter: 1 });
  return {
    active, split: false, dualWatch: true, ptt: false,
    txTarget: { status: 'known', receiver: active, slot: null, frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14250000),
    fieldStatus: statuses(paths),
  } as unknown as ServerState;
}

/**
 * MOR-1085 — 2/ab_shared with SUB never observed: the `ab_shared` analogue of
 * `mainSubSubUnobserved` below (the "startup window" / selection-fallback
 * state), applied to the OTHER dual topology so the same startup gap is
 * proven across both, not just `main_sub`.
 */
function abSharedSubUnobserved(): ServerState {
  const paths = [...RADIO_WIDE, 'main.freqHz', 'main.mode', 'main.filter'];
  const base = abSharedState('MAIN') as unknown as Record<string, unknown>;
  const { sub: _absent, ...rest } = base;
  return { ...rest, fieldStatus: statuses(paths) } as unknown as ServerState;
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
 * MOR-1085 — 1/ab with slot B never observed: the per-SLOT analogue of
 * `mainSubSubUnobserved`'s per-RECEIVER startup window, exercising selection
 * fallback on a topology that has no second receiver to degrade at all —
 * `dual-rx-unavailable`'s capability axis is structurally inapplicable to
 * `1/ab` (there is no `dual_rx` concept for one receiver), so this is the
 * meaningful "unobserved slot" state that topology actually admits.
 *
 * Deletes the `vfoB` key outright — VERIFIED against two candidate shapes
 * (MOR-1281 discipline), not assumed:
 *
 *   - keeping `vfoB` and narrowing only `fieldStatus` renders BYTE-IDENTICAL
 *     tile/select shape to the healthy `1/ab` baseline. The adapter's slot
 *     collapse (`radio-view-model-adapter.ts`'s `vfos` builder) branches on
 *     RAW STATE PRESENCE — `slots.every((id) => rx?.[SLOT_KEY[id]] != null)`
 *     — not on `fieldStatus`/observedness, so a slot whose key is merely
 *     unobserved-but-present still renders `kind: 'slotted'` and stays fully
 *     selectable. That shape does not exercise "selection fallback" at all.
 *   - deleting the key makes `every(...)` false for the WHOLE receiver,
 *     collapsing BOTH slots into ONE `kind: 'unknown'` position (comment at
 *     that call site: "A slotted scheme whose slot view was never observed:
 *     ONE position of unknown slot identity"). With only one vfo in the
 *     entire topology, `selectionPoolSize` is 1, so `isSelectable` (which
 *     requires `hasVfoPair`) is false — the position renders as a
 *     `data-vfo-label` span, not a button, at ALL. Verified actual: 1 tile,
 *     0 selects (neither enabled nor disabled — there is no select control
 *     to gate, the structurally-absent half of the MOR-1256 two-level
 *     doctrine, not the present-and-disabled half).
 *
 * The second shape is the real "selection fallback" this fixture is for.
 */
function abStateSlotBUnobserved(): ServerState {
  const paths = [...RADIO_WIDE, 'main.activeSlot',
    'main.vfoA.freqHz', 'main.vfoA.mode', 'main.vfoA.filterNum'];
  const base = abState() as unknown as Record<string, unknown>;
  const mainBase = base.main as Record<string, unknown>;
  const { vfoB: _absentSlot, ...restMain } = mainBase;
  return { ...base, main: restMain, fieldStatus: statuses(paths) } as unknown as ServerState;
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

/**
 * MOR-1351: hardened to a REAL radio's shape — IC-7610 (`rigs/ic7610.toml`),
 * the only dual-receiver profile in the tree and already this fixture's
 * implied topology (`receivers: 2, vfoScheme: 'main_sub'`) — modes/filters/
 * tags verbatim from that profile.
 *
 * MOR-1355 (N1 follow-up landed): the five `deriveTxAux` evidence tags
 * (`tuner`/`vox`/`compressor`/`monitor`/`drive_gain`) are now INCLUDED — the
 * reason they were withheld ("this harness supplies no resolved
 * `SurfacePlan`, so every fixture would render the group ZONE-LESS") no
 * longer holds universally: `fixtures/main.ts` can now supply a real
 * `resolveSurfacePlan(dualReceiverCockpitLayout, …)` plan through Svelte
 * context, exactly the `DualReceiverCockpit.component.test.ts` recipe. It
 * still holds PER FIXTURE — only `topology-2-main-sub--planned` below opts
 * in (`planned: true`); every other fixture stays plan-less on purpose (the
 * ticket's instruction: the existing zone-less topologies still represent
 * production's pre-navigation reference path). Measured consequence: with
 * caps evidence for all five tags and no fixture state observing any of
 * them, `TxAuxSurface` renders exactly 13 controls, all present-and-disabled
 * (4 toggles + TUNE + 8 level inputs — see `TX_AUX_TOGGLES`/`TX_AUX_LEVELS`
 * in `TxAuxSurface.svelte`) — inside `tx-aux` for the one planned fixture,
 * outside every declared zone (`NO-ZONE`) everywhere else. Every affected
 * `zonelessControls` pin below carries this count with its own rationale
 * rather than a blanket default, per the MOR-1355 build report.
 *
 * `scope` is `true` with a `'scope'` tag (matching the cockpit vitest
 * fixture): `presentation-capabilities.ts`'s `agreed()` requires the boolean
 * and the tag to agree, so leaving the tag off while flipping the boolean
 * would itself raise `scope-capability-contradiction`. The MOR-1085
 * `audio-only-scope` contrast fixture below now states its own `scope: false`
 * condition explicitly instead of inheriting it (see `audioOnlyScopeCaps`).
 * This flips `topology-2-main-sub`'s `expect.scopeFacts.hardwareScope` from
 * `structural: false` to `structural: true` — `operational` stays `false`
 * because no fixture state carries `scopeControls`.
 */
/**
 * MOR-1451 — the visual harness's `model: 'fixture'` capabilities never
 * declared an `s_meter` calibration table, so every S-meter surface in
 * every baseline rendered through the frontend's since-removed hardcoded
 * IC-7610-shaped fallback curve — a real radio state the visual gate never
 * actually covered (no capture had ever exercised the calibrated path).
 * Declaring a table here is required, not incidental: with no table, the
 * fixture is honestly UNCALIBRATED (MOR-1451's whole point), and every
 * S-meter readout renders the plain raw number + "uncalibrated" instead of
 * an S-unit — which is what a real un-calibrated radio (ic705/ic9700/tx500/
 * x6100/x6200) would show, not what the reference layouts intend to depict.
 * Anchors are the IC-7300's documented CI-V S-meter scale (0=S0, 120=S9,
 * 241=S9+60 — see `rigs/ic7300.toml`'s citation), chosen as the
 * representative calibrated curve since it is freshly verified against the
 * official Icom CI-V Reference Guide.
 */
const S_METER_CAL: MeterCalPoint[] = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 120, actual: 0, label: 'S9' },
  { raw: 241, actual: 60, label: 'S9+60' },
];

const baseCaps = (): Capabilities => ({
  model: 'fixture', scope: true, audio: true, tx: true,
  meterCalibrations: { s_meter: S_METER_CAL },
  capabilities: [
    'audio', 'tx', 'dual_rx', 'tuner', 'dual_watch', 'lan_dual_rx_audio_routing',
    'af_level', 'rf_gain', 'squelch', 'attenuator', 'preamp', 'digisel', 'ip_plus',
    'antenna', 'rx_antenna', 'nb', 'nr', 'notch', 'apf', 'twin_peak', 'pbt',
    'filter_width', 'filter_shape', 'split', 'ssb_tx_bw', 'cw', 'break_in', 'rit', 'xit',
    'meters', 'data_mode', 'mod_input_routing', 'agc', 'power_control', 'dial_lock',
    'scan', 'bsr', 'main_sub_tracking', 'tuning_step', 'band_edge', 'xfc', 'system_settings',
    'scope', 'vox', 'compressor', 'monitor', 'drive_gain',
  ],
  receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [{ start: 1800000, end: 54000000, label: 'HF+6m', bands: [
    { name: '20m', start: 14000000, end: 14350000, default: 14195000 },
    { name: '40m', start: 7000000, end: 7300000, default: 7100000 },
  ] }],
  modes: ['USB', 'LSB', 'CW', 'CW-R', 'AM', 'FM', 'RTTY', 'RTTY-R', 'PSK', 'PSK-R'],
  filters: ['FIL1', 'FIL2', 'FIL3'],
  antennas: 2,
  attValues: [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45],
  preValues: [0, 1, 2],
  agcModes: [1, 2, 3], agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

const mainSubCaps = baseCaps;
const abSharedCaps = (): Capabilities =>
  ({ ...baseCaps(), vfoScheme: 'ab_shared' } as unknown as Capabilities);
const singleCaps = (): Capabilities => ({
  ...baseCaps(), receivers: 1, vfoScheme: 'single', scope: false, capabilities: ['audio', 'tx'],
} as unknown as Capabilities);
const abCaps = (): Capabilities => ({
  ...baseCaps(), receivers: 1, vfoScheme: 'ab', scope: false, capabilities: ['audio', 'tx'],
} as unknown as Capabilities);
/** The ticket's orthogonal condition: scope=false + audioFft=true. */
const audioOnlyScopeCaps = (): Capabilities => ({
  ...baseCaps(), scope: false,
  capabilities: baseCaps().capabilities.filter((t) => t !== 'scope'),
  audioFftAvailable: true,
} as unknown as Capabilities);
/** Structurally dual, no `dual_rx` tag → `dual-rx-unavailable` (MOR-1256). */
const dualRxUnavailableCaps = (): Capabilities =>
  ({ ...baseCaps(), scope: false, capabilities: ['audio', 'tx'] } as unknown as Capabilities);
/**
 * MOR-1085 — the `ab_shared` analogue of `dualRxUnavailableCaps`: structurally
 * dual (`vfoScheme: 'ab_shared'`, `receivers: 2`), no `dual_rx` tag, so the
 * SAME MOR-1256 two-level gate applies on the other dual topology.
 */
const abSharedDualRxUnavailableCaps = (): Capabilities =>
  ({ ...abSharedCaps(), scope: false, capabilities: ['audio', 'tx'] } as unknown as Capabilities);

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
  /**
   * MOR-1085. `true` (default) for the dual-receiver-cockpit composition,
   * which binds every declared zone to a `data-zone-id` element. `false` for
   * the reference/single composition, which MOR-1069 deliberately leaves
   * zone-less ("a zone element exists only where an arrangement must place
   * it, and the single composition places nothing" —
   * `presentation/layouts/desktop-declarations.ts`). Every zone-shaped check
   * in `assertions.ts` — the cockpit's inert scope/controls placeholders,
   * the "every control lives in a zone" acceptance gate, the containment
   * half of the radio-wide-row check — is cockpit-specific by construction
   * and reads this flag rather than assuming one composition's shape.
   */
  zonedComposition?: boolean;
  /**
   * MOR-1085 checklist item 5. Whether `[data-testid="rx-audio-surface"]`
   * (the only currently-wired "audio path" UI, MOR-1279) is present.
   * Structural: renders in the reference/single composition whenever a view
   * model exists, never in the dual-receiver-cockpit composition. Optional
   * so existing fixtures are unaffected; set where the contrast matters.
   */
  rxAudioSurfacePresent?: boolean;
  /** Browser-audio facts rendered by `RxAudioSurface` for an axis fixture. */
  rxAudio?: {
    monitorMode: 'local' | 'live' | 'mute';
    volume: number | null;
    connectionAudio: boolean;
    linkPresent: boolean;
  };
  /**
   * MOR-1085 checklist item 5. The `view.scope.{hardwareScope,audioFftScope}`
   * facts (MOR-1298/1299 vocabulary), checked directly against the real
   * adapter's output — no semantic surface consumes them yet on either
   * layout (see the MOR-1085 report), so this is the only way to pin the
   * "hardware-scope affordances absent, audioFft honestly reported" half of
   * the audio-only-scope condition today. `null` means "no view model"
   * (caps not loaded). Optional and set only where this contrast is the
   * point of the fixture.
   */
  scopeFacts?: {
    hardwareScope: { structural: boolean; operational: boolean };
    audioFftScope: { structural: boolean; operational: boolean };
  } | null;
}

export interface Fixture {
  id: string;
  /** One line, for the manifest. */
  what: string;
  state: () => ServerState | null;
  caps: () => Capabilities | null;
  tx: TxSnapshot;
  modGuard?: ModGuardProps;
  /** MOR-1392. Independent overrides of the fixture runtime's audio facts. */
  audioRuntime?: Partial<AudioRuntimeState>;
  /**
   * MOR-1085. Which real component this fixture mounts: the
   * dual-receiver-cockpit shell (default, unchanged from MOR-1070) or
   * `ReferenceLayout.svelte` (`SemanticRadioSurfaces strips="single"` — the
   * same wiring `desktop-v2`/`sdr-test` compose today). One fixture id is
   * one grid cell; `toReferenceFixture()` below derives every `--reference`
   * id from its cockpit sibling.
   */
  layout?: 'cockpit' | 'reference';
  /**
   * MOR-1355. `true` ⇒ `fixtures/main.ts` mounts this fixture with a REAL
   * resolved `SurfacePlan` (`resolveSurfacePlan(dualReceiverCockpitLayout,
   * …)`) supplied through Svelte context — the same recipe
   * `DualReceiverCockpit.component.test.ts`'s `render(defaultPlan())` uses.
   * Default `false`/absent: the pre-MOR-1355 plan-less mount, unchanged.
   */
  planned?: boolean;
  expect: Expectation;
}

const DUAL_ZONES = ['primary-vfo', 'secondary-vfo', 'global', 'rx-tx'] as const;
const SINGLE_ZONES = ['primary-vfo', 'global', 'rx-tx'] as const;
/**
 * MOR-1355. `dualReceiverCockpitLayout` declares a `tx-aux` zone (MOR-1336/
 * S4); a `planned` fixture resolves a real plan against it, so `tx-aux`
 * genuinely comes last in DOM order, after `rx-tx`.
 */
const DUAL_ZONES_PLANNED = [...DUAL_ZONES, 'tx-aux'] as const;
/**
 * MOR-1355. The exact `TxAuxSurface` control count once `baseCaps` carries
 * all five `deriveTxAux` evidence tags and no fixture state observes any of
 * them: 4 toggles (ATU/VOX/COMP/MON) + 1 TUNE button + 8 level inputs
 * (rfPower/micGain/driveGain/voxGain/antiVoxGain/voxDelay/compressorLevel/
 * monitorLevel) = 13, every one present-and-disabled
 * (`TxAuxSurface.svelte`'s `TX_AUX_TOGGLES`/`TX_AUX_LEVELS`). Measured via
 * `fixtures/capture.mjs`, not assumed — see the MOR-1355 build report.
 */
const TX_AUX_ZONELESS_CONTROLS = 13;

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

/**
 * MOR-1085 — every dual-receiver-cockpit fixture. `FIXTURES` below adds the
 * reference-layout twin of each (except `tx-adjacent-alerts`, a
 * cockpit-zone-specific acceptance gate — see `toReferenceFixture`).
 */
const CORE_FIXTURES: readonly Fixture[] = [
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
    // MOR-1085 — "selection fallback" on `1/ab`: `dual-rx-unavailable`'s
    // capability axis does not exist for a single receiver, but a slot CAN
    // still be unobserved, which is the state this topology actually admits
    // (see `abStateSlotBUnobserved` above).
    // `tiles: 1, selectsEnabled: 0, selectsDisabled: 0` — VERIFIED, not
    // assumed by analogy with `sub-unobserved`'s 3-tiles/1-disabled shape:
    // losing one of two DECLARED slots collapses the receiver to ONE
    // `kind: 'unknown'` position (see `abStateSlotBUnobserved` above), and
    // with only that one vfo in the whole topology it is not selectable at
    // all — no select button exists to be enabled OR disabled. This is the
    // "structurally absent" half of the MOR-1085 two-level doctrine
    // (checklist item 4), the OPPOSITE half from `sub-unobserved`'s
    // "present and really disabled".
    id: 'topology-1-ab-selection-fallback',
    what: '1/ab, slot B never observed — the receiver collapses to ONE unknown-slot tile with no '
      + 'select control at all (structurally absent, not present-and-disabled).',
    state: abStateSlotBUnobserved, caps: abCaps, tx: tx({}),
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
    id: 'topology-2-ab-shared',
    what: '2/ab_shared — two receivers, one unslotted VFO each; SUB is the active receiver.',
    state: () => abSharedState(), caps: abSharedCaps, tx: tx({}),
    expect: {
      zones: DUAL_ZONES, strips: 2, stripReceivers: ['MAIN', 'SUB'],
      stripOperational: [true, true], stripActive: [false, true],
      tiles: 2, selectsEnabled: 1, selectsDisabled: 0,
      radioWideSwitchesDisabled: false, keyDisabled: false,
      rfLabel: 'RX', sessionLabel: 'ready',
      faultResetPresent: false, modInputWarningPresent: false,
      // MOR-1355: `abSharedCaps` inherits `baseCaps`'s txAux evidence and this
      // fixture supplies no plan (`planned` unset), so TxAuxSurface's 13
      // controls land NO-ZONE.
      zonelessControls: TX_AUX_ZONELESS_CONTROLS,
    },
  },
  {
    // MOR-1085 — "unsupported controls" state on the SECOND dual topology
    // (`dual-rx-unavailable` above already covers `main_sub`; MOR-1256's
    // two-level gate is a capability-derivation property, not a
    // `main_sub`-specific one, so it applies here unchanged). `active:
    // 'MAIN'` so the receiver capabilities degrade (SUB) is not also the
    // one `state.active` claims — same separation `dual-rx-unavailable`
    // keeps for `main_sub`.
    id: 'topology-2-ab-shared-unsupported-controls',
    what: '2/ab_shared, structurally dual but no `dual_rx` tag (MOR-1256) — SUB present, its select '
      + 'really disabled, same two-level gate as `dual-rx-unavailable` on the other dual topology.',
    state: () => abSharedState('MAIN'), caps: abSharedDualRxUnavailableCaps, tx: tx({}),
    expect: {
      zones: DUAL_ZONES, strips: 2, stripReceivers: ['MAIN', 'SUB'],
      stripOperational: [true, false], stripActive: [true, false],
      tiles: 2, selectsEnabled: 0, selectsDisabled: 1,
      radioWideSwitchesDisabled: false, keyDisabled: false,
      rfLabel: 'RX', sessionLabel: 'ready',
      faultResetPresent: false, modInputWarningPresent: false, zonelessControls: 0,
    },
  },
  {
    // MOR-1085 — "selection fallback" / startup-window state on `ab_shared`
    // (`sub-unobserved` above already covers `main_sub`).
    //
    // `selectsEnabled: 1, selectsDisabled: 0` — VERIFIED against the actual
    // capture, not assumed by analogy with `sub-unobserved`: an unslotted
    // `ab_shared` vfo's `slot.kind` is never `'unknown'`
    // (`VfoSurface.svelte`'s `selectDisabled = selectable && (vfo.slot.kind
    // === 'unknown' || disabled)` only has a `'unknown'` kind for A/B SLOTS),
    // and this state does not touch the `disabled` prop path either (that is
    // driven by `isOperationalStrip`, i.e. the CAPABILITY gate, not by
    // per-field observedness) — so an unobserved `ab_shared` receiver's
    // select stays exactly as enabled as the healthy baseline. This is the
    // real, honest answer for this topology, not a copy of `sub-unobserved`'s
    // numbers.
    id: 'topology-2-ab-shared-selection-fallback',
    what: '2/ab_shared startup window: SUB never observed — strip present, select UNAFFECTED (see '
      + 'comment: ab_shared select-gating is capability-driven, not observedness-driven).',
    state: abSharedSubUnobserved, caps: abSharedCaps, tx: tx({}),
    expect: {
      zones: DUAL_ZONES, strips: 2, stripReceivers: ['MAIN', 'SUB'],
      stripOperational: [true, true], stripActive: [true, false],
      tiles: 2, selectsEnabled: 1, selectsDisabled: 0,
      radioWideSwitchesDisabled: false, keyDisabled: false,
      rfLabel: 'RX', sessionLabel: 'ready',
      faultResetPresent: false, modInputWarningPresent: false,
      // MOR-1355: same reasoning as `topology-2-ab-shared` above.
      zonelessControls: TX_AUX_ZONELESS_CONTROLS,
    },
  },
  {
    id: 'topology-2-main-sub',
    what: '2/main_sub — the reference dual state: 4 tiles across 2 strips, MAIN A active.',
    state: () => withMeters(mainSubState('MAIN')), caps: mainSubCaps, tx: tx({}),
    // MOR-1085 checklist item 5 contrast pair (with `audio-only-scope` below):
    // MOR-1351 gave baseCaps a `scope` tag AND `scope: true` (they must
    // agree, `presentation-capabilities.ts`'s `agreed()`), so hardwareScope
    // is now structurally available; `audioFftAvailable` stays `false`, so
    // audioFftScope stays fully unavailable — computed straight from the
    // real adapter, not asserted by fiat (see `scope-facts-honest` in
    // assertions.ts). No fixture state carries `scopeControls`, so
    // hardwareScope stays operationally unavailable even though it is now
    // structurally available. `rxAudioSurfacePresent: false` pins the
    // cockpit's OWN half of the contrast: the dual composition never mounts
    // `RxAudioSurface` regardless of any audio capability (structural, not
    // capability-gated) — see the `--reference` twin below for the layout
    // that DOES mount it.
    expect: mainSubExpect({
      scopeFacts: {
        hardwareScope: { structural: true, operational: false },
        audioFftScope: { structural: false, operational: false },
      },
      rxAudioSurfacePresent: false,
      // MOR-1355: `mainSubCaps` (= `baseCaps`) now carries txAux evidence and
      // this fixture supplies no plan — see `topology-2-main-sub--planned`
      // below for the plan-ful twin where this is honestly 0.
      zonelessControls: TX_AUX_ZONELESS_CONTROLS,
    }),
  },
  {
    id: 'audio-only-scope',
    what: 'scope=false + audioFft=true on 2/main_sub — the cockpit must claim nothing either way.',
    state: () => mainSubState('MAIN'), caps: audioOnlyScopeCaps, tx: tx({}),
    // `audioOnlyScopeCaps` explicitly restates `scope: false` (and drops the
    // `scope` tag) on top of baseCaps, then sets `audioFftAvailable: true` —
    // `hardwareScope` stays fully unavailable (structural comes from the
    // explicit override, not an inherited default) and `audioFftScope.
    // operational` follows `state !== null`, true here.
    expect: mainSubExpect({
      scopeFacts: {
        hardwareScope: { structural: false, operational: false },
        audioFftScope: { structural: true, operational: true },
      },
      rxAudioSurfacePresent: false,
      // MOR-1355: `audioOnlyScopeCaps` inherits `baseCaps`'s txAux evidence
      // and this fixture supplies no plan.
      zonelessControls: TX_AUX_ZONELESS_CONTROLS,
    }),
  },
  {
    id: 'sub-unobserved',
    what: 'startup window: SUB never observed — strip present, one explicit unknown slot, select disabled.',
    state: mainSubSubUnobserved, caps: mainSubCaps, tx: tx({}),
    // MOR-1355: `mainSubCaps` carries txAux evidence, no plan supplied.
    expect: mainSubExpect({
      tiles: 3, selectsEnabled: 1, selectsDisabled: 1, zonelessControls: TX_AUX_ZONELESS_CONTROLS,
    }),
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
    // MOR-1355: `mainSubCaps` carries txAux evidence, no plan supplied.
    expect: mainSubExpect({ zonelessControls: TX_AUX_ZONELESS_CONTROLS }),
  },
  {
    id: 'tx-phase-pending',
    what: 'TX keying in progress — RF uncertain, session pending, key blocked, unkey still live.',
    state: () => withMeters(mainSubState('MAIN')), caps: mainSubCaps,
    tx: tx({
      phase: 'key-confirm-pending', intent: 'latched', guard: { leaseId: 'L1' },
      radioTx: 'off', txRisk: 'uncertain', mayOwnKey: true,
    }),
    // MOR-1355: `mainSubCaps` carries txAux evidence, no plan supplied.
    expect: mainSubExpect({
      keyDisabled: true, rfLabel: 'TX?', sessionLabel: 'keying',
      zonelessControls: TX_AUX_ZONELESS_CONTROLS,
    }),
  },
  {
    id: 'tx-phase-tx',
    what: 'transmitting — RF TX, session key down, key blocked, unkey the only way out.',
    state: () => withMeters(mainSubState('MAIN')), caps: mainSubCaps,
    tx: tx({
      phase: 'active', intent: 'latched', guard: { leaseId: 'L1' },
      radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true,
    }),
    // MOR-1355: `mainSubCaps` carries txAux evidence, no plan supplied.
    expect: mainSubExpect({
      keyDisabled: true, rfLabel: 'TX', sessionLabel: 'key down',
      zonelessControls: TX_AUX_ZONELESS_CONTROLS,
    }),
  },
  {
    id: 'tx-phase-fault',
    what: 'TX fault — session fault, fault line shown, the App-owned fault reset affordance renders.',
    state: () => withMeters(mainSubState('MAIN')), caps: mainSubCaps,
    tx: tx({ phase: 'failed', radioTx: 'unknown', txRisk: 'uncertain', fault: 'audio-failed' }),
    // MOR-1355: `mainSubCaps` carries txAux evidence, no plan supplied.
    expect: mainSubExpect({
      keyDisabled: true, rfLabel: 'TX?', sessionLabel: 'fault',
      faultResetPresent: true, zonelessControls: TX_AUX_ZONELESS_CONTROLS,
    }),
  },
  {
    id: 'connection-loss-stale',
    what: 'radio link lost, values retained but every field STALE — every fact degrades to unknown.',
    state: () => mainSubState('MAIN', stale), caps: mainSubCaps, tx: tx({}),
    // MOR-1355: `mainSubCaps` carries txAux evidence, no plan supplied.
    expect: mainSubExpect({
      stripActive: [false, false], selectsEnabled: 4, selectsDisabled: 0,
      radioWideSwitchesDisabled: true, keyDisabled: true,
      zonelessControls: TX_AUX_ZONELESS_CONTROLS,
    }),
  },
  {
    id: 'connection-loss-state-null',
    what: 'reconnect window — capabilities known, no state payload at all; everything present and inert.',
    state: () => null, caps: mainSubCaps, tx: tx({}),
    // MOR-1355: `toRadioViewModel` gates only on `caps` being non-null
    // (`radio-view-model-adapter.ts:1195`), so `mainSubCaps`'s txAux evidence
    // still emits the group here even though `state` is null — no plan
    // supplied, so still NO-ZONE.
    expect: mainSubExpect({
      stripActive: [false, false], tiles: 2, selectsEnabled: 0, selectsDisabled: 2,
      radioWideSwitchesDisabled: true, keyDisabled: true,
      zonelessControls: TX_AUX_ZONELESS_CONTROLS,
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
    // MOR-1085 checklist item 2: renamed from `zoneless-controls`. The old id
    // and `what` both dated to before MOR-1258 moved these three controls'
    // render site — `tx-fault-reset` and the two `ModInputTxWarning` buttons
    // — to sit BESIDE `RxTxSurface` inside the bound `.rx-tx-zone` div. They
    // are formal members of the `rx-tx` zone now (`zonelessControls: 0`
    // below already asserted that; only the name and prose still claimed
    // the pre-MOR-1258 shape). The fixture still earns its keep: it is the
    // one state where all three conditional controls render simultaneously,
    // which is what acceptance gate (b) actually needs proving.
    id: 'tx-adjacent-alerts',
    what: 'acceptance gate (b): the three conditional controls render INSIDE the rx-tx zone (MOR-1258), '
      + 'never zone-less, even with all three present at once.',
    state: () => mainSubState('MAIN'), caps: mainSubCaps,
    tx: tx({ phase: 'failed', radioTx: 'unknown', txRisk: 'uncertain', fault: 'audio-failed' }),
    modGuard: { visible: true, sourceLabel: 'MIC' },
    // MOR-1355: `mainSubCaps` carries txAux evidence, no plan supplied — the
    // acceptance gate this fixture proves (the three TX-adjacent alerts are
    // inside `rx-tx`, never zone-less) is UNCHANGED; the 13 txAux controls
    // are a separate, honestly-disclosed zone-less class alongside it.
    expect: mainSubExpect({
      keyDisabled: true, rfLabel: 'TX?', sessionLabel: 'fault',
      faultResetPresent: true, modInputWarningPresent: true,
      zonelessControls: TX_AUX_ZONELESS_CONTROLS,
    }),
  },
];

/**
 * MOR-1085 — derives a fixture's reference-layout twin: same state, caps, tx
 * and mod-guard (the RADIO doesn't change, only which real component mounts
 * it), `layout: 'reference'`, and an `expect` rewritten for the structural
 * shape `ReferenceLayout.svelte` actually renders:
 *
 *   - `zones: []`, `strips: 0`, `stripReceivers/Operational/Active: []` —
 *     the single composition never creates a `data-zone-id` element or a
 *     `channel-strip` div (see `zonedComposition` in `assertions.ts`); all
 *     of a topology's vfos render inside ONE `<VfoSurface>` call instead of
 *     per-receiver strips.
 *   - `zonedComposition: false` — turns off every cockpit-only assertion
 *     (inert scope/controls placeholders, the zone-order acceptance gate)
 *     that presupposes a zone wrapper.
 *   - `rxAudioSurfacePresent` — `RxAudioSurface` mounts in the single
 *     composition whenever a view model exists (MOR-1279; every fixture's
 *     `baseCaps`-derived caps carry `audio: true`, so this is universal, not
 *     per-topology); `rfLabel !== null` is the same "view model exists" test
 *     `single-tx-authority-surface` already uses (`toRadioViewModel` returns
 *     null exactly when caps is null).
 *   - `tiles`, `selectsEnabled/Disabled`, `keyDisabled`, `rfLabel`,
 *     `sessionLabel`, `radioWideSwitchesDisabled`, `faultResetPresent`,
 *     `modInputWarningPresent`, `scopeFacts` carry over UNCHANGED: they are
 *     properties of the view model (`toRadioViewModel(state, caps, tx, …)`),
 *     which is composition-independent — splitting the same vfos into per-
 *     receiver strips does not change how many are selectable or what the TX
 *     readout says. This is verified, not assumed: every reference fixture
 *     below was run through `capture.mjs` and its assertions pass (see the
 *     MOR-1085 report), so a wrong carry-over would have shown up as a real
 *     `select-gating`/`tx-readout`/etc. failure, not a silent mismatch.
 *   - `zonelessControls` stays whatever the source declared (irrelevant here
 *     — the check that reads it is itself skipped when `zonedComposition`
 *     is false).
 */
/**
 * MOR-1085 FINDING (see the report): the reference/single composition's
 * `vfoSurface()` snippet never passes a `disabled` prop to `<VfoSurface>` —
 * only the dual composition's per-strip wiring does
 * (`disabled={!isOperationalStrip(view, receiverId)}`). `selectDisabled`
 * (`VfoSurface.svelte`) is `selectable && (vfo.slot.kind === 'unknown' ||
 * disabled)`, so on the reference layout the MOR-1256 two-level gate never
 * engages: a structurally-dual, operationally-degraded receiver's select
 * stays fully enabled there today, verified against the actual capture
 * (`3 enabled / 0 disabled`, not the cockpit's `1/2`). This is real,
 * pre-existing production behavior this ticket observes, not a bug this
 * catalog papers over — the two ids below are exactly the fixtures whose
 * `dual_rx`-unavailable capability makes the cockpit disable a select; their
 * reference twins keep the HEALTHY-baseline gating numbers instead of
 * inheriting the cockpit's.
 */
const REFERENCE_SELECT_GATING_OVERRIDE: Readonly<Record<string, Pick<Expectation,
  'selectsEnabled' | 'selectsDisabled'>>> = {
  'dual-rx-unavailable': { selectsEnabled: 3, selectsDisabled: 0 },
  'topology-2-ab-shared-unsupported-controls': { selectsEnabled: 1, selectsDisabled: 0 },
};

function toReferenceFixture(f: Fixture): Fixture {
  return {
    id: `${f.id}--reference`,
    what: `${f.what} [reference layout: SemanticRadioSurfaces strips="single", the wiring `
      + 'desktop-v2/sdr-test compose today — see ReferenceLayout.svelte].',
    state: f.state, caps: f.caps, tx: f.tx, modGuard: f.modGuard,
    audioRuntime: f.audioRuntime,
    layout: 'reference',
    expect: {
      ...f.expect,
      zones: [], strips: 0, stripReceivers: [], stripOperational: [], stripActive: [],
      zonedComposition: false,
      rxAudioSurfacePresent: f.caps() !== null,
      ...REFERENCE_SELECT_GATING_OVERRIDE[f.id],
    },
  };
}

/**
 * MOR-1392 — the browser-audio axis, isolated from topology/TX fixtures.
 * These three cells share one radio and one reference layout; only the
 * runtime audio facts vary. The down/up pair pins both link polarities while
 * live is selected, and the mute/down cell proves `muted` wins over
 * `rxEnabled` without making an epistemically false link-loss claim.
 */
const audioRuntimeFixture = (
  id: string,
  audioRuntime: Partial<AudioRuntimeState>,
  rxAudio: NonNullable<Expectation['rxAudio']>,
): Fixture => toReferenceFixture({
  id,
  what: `MOR-1392 audio-runtime axis: ${id}.`,
  state: () => mainSubState('MAIN'),
  caps: mainSubCaps,
  tx: tx({}),
  audioRuntime,
  expect: mainSubExpect({ rxAudioSurfacePresent: true, rxAudio }),
});

const AUDIO_RUNTIME_FIXTURES: readonly Fixture[] = [
  audioRuntimeFixture(
    'rx-audio-live-link-down',
    { rxEnabled: true, muted: false, volume: 37, connectionAudio: false },
    { monitorMode: 'live', volume: 37, connectionAudio: false, linkPresent: true },
  ),
  audioRuntimeFixture(
    'rx-audio-live-link-up',
    { rxEnabled: true, muted: false, volume: 73, connectionAudio: true },
    { monitorMode: 'live', volume: 73, connectionAudio: true, linkPresent: false },
  ),
  audioRuntimeFixture(
    'rx-audio-muted-link-down',
    { rxEnabled: true, muted: true, volume: 19, connectionAudio: false },
    { monitorMode: 'mute', volume: null, connectionAudio: false, linkPresent: false },
  ),
];

/**
 * MOR-1355 — the harness's first PLAN-FUL topology. `planned: true` makes
 * `fixtures/main.ts` mount this fixture with a REAL
 * `resolveSurfacePlan(dualReceiverCockpitLayout, …)` plan (default
 * workspace — no operator preference), the same recipe
 * `DualReceiverCockpit.component.test.ts`'s `render(defaultPlan())` proves.
 * Same radio as `topology-2-main-sub` (state/caps/tx all byte-identical) so
 * the ONLY variable is plan-ful vs plan-less — a controlled pair, not a new
 * radio shape.
 *
 * Kept OUT of `CORE_FIXTURES` (and so out of `toReferenceFixture`'s
 * `--reference` derivation) deliberately: a `--reference` twin would mount
 * `ReferenceLayout`/`desktop-v2`'s wiring, whose manifest
 * (`desktopV2Layout`) declares a DIFFERENT zone set (`receiver-deck`,
 * `meters`, `scope-display`, `filter`, `rf-front-end` in addition to
 * `tx-aux`) — plumbing that through is real, separately-scoped work
 * (`main.ts` only resolves `dualReceiverCockpitLayout` today), not implied
 * by this one topology. Left as a named follow-up in the build report
 * rather than silently mounted plan-less under a `--planned` name that
 * would claim more than it does.
 *
 * `zones` gains `tx-aux` (`DUAL_ZONES_PLANNED`) — the manifest's declared
 * zone the resolved plan actually binds — and `zonelessControls: 0` is now
 * HONEST: the same 13 `TxAuxSurface` controls every plan-less `main_sub`
 * fixture above counts as NO-ZONE land inside `data-zone-id="tx-aux"` here.
 * `assertions.ts`'s `focus-order-is-zone-order` proves the ordering claim:
 * the zoned index sequence must still end at the LAST declared zone, which
 * is `tx-aux` now, not `rx-tx` — MOR-1344's zone-INDEPENDENT
 * `logical-focus-order-vfo-precedes-rx-tx-authority` is untouched by any of
 * this (it never reads `data-zone-id` at all), so both the zone-aware and
 * zone-free tab-order invariants are exercised on the SAME capture.
 */
const PLANNED_FIXTURES: readonly Fixture[] = [
  {
    id: 'topology-2-main-sub--planned',
    what: '2/main_sub with a REAL resolved SurfacePlan (dualReceiverCockpitLayout, default '
      + 'workspace) — tx-aux is genuinely BOUND, not merely declared; zonelessControls is honest 0.',
    state: () => withMeters(mainSubState('MAIN')), caps: mainSubCaps, tx: tx({}),
    planned: true,
    expect: mainSubExpect({
      zones: DUAL_ZONES_PLANNED,
      scopeFacts: {
        hardwareScope: { structural: true, operational: false },
        audioFftScope: { structural: false, operational: false },
      },
      rxAudioSurfacePresent: false,
    }),
  },
];

/**
 * The full MOR-1085 grid: every `CORE_FIXTURES` (dual-receiver-cockpit)
 * entry, plus its reference-layout twin — except `tx-adjacent-alerts`, whose
 * whole point is the cockpit's OWN zone-containment acceptance gate (b) and
 * has no reference-layout equivalent (the reference/single composition has
 * no zone concept for the three alerts to be "inside" or "outside" of; see
 * `zonedComposition` in `assertions.ts`) — plus MOR-1355's `PLANNED_FIXTURES`
 * (no reference twin either; see the comment above `PLANNED_FIXTURES`).
 */
export const FIXTURES: readonly Fixture[] = [
  ...CORE_FIXTURES,
  ...CORE_FIXTURES.filter((f) => f.id !== 'tx-adjacent-alerts').map(toReferenceFixture),
  ...PLANNED_FIXTURES,
  ...AUDIO_RUNTIME_FIXTURES,
];

export const fixtureById = (id: string): Fixture | undefined =>
  FIXTURES.find((f) => f.id === id);
