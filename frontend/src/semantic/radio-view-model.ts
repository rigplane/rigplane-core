/**
 * Radio semantic view-model contract (MOR-1062).
 *
 * The seam between adapters (which consume runtime state and capabilities —
 * see `lib/runtime/adapters/*`) and semantic UI (which renders only this
 * shape and nothing else — no transport, store, or manufacturer knowledge).
 * A design language may change how a fact looks; it may never change which
 * facts exist. See docs/plans/2026-07-25-ui-composition-architecture-v3.md
 * and the MOR-977 shared semantic skeleton (Linear comment, 2026-08-03).
 *
 * `ActiveRx` and the scheme-conditioned slot/target shapes below mirror the
 * exact identity types frozen by the MOR-988 "Accepted capability and
 * presentation semantics" decision (§3.2, §4) — this contract reuses that
 * vocabulary rather than reinventing it. Every "unknown"/"denied" branch is
 * intentional and must survive round-tripping — collapsing it into a
 * boolean or a default is the failure mode this contract exists to prevent.
 */
import type { VfoScheme } from '$lib/types/capabilities';
import type { FrequencyPermit } from '$lib/utils/tx-permit';
import { invalid, record, exactKeys, str } from './validator-primitives';

export type ReceiverId = 'MAIN' | 'SUB';
export type VfoSlotId = 'A' | 'B';

/**
 * Whether a VFO/target position has an addressable A/B slot at all, distinct
 * from whether that slot was actually observed. `unslotted` = the scheme has
 * no A/B concept here (`single`, `ab_shared`); `unknown` = a slotted scheme
 * (`ab`, `main_sub`) whose slot could not be observed — MOR-988 §3.2/§4:
 * missing/stale never synthesizes `A`.
 */
export type VfoSlot =
  | { kind: 'slotted'; id: VfoSlotId }
  | { kind: 'unslotted' }
  | { kind: 'unknown' };

/** MOR-988 §3.2 `ActiveRx`, verbatim: an adapter with no observation must never fabricate 'MAIN'. */
export type ActiveRx =
  | { status: 'known'; receiver: ReceiverId }
  | { status: 'unknown' };

/** A boolean radio fact (`split`, `dualWatch`) that can itself be unobserved. */
export type BooleanFact =
  | { status: 'known'; value: boolean }
  | { status: 'unknown' };

/**
 * Structural = the radio model supports this. Operational = usable right
 * now, given live capability AND field-observed state. MOR-977 two-level
 * gating: a control that fails the STRUCTURAL half is absent (nothing to
 * render); a control that fails only the OPERATIONAL half stays present but
 * disabled, degrading to an explicit unknown/disabled state rather than a
 * guessed default — MOR-988 §11.3: "Old v1 servers lacking additive fields
 * degrade to `unknown`/disabled in v3, not guessed behavior."
 */
export interface Availability {
  structural: boolean;
  operational: boolean;
}

export interface VfoViewModel {
  receiver: ReceiverId;
  slot: VfoSlot;
  label: string;
  frequencyHz: number | null;
  mode: string | null;
  filter: string | null;
  isActive: boolean;
  isTxTarget: boolean;
}

export type TxTargetViewModel =
  | { status: 'known'; receiver: ReceiverId; slot: VfoSlot; frequencyHz: number | null }
  | { status: 'unknown'; reason: 'not-observed' | 'stale' | 'unsupported' | 'contradiction' };

export interface ScopeAvailabilityViewModel {
  hardwareScope: Availability;
  audioFftScope: Availability;
}

export type DisabledReasonCode =
  | 'capability-unavailable'
  | 'field-not-observed'
  | 'tx-target-unknown'
  | 'out-of-band';

export interface DisabledReason {
  field: string;
  code: DisabledReasonCode;
}

/**
 * A single TX-adjacent fact (MOR-1244): a value that is either known (with
 * its type-checked reading) or unknown, paired with the MOR-977 two-level
 * `Availability` — structural (does this radio model have the control at
 * all) and operational (is it currently readable). The two are independent:
 * `structural: false` means nothing to render; `structural: true,
 * operational: false` means present-but-disabled, degrading to `unknown`
 * rather than a guessed value, same doctrine as every other fact here.
 */
export type TxAuxReading<T> = { status: 'known'; value: T } | { status: 'unknown' };
export interface TxAuxField<T> {
  reading: TxAuxReading<T>;
  availability: Availability;
}
export type AtuStatus = 'off' | 'on' | 'tuning';

/**
 * TX-adjacent facts (MOR-1244, MOR-1262 decomposition slice 1A): ATU/TUNE,
 * VOX (+gain/anti-vox/delay), COMP (+level), MON (+level), RF power, mic
 * gain, drive gain. Facts only — no action/dispatch. ATU TUNE is a
 * transmit-causing action; this contract carries only its honestly-gated
 * *state*, never a control to trigger it (MOR-1262 §2 slice 1 safety note i).
 */
export interface TxAuxViewModel {
  atu: TxAuxField<AtuStatus>;
  vox: TxAuxField<boolean>;
  voxGain: TxAuxField<number>;
  antiVoxGain: TxAuxField<number>;
  voxDelay: TxAuxField<number>;
  compressor: TxAuxField<boolean>;
  compressorLevel: TxAuxField<number>;
  monitor: TxAuxField<boolean>;
  monitorLevel: TxAuxField<number>;
  rfPower: TxAuxField<number>;
  micGain: TxAuxField<number>;
  driveGain: TxAuxField<number>;
}

/**
 * A single meter fact (MOR-1262 decomposition slice 2A): a numeric reading
 * that is either known or unknown, the MOR-977 two-level `Availability`, and
 * whether the meter reads meaningfully in the CURRENT RF state.
 *
 * `relevant` is the one field of this contract that must NOT be derived from
 * radio state: TX-gated meters (Po/SWR/ALC/COMP/Id) are relevant exactly when
 * the App-owned TX authority — the same source as the AppGlobalHost lamp
 * (MOR-1008/MOR-1059) — says the transmitter may be live. Deriving it from
 * `radioState.ptt` is the open disagreement MOR-1235 reports, and safety
 * invariant R9 forbids reintroducing it here: this contract EXPOSES the
 * authority's conclusion, it never computes one.
 */
export type MeterReading = { status: 'known'; value: number } | { status: 'unknown' };
export interface MeterField {
  reading: MeterReading;
  availability: Availability;
  relevant: boolean;
}

/**
 * The authoritative RF state the meters are read against. Member-for-member
 * the RX/TX surface's own `RfState` (`rx-tx-surface.ts`, MOR-1064) — declared
 * again here rather than imported because that module already imports this
 * one, and a contract must not depend on a surface. Agreement is pinned in
 * `__tests__/meters.test.ts` against the real union and the real reducer.
 */
export type MeterRfState = 'receiving' | 'transmitting' | 'uncertain' | 'unknown';

/**
 * Meter facts (MOR-1262 decomposition slice 2A): S / Po / SWR / ALC / COMP /
 * Vd / Id — the seven meters the shipped v2 dock renders. Facts only: no
 * ballistics, no peak-hold, no formatting (those are presentation, slice 2B).
 */
export interface MetersViewModel {
  rfState: MeterRfState;
  signal: MeterField;
  power: MeterField;
  swr: MeterField;
  alc: MeterField;
  compression: MeterField;
  drainVoltage: MeterField;
  drainCurrent: MeterField;
}

/**
 * A single RX-audio fact (MOR-1262 decomposition slice 3A, MOR-1274). Shape-
 * identical to `TxAuxField` — `{reading, availability}`, no third member —
 * so it is declared as an alias rather than a near-duplicate: one field shape
 * for every non-meter fact family, and slice 1A's declaration is left
 * untouched. The validator is shared for the same reason.
 */
export type RxAudioReading<T> = TxAuxReading<T>;
export type RxAudioField<T> = TxAuxField<T>;

/** What the operator is listening to, verbatim the shipped `RxAudioProps`
 *  vocabulary (`lib/runtime/props/panel-props.ts::toRxAudioProps`): `local` =
 *  the rig's own speaker, `live` = the browser RX stream, `mute` = silenced. */
export type MonitorMode = 'local' | 'live' | 'mute';

/** Dual-receiver audio routing focus, verbatim `AudioRoutingControl.svelte`. */
export type AudioFocus = 'main' | 'sub' | 'both';

/**
 * MOD-input readiness — the recorded "web voice TX = noise/squeal" guard.
 * Member-for-member `lib/runtime/adapters/tx-capabilities.ts`'s own
 * `ModInputReadiness`, declared again here for the same reason `MeterRfState`
 * is (a contract must not depend on an adapter); the adapter EXPOSES
 * `deriveTxCapabilities(...).modInputReadiness` rather than re-deriving it,
 * and both the union and the derivation are pinned in `__tests__/rx-audio.test.ts`.
 * `mismatch` is the failure the whole family exists to make visible: the rig
 * is modulating from MIC/ACC/USB while the web UI streams audio over LAN.
 */
export type ModInputReadiness =
  | { status: 'not-applicable' }
  | { status: 'ready'; source: number }
  | { status: 'mismatch'; source: number }
  | { status: 'unknown' };

/**
 * RX audio-chain facts (MOR-1262 decomposition slice 3A). Facts only — no
 * transport, no AudioContext, no lifetime: audio lifetime is App-owned
 * (MOR-1058, MOR-972 P0) and this group is a pure read-model over a snapshot
 * the App hands in. Constructing or serializing it must never start a stream.
 *
 * `monitorMode` and `liveAudio` come from that App-owned snapshot (the same
 * relationship `meters.rfState` has to the TX authority); `afLevel` /
 * `routingFocus` / `routingSplit` / `modInputSource` are two-level-gated facts
 * that degrade to `unknown` rather than to the shipped panel's fabricated
 * defaults (0.5 AF, 'both' focus).
 */
export interface RxAudioViewModel {
  monitorMode: MonitorMode;
  /** Structural = the radio streams audio at all; operational = the audio WS
   *  is up. Without it a surface cannot honestly offer the `live` mode. */
  liveAudio: Availability;
  /** 0..1. In `live` mode the browser volume; otherwise the radio's AF level —
   *  and `unknown` when that field was never observed, never 0.5. */
  afLevel: RxAudioField<number>;
  routingFocus: RxAudioField<AudioFocus>;
  routingSplit: RxAudioField<boolean>;
  /** The active DATA group's MOD-input source enum (`$lib/radio/mod-input`). */
  modInputSource: RxAudioField<number>;
  modInputReadiness: ModInputReadiness;
}

/**
 * A single mode/filter fact (MOR-1262 decomposition slice 4A, MOR-1280).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `RxAudioField` is: one field shape per fact family, no near-duplicate.
 */
export type ModeFilterField<T> = TxAuxField<T>;

/**
 * Mode/filter facts (MOR-1262 decomposition slice 4A). Facts only — no
 * command emission; choosing a mode or dragging the filter-width control
 * stays with the surface (slice 4B).
 *
 * `modeChoices`/`filterChoices` are the capability-derived choice sets
 * (`Capabilities.modes` / `.filters`, verbatim) — plain lists, not
 * `ModeFilterField`-wrapped, because a choice set is a structural fact about
 * the radio MODEL, not a live reading that can itself go stale. The current
 * selection and the width bounds ARE readings, and degrade to `unknown`
 * rather than to `toFilterProps`'s fabricated defaults ('USB', 2400 Hz,
 * 50..9999 Hz) — see `radio-view-model-adapter.ts`'s `deriveModeFilter`.
 *
 * `filterWidthMin`/`filterWidthMax` are the ONE remaining consumer of
 * `resolveFilterModeConfig`'s per-mode table lookup that this slice adds;
 * the X6200 CAT-audit lesson (filter-width codecs are radio-specific) is why
 * this group never re-derives that table itself — it reads the shipped
 * resolver's own output, like `modInputReadiness` reads `deriveTxCapabilities`.
 */
export interface ModeFilterViewModel {
  currentMode: ModeFilterField<string>;
  modeChoices: readonly string[];
  currentFilter: ModeFilterField<number>;
  filterChoices: readonly string[];
  filterWidth: ModeFilterField<number>;
  filterWidthMin: ModeFilterField<number>;
  filterWidthMax: ModeFilterField<number>;
}

/**
 * A single filter-passband fact (MOR-1262 decomposition slice 4A′, MOR-1284).
 * Shape-identical to `TxAuxField`, declared as an alias for the same reason
 * `ModeFilterField`/`RxAudioField` are: one field shape per fact family.
 */
export type FilterPassbandField<T> = TxAuxField<T>;

/**
 * Filter-passband facts (MOR-1262 decomposition slice 4A′, MOR-1284): filter
 * shape, IF-shift, passband tuning (PBT) inner/outer, and the DATA-mode
 * selector these controls key off. Facts only — no command emission, same
 * doctrine as `modeFilter` (MOR-1280).
 *
 * A SEPARATE group from `modeFilter` rather than five more keys on it: 4A
 * covers discrete SELECTION facts (which mode/filter is chosen, and the
 * capability-declared choice sets) gated by a single required-field-is-
 * signal-free capability check. This family covers continuous passband-
 * SHAPING facts (family 9 of the MOR-1262 decomposition — FilterPanel +
 * `filter-controls`) with a different, per-field evidence story (see
 * `deriveFilterPassband`). Folding both into one `exactKeys` list would mix
 * two evidence-gate shapes under one allow-list and force every 4B
 * passband-only consumer to import the selection keys too. One group per
 * family, same precedent as `txAux`/`meters`/`rxAudio`/`modeFilter`.
 *
 * `ifShift`/`pbtInner`/`pbtOuter` are the ONE remaining consumer of
 * `$lib/radio/filter-controls`'s `pbtRawToHz`/`deriveIfShift` — the exact
 * functions `toFilterProps` calls — never a re-derived formula (X6200
 * lesson: PBT/filter scaling is per-radio-model data). Unlike `toFilterProps`,
 * the adapter passes `pbtRawToHz` an explicit `PbtRange` derived from THIS
 * request's own `caps` argument (`pbtRangeFromCaps`, MOR-1284 F1) rather than
 * letting it fall back to the capabilities STORE singleton — a fact-layer
 * value must be a pure function of `(state, caps)`, never of module-global
 * state that can differ from the `caps` already in hand.
 */
export interface FilterPassbandViewModel {
  filterShape: FilterPassbandField<number>;
  ifShift: FilterPassbandField<number>;
  pbtInner: FilterPassbandField<number>;
  pbtOuter: FilterPassbandField<number>;
  dataMode: FilterPassbandField<number>;
}

export interface RadioViewModel {
  topologyId: string;
  vfoScheme: VfoScheme;
  activeReceiver: ActiveRx;
  vfos: readonly VfoViewModel[];
  /** Orthogonal wire booleans (state.ts `split`/`dualWatch`; independent CI-V
   *  commands) — both may be true, false, or unobserved independently. */
  split: BooleanFact;
  dualWatch: BooleanFact;
  txTarget: TxTargetViewModel;
  txPermit: FrequencyPermit;
  scope: ScopeAvailabilityViewModel;
  disabledReasons: readonly DisabledReason[];
  /** Absent (MOR-1264 optional group) ⇒ structurally unavailable: this radio
   *  model has no TX-adjacent controls at all. Never emitted as a placeholder
   *  of all-unknowns — see `radio-view-model-adapter.ts`'s evidence gate. */
  readonly txAux?: TxAuxViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio reports no meters at all,
   *  or the App TX authority was not supplied so no honest TX-relevance could
   *  be stated — see `radio-view-model-adapter.ts`'s `deriveMeters`. */
  readonly meters?: MetersViewModel;
  /** Absent (MOR-1264 optional group) ⇒ the App-owned RX-audio snapshot was
   *  not supplied, or this radio has no RX-audio chain to describe — see
   *  `radio-view-model-adapter.ts`'s `deriveRxAudio`. */
  readonly rxAudio?: RxAudioViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares no modes and no
   *  filters and nothing mode/filter-shaped was ever observed — see
   *  `radio-view-model-adapter.ts`'s `deriveModeFilter`. */
  readonly modeFilter?: ModeFilterViewModel;
  /** Absent (MOR-1264 optional group) ⇒ this radio declares no filters, no
   *  PBT, no IF-shift and no DATA-mode capability — see
   *  `radio-view-model-adapter.ts`'s `deriveFilterPassband`. */
  readonly filterPassband?: FilterPassbandViewModel;
}

const RECEIVER_IDS: readonly ReceiverId[] = ['MAIN', 'SUB'];
const SLOT_IDS: readonly VfoSlotId[] = ['A', 'B'];
const VFO_SCHEMES: readonly VfoScheme[] = ['single', 'ab', 'ab_shared', 'main_sub'];
const DISABLED_REASON_CODES: readonly DisabledReasonCode[] = [
  'capability-unavailable', 'field-not-observed', 'tx-target-unknown', 'out-of-band',
];

function oneOf<T>(value: unknown, allowed: readonly T[], path: string): T {
  if (!allowed.includes(value as T)) invalid(path, allowed.join(' | '));
  return value as T;
}
function bool(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') invalid(path, 'a boolean');
  return value;
}
function nullableNumber(value: unknown, path: string): number | null {
  if (value !== null && (typeof value !== 'number' || !Number.isFinite(value))) {
    invalid(path, 'a finite number or null');
  }
  return value as number | null;
}
function nullableString(value: unknown, path: string): string | null {
  if (value !== null && typeof value !== 'string') invalid(path, 'a string or null');
  return value as string | null;
}
function num(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) invalid(path, 'a finite number');
  return value;
}

/**
 * Declares a fact group as optional (MOR-1264, decision (b) of the MOR-1262
 * decomposition's slice 0): present ⇒ validated strictly by `validate` (the
 * group runs its own `exactKeys`/shape checks, unchanged); absent (`value`
 * is `undefined`) ⇒ the field stays `undefined` on the returned model and
 * the family is structurally unavailable — the MOR-988 §11.3 degrade-to-
 * unknown doctrine applied at the group level, not a new relaxation.
 *
 * A group is optional *only* because its key is listed in the containing
 * `exactKeys(...)` allow-list alongside the required keys — `exactKeys`
 * itself is untouched, so a key absent from that list is still rejected as
 * an extra. This is what keeps "optional" bounded rather than "anything
 * goes". A future slice declares and reads its group with one line each,
 * e.g. (MOR-1244, `txAux`) — `validateTxAux` built from the same
 * `record`/`exactKeys`/`str` imported from `./validator-primitives`:
 *   exactKeys(v, [...requiredKeys, 'txAux'], '$');
 *   txAux: optionalGroup(v.txAux, '$.txAux', validateTxAux),
 */
export function optionalGroup<T>(
  value: unknown,
  path: string,
  validate: (value: unknown, path: string) => T,
): T | undefined {
  return value === undefined ? undefined : validate(value, path);
}

function validateVfoSlot(value: unknown, path: string): VfoSlot {
  const v = record(value, path);
  if (v.kind === 'slotted') {
    exactKeys(v, ['kind', 'id'], path);
    return { kind: 'slotted', id: oneOf(v.id, SLOT_IDS, `${path}.id`) };
  }
  if (v.kind === 'unslotted') {
    exactKeys(v, ['kind'], path);
    return { kind: 'unslotted' };
  }
  if (v.kind === 'unknown') {
    exactKeys(v, ['kind'], path);
    return { kind: 'unknown' };
  }
  invalid(`${path}.kind`, `'slotted' | 'unslotted' | 'unknown'`);
}
function slotEqual(a: VfoSlot, b: VfoSlot): boolean {
  return a.kind === 'slotted' && b.kind === 'slotted' ? a.id === b.id : a.kind === b.kind;
}

function validateActiveRx(value: unknown, path: string): ActiveRx {
  const v = record(value, path);
  if (v.status === 'known') {
    exactKeys(v, ['status', 'receiver'], path);
    return { status: 'known', receiver: oneOf(v.receiver, RECEIVER_IDS, `${path}.receiver`) };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status'], path);
    return { status: 'unknown' };
  }
  invalid(`${path}.status`, `'known' | 'unknown'`);
}

function validateBooleanFact(value: unknown, path: string): BooleanFact {
  const v = record(value, path);
  if (v.status === 'known') {
    exactKeys(v, ['status', 'value'], path);
    return { status: 'known', value: bool(v.value, `${path}.value`) };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status'], path);
    return { status: 'unknown' };
  }
  invalid(`${path}.status`, `'known' | 'unknown'`);
}

function validateAvailability(value: unknown, path: string): Availability {
  const v = record(value, path);
  exactKeys(v, ['structural', 'operational'], path);
  return { structural: bool(v.structural, `${path}.structural`), operational: bool(v.operational, `${path}.operational`) };
}

function validateVfo(value: unknown, path: string): VfoViewModel {
  const v = record(value, path);
  exactKeys(v, ['receiver', 'slot', 'label', 'frequencyHz', 'mode', 'filter', 'isActive', 'isTxTarget'], path);
  return {
    receiver: oneOf(v.receiver, RECEIVER_IDS, `${path}.receiver`),
    slot: validateVfoSlot(v.slot, `${path}.slot`),
    label: str(v.label, `${path}.label`),
    frequencyHz: nullableNumber(v.frequencyHz, `${path}.frequencyHz`),
    mode: nullableString(v.mode, `${path}.mode`),
    filter: nullableString(v.filter, `${path}.filter`),
    isActive: bool(v.isActive, `${path}.isActive`),
    isTxTarget: bool(v.isTxTarget, `${path}.isTxTarget`),
  };
}

function validateTxTarget(value: unknown, path: string): TxTargetViewModel {
  const v = record(value, path);
  if (v.status === 'known') {
    exactKeys(v, ['status', 'receiver', 'slot', 'frequencyHz'], path);
    return {
      status: 'known',
      receiver: oneOf(v.receiver, RECEIVER_IDS, `${path}.receiver`),
      slot: validateVfoSlot(v.slot, `${path}.slot`),
      frequencyHz: nullableNumber(v.frequencyHz, `${path}.frequencyHz`),
    };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status', 'reason'], path);
    return {
      status: 'unknown',
      reason: oneOf(v.reason, ['not-observed', 'stale', 'unsupported', 'contradiction'] as const, `${path}.reason`),
    };
  }
  invalid(`${path}.status`, `'known' | 'unknown'`);
}

function validateTxPermit(value: unknown, path: string): FrequencyPermit {
  const v = record(value, path);
  if (v.status === 'allowed') {
    exactKeys(v, ['status', 'band'], path);
    return { status: 'allowed', band: nullableString(v.band, `${path}.band`) };
  }
  if (v.status === 'denied') {
    exactKeys(v, ['status', 'reason'], path);
    return { status: 'denied', reason: oneOf(v.reason, ['outside-configured-ranges'] as const, `${path}.reason`) };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status', 'reason'], path);
    return {
      status: 'unknown',
      reason: oneOf(v.reason, ['ranges-unconfigured', 'tx-target-unknown'] as const, `${path}.reason`),
    };
  }
  invalid(`${path}.status`, `'allowed' | 'denied' | 'unknown'`);
}

function validateDisabledReason(value: unknown, path: string): DisabledReason {
  const v = record(value, path);
  exactKeys(v, ['field', 'code'], path);
  return { field: str(v.field, `${path}.field`), code: oneOf(v.code, DISABLED_REASON_CODES, `${path}.code`) };
}

const METER_RF_STATES: readonly MeterRfState[] = ['receiving', 'transmitting', 'uncertain', 'unknown'];

function validateMeterField(value: unknown, path: string): MeterField {
  const v = record(value, path);
  exactKeys(v, ['reading', 'availability', 'relevant'], path);
  const r = record(v.reading, `${path}.reading`);
  let reading: MeterReading;
  if (r.status === 'known') {
    exactKeys(r, ['status', 'value'], `${path}.reading`);
    reading = { status: 'known', value: num(r.value, `${path}.reading.value`) };
  } else if (r.status === 'unknown') {
    exactKeys(r, ['status'], `${path}.reading`);
    reading = { status: 'unknown' };
  } else {
    invalid(`${path}.reading.status`, "'known' | 'unknown'");
  }
  return {
    reading,
    availability: validateAvailability(v.availability, `${path}.availability`),
    relevant: bool(v.relevant, `${path}.relevant`),
  };
}

/** This `exactKeys` list is exactly the seven meters the adapter reads plus
 *  the authoritative `rfState` — no speculative keys (MOR-1244 finding N4).
 *  See `radio-view-model-adapter.ts::deriveMeters`. */
function validateMeters(value: unknown, path: string): MetersViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'rfState', 'signal', 'power', 'swr', 'alc', 'compression', 'drainVoltage', 'drainCurrent',
  ], path);
  return {
    rfState: oneOf(v.rfState, METER_RF_STATES, `${path}.rfState`),
    signal: validateMeterField(v.signal, `${path}.signal`),
    power: validateMeterField(v.power, `${path}.power`),
    swr: validateMeterField(v.swr, `${path}.swr`),
    alc: validateMeterField(v.alc, `${path}.alc`),
    compression: validateMeterField(v.compression, `${path}.compression`),
    drainVoltage: validateMeterField(v.drainVoltage, `${path}.drainVoltage`),
    drainCurrent: validateMeterField(v.drainCurrent, `${path}.drainCurrent`),
  };
}

const ATU_STATUSES: readonly AtuStatus[] = ['off', 'on', 'tuning'];

function validateTxAuxField<T>(
  value: unknown, path: string, validateValue: (v: unknown, p: string) => T,
): TxAuxField<T> {
  const v = record(value, path);
  exactKeys(v, ['reading', 'availability'], path);
  const r = record(v.reading, `${path}.reading`);
  let reading: TxAuxReading<T>;
  if (r.status === 'known') {
    exactKeys(r, ['status', 'value'], `${path}.reading`);
    reading = { status: 'known', value: validateValue(r.value, `${path}.reading.value`) };
  } else if (r.status === 'unknown') {
    exactKeys(r, ['status'], `${path}.reading`);
    reading = { status: 'unknown' };
  } else {
    invalid(`${path}.reading.status`, "'known' | 'unknown'");
  }
  return { reading, availability: validateAvailability(v.availability, `${path}.availability`) };
}

/** N4: this `exactKeys` list is exactly the 12 fields the adapter reads —
 *  no speculative keys. See `radio-view-model-adapter.ts::deriveTxAux`. */
function validateTxAux(value: unknown, path: string): TxAuxViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'atu', 'vox', 'voxGain', 'antiVoxGain', 'voxDelay',
    'compressor', 'compressorLevel', 'monitor', 'monitorLevel',
    'rfPower', 'micGain', 'driveGain',
  ], path);
  return {
    atu: validateTxAuxField(v.atu, `${path}.atu`, (val, p) => oneOf(val, ATU_STATUSES, p)),
    vox: validateTxAuxField(v.vox, `${path}.vox`, bool),
    voxGain: validateTxAuxField(v.voxGain, `${path}.voxGain`, num),
    antiVoxGain: validateTxAuxField(v.antiVoxGain, `${path}.antiVoxGain`, num),
    voxDelay: validateTxAuxField(v.voxDelay, `${path}.voxDelay`, num),
    compressor: validateTxAuxField(v.compressor, `${path}.compressor`, bool),
    compressorLevel: validateTxAuxField(v.compressorLevel, `${path}.compressorLevel`, num),
    monitor: validateTxAuxField(v.monitor, `${path}.monitor`, bool),
    monitorLevel: validateTxAuxField(v.monitorLevel, `${path}.monitorLevel`, num),
    rfPower: validateTxAuxField(v.rfPower, `${path}.rfPower`, num),
    micGain: validateTxAuxField(v.micGain, `${path}.micGain`, num),
    driveGain: validateTxAuxField(v.driveGain, `${path}.driveGain`, num),
  };
}

const MONITOR_MODES: readonly MonitorMode[] = ['local', 'live', 'mute'];
const AUDIO_FOCUSES: readonly AudioFocus[] = ['main', 'sub', 'both'];

/** The `source` carried by `ready`/`mismatch` is the offending/confirmed
 *  MOD-input enum value; a bare status must NOT carry one (that would make
 *  "not applicable" indistinguishable from a read). */
function validateModInputReadiness(value: unknown, path: string): ModInputReadiness {
  const v = record(value, path);
  if (v.status === 'ready') {
    exactKeys(v, ['status', 'source'], path);
    return { status: 'ready', source: num(v.source, `${path}.source`) };
  }
  if (v.status === 'mismatch') {
    exactKeys(v, ['status', 'source'], path);
    return { status: 'mismatch', source: num(v.source, `${path}.source`) };
  }
  if (v.status === 'not-applicable') {
    exactKeys(v, ['status'], path);
    return { status: 'not-applicable' };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status'], path);
    return { status: 'unknown' };
  }
  invalid(`${path}.status`, "'not-applicable' | 'ready' | 'mismatch' | 'unknown'");
}

/** N4 again: exactly the seven facts the adapter reads, no speculative keys.
 *  The per-field validator is `validateTxAuxField` — `RxAudioField` IS
 *  `TxAuxField`, so sharing it is the alias's whole point, not a shortcut.
 *  See `radio-view-model-adapter.ts::deriveRxAudio`. */
function validateRxAudio(value: unknown, path: string): RxAudioViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'monitorMode', 'liveAudio', 'afLevel', 'routingFocus', 'routingSplit',
    'modInputSource', 'modInputReadiness',
  ], path);
  return {
    monitorMode: oneOf(v.monitorMode, MONITOR_MODES, `${path}.monitorMode`),
    liveAudio: validateAvailability(v.liveAudio, `${path}.liveAudio`),
    afLevel: validateTxAuxField(v.afLevel, `${path}.afLevel`, num),
    routingFocus: validateTxAuxField(
      v.routingFocus, `${path}.routingFocus`, (val, p) => oneOf(val, AUDIO_FOCUSES, p),
    ),
    routingSplit: validateTxAuxField(v.routingSplit, `${path}.routingSplit`, bool),
    modInputSource: validateTxAuxField(v.modInputSource, `${path}.modInputSource`, num),
    modInputReadiness: validateModInputReadiness(v.modInputReadiness, `${path}.modInputReadiness`),
  };
}

/** A capability-derived choice set: plain strings, no field-shape wrapper —
 *  see `ModeFilterViewModel`'s doc comment for why. */
function strArray(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) invalid(path, 'an array of strings');
  return value.map((item, i) => str(item, `${path}[${i}]`));
}

/** N4 again: exactly the seven facts the adapter reads, no speculative keys.
 *  See `radio-view-model-adapter.ts::deriveModeFilter`. */
function validateModeFilter(value: unknown, path: string): ModeFilterViewModel {
  const v = record(value, path);
  exactKeys(v, [
    'currentMode', 'modeChoices', 'currentFilter', 'filterChoices',
    'filterWidth', 'filterWidthMin', 'filterWidthMax',
  ], path);
  return {
    currentMode: validateTxAuxField(v.currentMode, `${path}.currentMode`, str),
    modeChoices: strArray(v.modeChoices, `${path}.modeChoices`),
    currentFilter: validateTxAuxField(v.currentFilter, `${path}.currentFilter`, num),
    filterChoices: strArray(v.filterChoices, `${path}.filterChoices`),
    filterWidth: validateTxAuxField(v.filterWidth, `${path}.filterWidth`, num),
    filterWidthMin: validateTxAuxField(v.filterWidthMin, `${path}.filterWidthMin`, num),
    filterWidthMax: validateTxAuxField(v.filterWidthMax, `${path}.filterWidthMax`, num),
  };
}

/** N4 again: exactly the five facts the adapter reads, no speculative keys.
 *  See `radio-view-model-adapter.ts::deriveFilterPassband`. */
function validateFilterPassband(value: unknown, path: string): FilterPassbandViewModel {
  const v = record(value, path);
  exactKeys(v, ['filterShape', 'ifShift', 'pbtInner', 'pbtOuter', 'dataMode'], path);
  return {
    filterShape: validateTxAuxField(v.filterShape, `${path}.filterShape`, num),
    ifShift: validateTxAuxField(v.ifShift, `${path}.ifShift`, num),
    pbtInner: validateTxAuxField(v.pbtInner, `${path}.pbtInner`, num),
    pbtOuter: validateTxAuxField(v.pbtOuter, `${path}.pbtOuter`, num),
    dataMode: validateTxAuxField(v.dataMode, `${path}.dataMode`, num),
  };
}

/** Runtime validator (repo idiom: throws TypeError with a `$.path`, see `validateCapabilities`).
 *  Also enforces two cross-field invariants (review cycle 1, V1): `txPermit`
 *  cannot be 'allowed' while `txTarget` is unknown (no fail-open), and
 *  `isTxTarget` can be true only on the VFO a known `txTarget` names. */
export function validateRadioViewModel(value: unknown): RadioViewModel {
  const v = record(value, '$');
  exactKeys(v, [
    'topologyId', 'vfoScheme', 'activeReceiver', 'vfos', 'split', 'dualWatch',
    'txTarget', 'txPermit', 'scope', 'disabledReasons', 'txAux', 'meters', 'rxAudio', 'modeFilter',
    'filterPassband',
  ], '$');
  if (!Array.isArray(v.vfos)) invalid('$.vfos', 'an array');
  if (!Array.isArray(v.disabledReasons)) invalid('$.disabledReasons', 'an array');
  const scope = record(v.scope, '$.scope');
  exactKeys(scope, ['hardwareScope', 'audioFftScope'], '$.scope');

  const vfos = v.vfos.map((vfo, i) => validateVfo(vfo, `$.vfos[${i}]`));
  const txTarget = validateTxTarget(v.txTarget, '$.txTarget');
  const txPermit = validateTxPermit(v.txPermit, '$.txPermit');

  if (txPermit.status === 'allowed' && txTarget.status === 'unknown') {
    invalid('$.txPermit', "'allowed' only when txTarget is known (fail-open otherwise)");
  }
  vfos.forEach((vfo, i) => {
    const matches = txTarget.status === 'known'
      && vfo.receiver === txTarget.receiver && slotEqual(vfo.slot, txTarget.slot);
    if (vfo.isTxTarget && !matches) {
      invalid(`$.vfos[${i}].isTxTarget`, 'true only on the VFO matching a known txTarget');
    }
  });

  // Conditional spread, not `txAux: optionalGroup(...)` directly: an absent
  // group must OMIT the key, not merely set it to `undefined` — a plain
  // property assignment still shows up in `Object.keys()`, which would make
  // "structurally unavailable" indistinguishable from "present" to any
  // consumer that inventories keys (see the adapter test's exact-key-list
  // assertion this was verified against).
  const txAux = optionalGroup(v.txAux, '$.txAux', validateTxAux);
  const meters = optionalGroup(v.meters, '$.meters', validateMeters);
  const rxAudio = optionalGroup(v.rxAudio, '$.rxAudio', validateRxAudio);
  const modeFilter = optionalGroup(v.modeFilter, '$.modeFilter', validateModeFilter);
  const filterPassband = optionalGroup(v.filterPassband, '$.filterPassband', validateFilterPassband);

  return {
    topologyId: str(v.topologyId, '$.topologyId'),
    vfoScheme: oneOf(v.vfoScheme, VFO_SCHEMES, '$.vfoScheme'),
    activeReceiver: validateActiveRx(v.activeReceiver, '$.activeReceiver'),
    vfos,
    split: validateBooleanFact(v.split, '$.split'),
    dualWatch: validateBooleanFact(v.dualWatch, '$.dualWatch'),
    txTarget,
    txPermit,
    scope: {
      hardwareScope: validateAvailability(scope.hardwareScope, '$.scope.hardwareScope'),
      audioFftScope: validateAvailability(scope.audioFftScope, '$.scope.audioFftScope'),
    },
    disabledReasons: v.disabledReasons.map((r, i) => validateDisabledReason(r, `$.disabledReasons[${i}]`)),
    ...(txAux !== undefined ? { txAux } : {}),
    ...(meters !== undefined ? { meters } : {}),
    ...(rxAudio !== undefined ? { rxAudio } : {}),
    ...(modeFilter !== undefined ? { modeFilter } : {}),
    ...(filterPassband !== undefined ? { filterPassband } : {}),
  };
}
