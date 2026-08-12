/**
 * Radio view-model adapter — the live seam behind the semantic VFO and RX/TX
 * surfaces (MOR-1065).
 *
 * Maps REAL runtime state + capabilities onto the MOR-1062 radio semantic
 * view-model shape. Unknown-preservation is the whole job: an unobserved
 * active receiver, A/B slot, split, dual-watch or TX target must reach the
 * surfaces as the contract's explicit `unknown` branch — never as a
 * fabricated `'MAIN'` / `'A'` / `false` (MOR-988 §3.2, §4, §11.3). Every
 * radio fact below is gated on the backend's own field status, so an old or
 * partially-observed server degrades to `unknown`/disabled rather than to a
 * guessed default.
 *
 * The contract type is imported TYPE-ONLY. eslint invariant 1 (MOR-1061 F2)
 * bans `lib/runtime/** -> semantic/**`, but the v3 ADR's own dependency
 * diagram puts the view model on the ADAPTER side of that seam, so the
 * adapters zone carries a narrow, recorded `allowTypeImports` exception (see
 * `eslint.config.js`; MOR-1065 review ruling 2). Value imports from
 * `semantic/` stay blocked — pinned by `architecture-boundaries.test.ts`.
 * Annotating the return type here is what makes contract drift a compile
 * error at the PRODUCER (and, because the returned object literal is checked
 * against the annotation, an extra field is an error too);
 * `__tests__/radio-view-model-adapter.test.ts` additionally runs every
 * emitted model through the real `validateRadioViewModel`.
 */
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type {
  RadioViewModel, TxAuxField, TxAuxViewModel, AtuStatus,
  MeterField, MeterRfState, MetersViewModel,
  AudioFocus, MonitorMode, RxAudioViewModel, ModeFilterViewModel,
  FilterPassbandViewModel, DspViewModel, RfFrontEndViewModel,
  BandChoice, BandViewModel, RitXitViewModel, AntennaViewModel, ScanViewModel,
  BreakInMode, CwKeyerViewModel, ScopeControlsViewModel,
  ScopeDisplayViewModel, ScopeSourceKind, ScopeHealthState,
} from '../../../semantic/radio-view-model';
import type { TxAuthoritySnapshot } from '../../../semantic/rx-tx-surface';
import { isFieldAvailable } from '$lib/state/field-status';
import { modInputStateKey } from '$lib/radio/mod-input';
import { flattenBands, findActiveBand } from '$lib/radio/band-plan';
import { getFrequencyPermit, type FrequencyPermit, type TxPermit } from '$lib/utils/tx-permit';
import {
  relativeVfoIdentityUnknown,
  resolveFilterModeConfig,
} from '$lib/runtime/props/panel-props';
import {
  deriveIfShift, pbtRangeFromCaps, pbtRawToHz,
  controlRangeFromCapsOrDefault, nrRawToDisplay, nbDepthRawToDisplay,
} from '$lib/radio/filter-controls';
import {
  derivePresentationCapabilities, type ReceiverId, type VfoSlotId,
} from './presentation-capabilities';
import {
  deriveTxCapabilities, type ModInputSource, type TxCapabilityFacts,
} from './tx-capabilities';
// MOR-1312 (12A verify N1): the REAL type, not a hand-copied literal union —
// `ScopeDisplaySnapshot.lifecycle` below now widens automatically if
// `ResourceHealth` ever gains a value upstream, instead of silently staying
// narrower than the runtime status it mirrors. `ConnectionState` (the sibling
// type N1 also names) stays a hand-copied literal deliberately: it lives in
// `$lib/transport/ws-client`, and this file's own purity guard
// (`__tests__/rx-audio-purity.isolated.test.ts` pin 3 / SAFETY CONSTRAINT 1)
// asserts NO import from `$lib/transport/*` anywhere in this adapter, type-only
// or not — that guard is more binding than N1's suggestion, so `.transport`
// keeps its inline union and only `.lifecycle` gets the real type.
import type { ResourceHealth } from '$lib/runtime/resource-demand';

type Slot =
  | { kind: 'slotted'; id: VfoSlotId }
  | { kind: 'relative'; role: 'selected' | 'unselected' }
  | { kind: 'unslotted' }
  | { kind: 'unknown' };
type Fact = { status: 'known'; value: boolean } | { status: 'unknown' };
type Reason = {
  field: string;
  code: 'capability-unavailable' | 'field-not-observed' | 'tx-target-unknown' | 'out-of-band'
    | 'mutually-exclusive-control';
};
type Readable = {
  freqHz?: number; mode?: string; filter?: number | null; filterNum?: number | null;
};
type Position = { slot: Slot; base: string; filterKey: 'filter' | 'filterNum'; src: Readable | null };

const RECEIVER_KEY = { MAIN: 'main', SUB: 'sub' } as const;
const SLOT_KEY = { A: 'vfoA', B: 'vfoB' } as const;

/** Observed + fresh + available — the same three-part gate the TX authority uses. */
function seen(state: ServerState | null, path: string): boolean {
  const status = state?.fieldStatus?.[path];
  return status?.observed === true && status.freshness === 'fresh'
    && status.availability === 'available';
}

/**
 * THE active-receiver identity — `seen()` plus a positively recognised
 * MAIN/SUB reading. Extracted (MOR-1356) so `toRadioViewModel`'s
 * `activeReceiver` fact and `deriveBand`'s receiver-scoped TX permit share
 * ONE criterion rather than two that can drift apart: the model must not be
 * able to say "I don't know which receiver is active" and, in the same
 * payload, hand out a TX permit scoped to the receiver it just guessed.
 *
 * MOR-1421: `singleReceiverTopology` — `topology.structuralCount === 1` from
 * the caller's own `derivePresentationCapabilities` call — makes this
 * capability-aware. On a single-receiver radio (MAIN-only topology, e.g. the
 * IC-7300) `active` is STRUCTURALLY always MAIN: there is no second receiver
 * for it to name, so a backend that never confirms the field (`active` reads
 * observed:false forever on that class of radio) leaves the fact `unknown`
 * for a reason that carries no information — "which receiver" was never an
 * open question. Returning `'MAIN'` here is a TAUTOLOGY the capabilities
 * already prove, not the fabricated-default guess MOR-988 §3.2 forbids
 * (guessing picks among live possibilities; this is the only possibility).
 * Multi-receiver radios (`singleReceiverTopology: false`, including an
 * unrecognised/contradictory caps shape) keep the original three-part
 * `seen()` gate byte-for-byte — there the identity is genuinely unresolved
 * without a confirmed reading.
 */
function activeReceiverId(
  state: ServerState | null, singleReceiverTopology: boolean,
): ReceiverId | null {
  if (singleReceiverTopology) return 'MAIN';
  const active = state?.active;
  return seen(state, 'active') && (active === 'MAIN' || active === 'SUB') ? active : null;
}

function boolFact(state: ServerState | null, path: string, value: unknown): Fact {
  return seen(state, path) && typeof value === 'boolean'
    ? { status: 'known', value }
    : { status: 'unknown' };
}

/** Per-position readings; each degrades to `null` on its own, unobserved field. */
function readings(
  state: ServerState | null, base: string, filterKey: 'filter' | 'filterNum', src: Readable | null,
): { frequencyHz: number | null; mode: string | null; filter: string | null } {
  const hz = src?.freqHz;
  const mode = src?.mode;
  const filter = filterKey === 'filter' ? src?.filter : src?.filterNum;
  return {
    frequencyHz: seen(state, `${base}freqHz`) && typeof hz === 'number' && Number.isFinite(hz)
      ? hz : null,
    mode: seen(state, `${base}mode`) && typeof mode === 'string' && mode !== '' ? mode : null,
    filter: seen(state, `${base}${filterKey}`) && typeof filter === 'number'
      ? `FIL${filter}` : null,
  };
}

const sameSlot = (a: Slot, b: Slot): boolean =>
  a.kind === 'slotted' && b.kind === 'slotted' ? a.id === b.id
    : a.kind === 'relative' && b.kind === 'relative' ? a.role === b.role
      : a.kind === b.kind;

function hasCap(caps: Capabilities | null, name: string): boolean {
  return caps?.capabilities?.includes(name) ?? false;
}

/**
 * MOR-1244: the SAME field-status gate `toTxProps`/`toVoxProps` use for these
 * exact controls (`$lib/runtime/props/panel-props.ts`,
 * `components-v2/wiring/state-adapter.ts`) — deliberately the looser
 * "not proven missing/stale" gate (`isFieldAvailable`, defaults to available
 * absent an explicit field-status entry), not this file's own stricter
 * `seen()` three-part gate used for VFO/TX-target identity. `txAux` controls
 * are legacy top-level fields with the same v2 availability story as the
 * panel they came from; giving them a stricter gate here would silently
 * disable controls v2 has always shown.
 */
function topFieldAvailable(state: ServerState | null, field: string): boolean {
  return isFieldAvailable(state, field);
}

/** `fieldFresh` is the raw `topFieldAvailable` read; a structurally-absent
 *  control must never report a "known" reading even if the (irrelevant)
 *  field happens to look fresh — so the reading gates on BOTH, same as
 *  `availability.operational`. */
function txAuxField<T>(structural: boolean, fieldFresh: boolean, value: T | undefined): TxAuxField<T> {
  const operational = structural && fieldFresh;
  return {
    reading: operational && value !== undefined ? { status: 'known', value } : { status: 'unknown' },
    availability: { structural, operational },
  };
}
const numOrUndef = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);
const boolOrUndef = (v: unknown): boolean | undefined => (typeof v === 'boolean' ? v : undefined);
const atuStatus = (v: unknown): AtuStatus | undefined =>
  v === 0 ? 'off' : v === 1 ? 'on' : v === 2 ? 'tuning' : undefined;

/**
 * N3: emits the group ONLY on positive evidence — "capability/observed
 * fields", verbatim. `caps.tx` alone is NOT sufficient: a radio can declare
 * generic TX capability (e.g. for the RX/TX authority, MOR-1064) without
 * this adapter having ANY reason to believe a single one of the twelve
 * txAux controls exists — no sub-capability tag AND no field ever observed.
 * Emitting a group there would be exactly the all-unknowns placeholder N3
 * forbids. So the gate is a real OR: at least one txAux-specific capability
 * tag (`vox`/`compressor`/`monitor`/`tuner`/`drive_gain` — confirmed against
 * both `toTxProps` and `AmberCockpit.svelte`'s
 * `hasCap('vox' | 'compressor' | 'tuner')` usage) OR at least one of the
 * twelve raw state fields actually present (any live radio that has ever
 * reported ptt/power/mic-gain telemetry clears this — RF power and mic gain
 * carry no separate capability tag in v2 either, they are core TX facts).
 * Once evidence exists, `hasCap(caps, 'tx')` remains required too — it is
 * `toTxProps`'s own `hasTx` gate for the panel as a whole.
 */
function deriveTxAux(state: ServerState | null, caps: Capabilities | null): TxAuxViewModel | undefined {
  if (!hasCap(caps, 'tx')) return undefined;
  const hasTuner = hasCap(caps, 'tuner');
  const hasVox = hasCap(caps, 'vox');
  const hasCompressor = hasCap(caps, 'compressor');
  const hasMonitor = hasCap(caps, 'monitor');
  const hasDriveGain = hasCap(caps, 'drive_gain');
  const rawValues = [
    state?.tunerStatus, state?.voxOn, state?.voxGain, state?.antiVoxGain, state?.voxDelay,
    state?.compressorOn, state?.compressorLevel, state?.monitorOn, state?.monitorGain,
    state?.powerLevel, state?.micGain, state?.driveGain,
  ];
  const hasEvidence = hasTuner || hasVox || hasCompressor || hasMonitor || hasDriveGain
    || rawValues.some((v) => v !== undefined);
  if (!hasEvidence) return undefined;
  return {
    atu: txAuxField(hasTuner, topFieldAvailable(state, 'tunerStatus'), atuStatus(state?.tunerStatus)),
    vox: txAuxField(hasVox, topFieldAvailable(state, 'voxOn'), boolOrUndef(state?.voxOn)),
    voxGain: txAuxField(hasVox, topFieldAvailable(state, 'voxGain'), numOrUndef(state?.voxGain)),
    antiVoxGain: txAuxField(hasVox, topFieldAvailable(state, 'antiVoxGain'), numOrUndef(state?.antiVoxGain)),
    voxDelay: txAuxField(hasVox, topFieldAvailable(state, 'voxDelay'), numOrUndef(state?.voxDelay)),
    compressor: txAuxField(hasCompressor, topFieldAvailable(state, 'compressorOn'), boolOrUndef(state?.compressorOn)),
    compressorLevel: txAuxField(
      hasCompressor, topFieldAvailable(state, 'compressorLevel'), numOrUndef(state?.compressorLevel),
    ),
    monitor: txAuxField(hasMonitor, topFieldAvailable(state, 'monitorOn'), boolOrUndef(state?.monitorOn)),
    monitorLevel: txAuxField(hasMonitor, topFieldAvailable(state, 'monitorGain'), numOrUndef(state?.monitorGain)),
    rfPower: txAuxField(true, topFieldAvailable(state, 'powerLevel'), numOrUndef(state?.powerLevel)),
    micGain: txAuxField(true, topFieldAvailable(state, 'micGain'), numOrUndef(state?.micGain)),
    driveGain: txAuxField(hasDriveGain, topFieldAvailable(state, 'driveGain'), numOrUndef(state?.driveGain)),
  };
}

/**
 * The subset of the App TX authority the meter facts read (MOR-1262 slice 2A).
 * `Pick<>` of the RX/TX surface's own snapshot type — one authority vocabulary
 * for the whole v3 contract layer, already parity-pinned against the real
 * reducer in `semantic/__tests__/rx-tx-authority-parity.test.ts`, so the
 * controller's deep-readonly `snapshot()` is assignable with no adaptation.
 */
export type MetersTxAuthority = Pick<TxAuthoritySnapshot, 'radioTx' | 'txRisk'>;

/**
 * SAFETY INVARIANT R9. TX truth comes from the App TX authority and from
 * nowhere else — never from `state.ptt`, which is a command/readback echo
 * that can read RX while the key is still down (the AppGlobalHost lamp's own
 * reasoning, MOR-1059) and whose use for meter chrome is the open MOR-1235
 * disagreement this slice closes.
 *
 * The body is a byte-identical copy of `rx-tx-surface.ts::rfState` — eslint
 * invariant 1 permits this file only TYPE imports from `semantic/`, so the
 * shared predicate cannot be called from here. Agreement with the real
 * function, across every state the real reducer can reach, is pinned in
 * `semantic/__tests__/meters.test.ts`; drift there is a red test, not a
 * silent fork.
 */
function meterRfState(tx: MetersTxAuthority): MeterRfState {
  if (tx.radioTx === 'on' || tx.txRisk === 'confirmed-on') return 'transmitting';
  if (tx.txRisk === 'uncertain') return 'uncertain';
  return tx.radioTx === 'off' && tx.txRisk === 'none' ? 'receiving' : 'unknown';
}

function meterField(structural: boolean, fieldFresh: boolean, raw: unknown, relevant: boolean): MeterField {
  const operational = structural && fieldFresh;
  const value = numOrUndef(raw);
  return {
    reading: operational && value !== undefined ? { status: 'known', value } : { status: 'unknown' },
    availability: { structural, operational },
    relevant,
  };
}

/**
 * Emits the group only on positive evidence, same discipline as `deriveTxAux`
 * (N3) with two additions:
 *
 *  - NO authority snapshot ⇒ NO group. Without the App TX authority there is
 *    no honest TX relevance to state, and inventing one from `state.ptt` is
 *    exactly what R9 forbids; a caller that has not wired the controller gets
 *    a structurally-absent family, not a guess.
 *  - `raw !== undefined` IS the shipped capability gate for meters. There is
 *    no per-meter capability tag anywhere in v2 — `MetersDockPanel.svelte`'s
 *    own doc comment says "capability gating by `!== undefined`" — so that
 *    gate is copied rather than replaced. TX meters additionally require the
 *    radio to be able to transmit at all (`caps.tx`, `toMeterProps`'s `hasTx`).
 *
 * Relevance fails CLOSED: TX meters read as relevant in every state that is
 * not a positively observed RX, so an 'uncertain'/'unknown' window keeps the
 * SWR and ALC fault meters live rather than greying them out mid-transmission.
 */
function deriveMeters(
  state: ServerState | null, caps: Capabilities | null, tx: MetersTxAuthority | null | undefined,
): MetersViewModel | undefined {
  if (!tx || !state) return undefined;
  const rfState = meterRfState(tx);
  const onTx = rfState !== 'receiving';
  const hasTx = caps?.tx ?? false;
  // Mirrors the shipped dock's own active-receiver read (`RadioLayout.svelte`):
  // the S-meter follows the receiver the operator is listening to.
  const onSub = state.active === 'SUB';
  const rx = onSub ? state.sub : state.main;
  const sPath = onSub ? 'sub.sMeter' : 'main.sMeter';
  const { powerMeter, swrMeter, alcMeter, compMeter, vdMeter, idMeter } = state;
  const raws = [rx?.sMeter, powerMeter, swrMeter, alcMeter, compMeter, vdMeter, idMeter];
  if (!raws.some((v) => v !== undefined)) return undefined;
  const txMeter = (raw: unknown, path: string): MeterField =>
    meterField(hasTx && raw !== undefined, topFieldAvailable(state, path), raw, onTx);
  return {
    rfState,
    signal: meterField(rx?.sMeter !== undefined, topFieldAvailable(state, sPath), rx?.sMeter, !onTx),
    power: txMeter(powerMeter, 'powerMeter'),
    swr: txMeter(swrMeter, 'swrMeter'),
    alc: txMeter(alcMeter, 'alcMeter'),
    compression: txMeter(compMeter, 'compMeter'),
    // Vd is the station's supply rail, not a TX reading: it is worth showing
    // in every RF state (the dock keeps it on instantaneous display for the
    // same reason), so it is structurally gated but never relevance-gated.
    drainVoltage: meterField(vdMeter !== undefined, topFieldAvailable(state, 'vdMeter'), vdMeter, true),
    drainCurrent: txMeter(idMeter, 'idMeter'),
  };
}

/**
 * Mode/filter facts (MOR-1262 decomposition slice 4A, MOR-1280): current
 * mode, capability-derived mode choice set, current filter selection,
 * capability-derived filter choice set, filter width and its min/max bounds.
 *
 * Evidence gate (N3): unlike `deriveTxAux`'s optional per-control fields,
 * `ReceiverStatePublic.mode`/`.filter` are REQUIRED fields — always present
 * once any receiver exists — so "a raw field was observed" carries no
 * evidence here (it would fire for every radio ever built). The only honest
 * signal is the capability-declared choice set: no declared modes AND no
 * declared filters is NO group, never an all-unknowns placeholder (pinned
 * against `radio-view-model-adapter.test.ts`'s own `modes: [], filters: []`
 * baseline fixtures, which must keep emitting no `modeFilter`).
 *
 * `resolveFilterModeConfig` moves BEHIND this adapter (the decomposition's
 * explicit instruction for this slice): `filterWidthMin`/`filterWidthMax`
 * read the shipped resolver's own per-mode table lookup — the same function
 * `toFilterProps`/`toScopeControlsProps` call directly today — rather than
 * re-deriving a width table here. v2's own call sites are untouched; this is
 * an additional consumer, not a migration (that is slice 4B).
 */
function deriveModeFilter(
  state: ServerState | null, caps: Capabilities | null,
): ModeFilterViewModel | undefined {
  if (!caps) return undefined;
  const modeChoices = caps.modes ?? [];
  const filterChoices = caps.filters ?? [];
  if (modeChoices.length === 0 && filterChoices.length === 0) return undefined;
  const onSub = state?.active === 'SUB';
  const rx = onSub ? state?.sub : state?.main;
  const base = onSub ? 'sub.' : 'main.';
  const hasModes = modeChoices.length > 0;
  const hasFilters = filterChoices.length > 0;
  const modeObserved = topFieldAvailable(state, `${base}mode`);
  const filterObserved = topFieldAvailable(state, `${base}filter`);
  const widthObserved = topFieldAvailable(state, `${base}filterWidth`);
  // The active mode-keyed filter config, resolved by the ONE shipped
  // derivation (`resolveFilterModeConfig`) — see the doc comment above.
  const filterConfig = resolveFilterModeConfig(caps, rx?.mode, rx?.dataMode);
  const widthMin = filterConfig?.minHz ?? filterConfig?.table?.[0] ?? caps.filterWidthMin;
  const widthMax = filterConfig?.maxHz
    ?? (filterConfig?.table?.length ? filterConfig.table[filterConfig.table.length - 1] : undefined)
    ?? caps.filterWidthMax;
  return {
    currentMode: txAuxField(hasModes, modeObserved, rx?.mode),
    modeChoices,
    currentFilter: txAuxField(hasFilters, filterObserved, numOrUndef(rx?.filter ?? undefined)),
    filterChoices,
    filterWidth: txAuxField(hasFilters, widthObserved, numOrUndef(rx?.filterWidth ?? undefined)),
    // F2 fix (verify round 1): the bounds' VALUE comes from
    // `resolveFilterModeConfig(caps, rx?.mode, rx?.dataMode)` — caps + MODE,
    // never from `filterWidth` — so their operational gate must be
    // `modeObserved`, not `widthObserved`. Gating on `widthObserved`
    // published mode-derived bounds as confirmed while `currentMode` itself
    // read unknown (fabrication), and withheld derivable bounds whenever a
    // width readback simply hadn't arrived yet (false negative).
    filterWidthMin: txAuxField(hasFilters, modeObserved, numOrUndef(widthMin)),
    filterWidthMax: txAuxField(hasFilters, modeObserved, numOrUndef(widthMax)),
  };
}

/**
 * Filter-passband facts (MOR-1262 decomposition slice 4A′, MOR-1284): filter
 * shape, IF-shift, PBT inner/outer, DATA-mode. A separate group from
 * `deriveModeFilter` — see `FilterPassbandViewModel`'s doc comment.
 *
 * Evidence gate, per field, following the same "capability-based where the
 * raw field is required-and-signal-free; observed-based operational gate
 * otherwise" split 4A used:
 *  - `dataMode` is a REQUIRED field (`ReceiverStatePublic.dataMode: number`,
 *    always present) — same story as 4A's `mode`/`filter`: structural is
 *    `hasCap(caps, 'data_mode')` (`toModeProps`'s own `hasDataMode` gate),
 *    never "was it observed".
 *  - `filterShape` is OPTIONAL and undeclared by any capability tag of its
 *    own; it is part of the same filter subsystem 4A's `filterWidth` is, so
 *    its structural gate is the SAME `hasFilters` signal that field uses.
 *  - `pbtInner`/`pbtOuter` are OPTIONAL, gated on `hasCap(caps, 'pbt')` AND a
 *    usable `pbt_inner` range declared by THIS `caps` argument itself
 *    (`pbtRangeFromCaps`, MOR-1291) — `toFilterProps`'s own `hasPbt`
 *    capability check alone is not enough here: unlike the v2 `panel-props.ts`
 *    path, this fact layer never falls back to a plausible IC-7610-shaped
 *    default (rawCenter 128 / ±1200 Hz) when a radio's own capabilities omit
 *    the range. A `pbt`-capable radio with no declared scale is exposed as
 *    structurally absent, not as a confidently-known reading sourced from
 *    module-global store state.
 *  - `ifShift` mirrors `toFilterProps`'s own conditional BYTE-FOR-BYTE: a
 *    radio with the `if_shift` capability reports its own raw field; one
 *    without it, but WITH `pbt` AND a usable PBT range, gets
 *    `deriveIfShift(pbtInner, pbtOuter)` — the ONE shipped fallback, not a
 *    re-derivation. Structural is therefore the OR of both paths (same "real
 *    OR, not a stand-in for AND" discipline `deriveRxAudio`'s `hasAfLevel`
 *    uses), with the derived side additionally requiring the PBT range
 *    (MOR-1291, same reasoning as `pbtInner`/`pbtOuter` above — there is no
 *    scale to derive an Hz value with otherwise); operational for the
 *    derived path requires BOTH pbtInner AND pbtOuter to be honestly
 *    observed — deriving from one observed and one silently-defaulted-to-128
 *    input is exactly the fabrication `deriveModeFilter`'s F2 fix forbids, so
 *    neither pbtInner nor pbtOuter nor ifShift ever computes over the OTHER
 *    field's ` ?? 128` fallback the way `toFilterProps` does.
 */
function deriveFilterPassband(
  state: ServerState | null, caps: Capabilities | null,
): FilterPassbandViewModel | undefined {
  if (!caps) return undefined;
  const hasFilters = (caps.filters ?? []).length > 0;
  const hasPbtCap = hasCap(caps, 'pbt');
  const hasIfShiftCap = hasCap(caps, 'if_shift');
  const hasDataModeCap = hasCap(caps, 'data_mode');
  if (!hasFilters && !hasPbtCap && !hasIfShiftCap && !hasDataModeCap) return undefined;

  const onSub = state?.active === 'SUB';
  const rx = onSub ? state?.sub : state?.main;
  const base = onSub ? 'sub.' : 'main.';

  const filterShapeObserved = topFieldAvailable(state, `${base}filterShape`);
  const dataModeObserved = topFieldAvailable(state, `${base}dataMode`);
  const pbtInnerObserved = topFieldAvailable(state, `${base}pbtInner`);
  const pbtOuterObserved = topFieldAvailable(state, `${base}pbtOuter`);
  const ifShiftRawObserved = topFieldAvailable(state, `${base}ifShift`);

  // The ONE shipped PBT raw->Hz conversion (`pbtRawToHz`, `$lib/radio/filter-
  // controls`) — called with the range explicitly derived from THIS
  // function's own `caps` argument (`pbtRangeFromCaps`, MOR-1284 F1), not the
  // capabilities STORE singleton `pbtRawToHz` otherwise falls back to. A
  // store-sourced scale would make these facts a function of module-global
  // state rather than of `(state, caps)` — identical arguments could yield
  // different facts across store writes, and worse, a radio whose OWN caps
  // already declare a non-default scale would read a confidently-wrong
  // `{known}` value from an unrelated (e.g. still-unpopulated) store. See the
  // doc comment above and the parity pin in
  // `__tests__/filter-passband-adapter.isolated.test.ts`. Computed only from the
  // field's OWN raw value — never from a `?? 128` stand-in — so an unobserved
  // pbtInner/pbtOuter never silently seeds a derived ifShift.
  //
  // MOR-1291: `pbtScale` is used ONLY when it resolves to a CONCRETE range.
  // Unlike `controlRangeFromCapsOrDefault`'s `nr_level`/`nb_depth` story
  // below, PBT has no per-radio-model default worth falling back to — the
  // IC-7610-shaped `{rawCenter:128, displayMin:-1200, displayMax:1200}`
  // `pbtRawToHz`/`pbtHzToRaw` fall back to (via their own store lookup, when
  // called with NO `range` argument) is exactly the fabrication this ticket
  // closes: a caps object that declares the `pbt` capability but omits its
  // OWN `controls.pbt_inner` range is treated as an honest "this radio's PBT
  // scale is unknown", never silently coerced to a plausible-looking IC-7610
  // reading sourced from module-global store state. `pbtRawToHz` is
  // therefore never invoked with `pbtScale` absent — the store-fallback
  // branch inside it exists only for the unrelated legacy `panel-props.ts`
  // v2 call sites that still call it with no `range` argument at all.
  const pbtScale = pbtRangeFromCaps(caps);
  const hasPbtRange = pbtScale !== undefined;
  const pbtInnerRaw = numOrUndef(rx?.pbtInner);
  const pbtOuterRaw = numOrUndef(rx?.pbtOuter);
  const pbtInnerHz = pbtScale && pbtInnerRaw !== undefined ? pbtRawToHz(pbtInnerRaw, pbtScale) : undefined;
  const pbtOuterHz = pbtScale && pbtOuterRaw !== undefined ? pbtRawToHz(pbtOuterRaw, pbtScale) : undefined;

  // `hasPbtRange` gates the DERIVED path the same way `hasPbtCap` alone used
  // to: a radio that declares `pbt` but no usable `pbt_inner` range can never
  // actually produce a PBT-derived ifShift Hz value (there is no scale to
  // convert with), so claiming `structural: true` there would promise a
  // reading that can never arrive. `hasIfShiftCap`'s own branch is untouched
  // — a REAL if_shift command needs no PBT scale at all.
  const ifShiftStructural = hasIfShiftCap || (hasPbtCap && hasPbtRange);
  const ifShiftOperational = hasIfShiftCap
    ? ifShiftRawObserved
    : (pbtInnerObserved && pbtOuterObserved);
  const ifShiftValue = hasIfShiftCap
    ? numOrUndef(rx?.ifShift)
    : (pbtInnerHz !== undefined && pbtOuterHz !== undefined
      ? deriveIfShift(pbtInnerHz, pbtOuterHz)
      : undefined);

  return {
    filterShape: txAuxField(hasFilters, filterShapeObserved, numOrUndef(rx?.filterShape)),
    ifShift: txAuxField(ifShiftStructural, ifShiftOperational, ifShiftValue),
    // MOR-1494 review round. Deliberately NOT `ifShiftStructural` above.
    // `ifShiftStructural`/`ifShiftOperational`/`ifShiftValue` stay exactly as
    // they were — a radio with `pbt` but no `if_shift` still gets an honest
    // derived `ifShift` READING (`scope-adapter.ts`'s `toSpectrumAuthority`
    // reads `filterPassband.ifShift` for the passband-center overlay on
    // EVERY radio that has PBT, IC-7300 included, and must keep doing so).
    // This flag answers a DIFFERENT question — does the radio have a REAL
    // `if_shift` COMMAND of its own — for `FilterSurface.svelte` to decide
    // whether to show the IF-shift CONTROL at all. IC-7300 (PBT-only) has no
    // such command; showing the control permanently disabled with a
    // PBT-derived stand-in is a dead control, not a usable one (the owner's
    // MOR-1494 ruling: hide capability-absent controls, don't show them
    // dead). See `FilterPassbandViewModel.ifShiftControlStructural`'s doc
    // comment (`radio-view-model.ts`) for the full split.
    ifShiftControlStructural: hasIfShiftCap,
    // MOR-1291: structural requires BOTH the `pbt` capability tag AND a
    // usable `pbt_inner` range from THIS caps argument — a radio that
    // declares the capability but omits (or malforms) its own range is
    // exposed as structurally absent (unavailable), same "hide, don't show
    // dead" doctrine `ifShiftControlStructural` above already established,
    // never a plausible IC-7610-shaped reading manufactured from a
    // module-global store fallback (see the `pbtScale` doc comment above).
    pbtInner: txAuxField(hasPbtCap && hasPbtRange, pbtInnerObserved, pbtInnerHz),
    pbtOuter: txAuxField(hasPbtCap && hasPbtRange, pbtOuterObserved, pbtOuterHz),
    dataMode: txAuxField(hasDataModeCap, dataModeObserved, numOrUndef(rx?.dataMode)),
  };
}

/**
 * DSP facts (MOR-1262 decomposition slice 5A, MOR-1290): NR, NB (+ depth/
 * width), notch (auto/manual), AGC. A separate group from `filterPassband`
 * — family enumeration is explicit and CLOSED (filter shape / IF-shift / PBT
 * are family 4, `deriveFilterPassband` above; never duplicated here).
 *
 * Evidence gate (N3): each field's structural gate mirrors `toDspProps`'/
 * `toAgcProps`' OWN gate verbatim — `hasCap(caps, 'nr'|'nb'|'notch'|'agc')`,
 * or (for `nbDepth`/`nbWidth`) the presence of a `controls.nb_depth` range,
 * exactly `toDspProps`' own `hasNbDepth`/`hasNbWidth` (`hasNbWidth` borrows
 * `hasNbDepth`'s signal verbatim, same as that function does). The group
 * itself emits only when at least one of these signals is positive.
 */
function deriveDsp(
  state: ServerState | null, caps: Capabilities | null,
): DspViewModel | undefined {
  if (!caps) return undefined;
  const hasNrCap = hasCap(caps, 'nr');
  const hasNbCap = hasCap(caps, 'nb');
  const hasNotchCap = hasCap(caps, 'notch');
  const hasAgcCap = hasCap(caps, 'agc');
  const hasNbDepth = (caps.controls?.nb_depth ?? null) !== null;
  if (!hasNrCap && !hasNbCap && !hasNotchCap && !hasAgcCap && !hasNbDepth) return undefined;

  const onSub = state?.active === 'SUB';
  const rx = onSub ? state?.sub : state?.main;
  const base = onSub ? 'sub.' : 'main.';

  // The ONE shipped display-scale conversions (`nrRawToDisplay`/
  // `nbDepthRawToDisplay`, `$lib/radio/filter-controls`) — called with the
  // range explicitly derived from THIS function's own `caps` argument
  // (`controlRangeFromCapsOrDefault`, mirroring `filterPassband`'s
  // `pbtRangeFromCaps`, MOR-1284 F1), not the capabilities STORE singleton
  // they otherwise fall back to. MOR-1290 F1 (verify round 1): the plain
  // `controlRangeFromCaps` alone still returns `undefined` when `caps`
  // declares no range for the key, and passing `undefined` as `range` makes
  // `nrRawToDisplay`/`nbDepthRawToDisplay` fall through to THEIR OWN store
  // lookup — reintroducing the exact module-global dependency this is meant
  // to close, for the one case (`caps` omitting the key) that matters most
  // (a server whose capabilities haven't fully landed yet). The `…OrDefault`
  // wrapper always returns a CONCRETE range — this module's own
  // `CONTROL_DEFAULTS`, the same ones `nrRawToDisplay`/`nbDepthRawToDisplay`
  // fall back to today — so the store is NEVER consulted here, in either
  // branch: the fact is a pure function of `(state, caps)` unconditionally.
  // See the determinism pin in `__tests__/dsp-adapter.isolated.test.ts`. Computed only
  // from the field's OWN raw value — never from a `?? 0` stand-in — so an
  // unobserved nrLevel/nbDepth never silently reports a fabricated reading.
  const nrLevelRange = controlRangeFromCapsOrDefault('nr_level', caps);
  const nrLevelRaw = numOrUndef(rx?.nrLevel);
  const nrLevelValue = nrLevelRaw !== undefined ? nrRawToDisplay(nrLevelRaw, nrLevelRange) : undefined;

  const nbDepthRange = controlRangeFromCapsOrDefault('nb_depth', caps);
  const nbDepthRaw = numOrUndef(state?.nbDepth);
  const nbDepthValue = nbDepthRaw !== undefined ? nbDepthRawToDisplay(nbDepthRaw, nbDepthRange) : undefined;

  // `notchMode` is derived from TWO raw booleans (`autoNotch`/`manualNotch`);
  // same "never derive from a half-observed input" discipline `filterPassband`'s
  // `ifShift` uses for `pbtInner`/`pbtOuter` (MOR-1284 F2 lesson) — both must
  // be honestly observed before a definitive 'off'/'auto'/'manual' is reported.
  const autoNotchObserved = topFieldAvailable(state, `${base}autoNotch`);
  const manualNotchObserved = topFieldAvailable(state, `${base}manualNotch`);
  const autoNotchRaw = boolOrUndef(rx?.autoNotch);
  const manualNotchRaw = boolOrUndef(rx?.manualNotch);
  const notchModeValue = autoNotchRaw !== undefined && manualNotchRaw !== undefined
    ? (autoNotchRaw ? 'auto' as const : manualNotchRaw ? 'manual' as const : 'off' as const)
    : undefined;

  return {
    nrActive: txAuxField(hasNrCap, topFieldAvailable(state, `${base}nr`), boolOrUndef(rx?.nr)),
    nrLevel: txAuxField(hasNrCap, topFieldAvailable(state, `${base}nrLevel`), nrLevelValue),
    nbActive: txAuxField(hasNbCap, topFieldAvailable(state, `${base}nb`), boolOrUndef(rx?.nb)),
    nbLevel: txAuxField(hasNbCap, topFieldAvailable(state, `${base}nbLevel`), numOrUndef(rx?.nbLevel)),
    nbDepth: txAuxField(hasNbDepth, topFieldAvailable(state, 'nbDepth'), nbDepthValue),
    nbWidth: txAuxField(hasNbDepth, topFieldAvailable(state, 'nbWidth'), numOrUndef(state?.nbWidth)),
    notchMode: txAuxField(hasNotchCap, autoNotchObserved && manualNotchObserved, notchModeValue),
    notchFreq: txAuxField(hasNotchCap, topFieldAvailable(state, 'notchFilter'), numOrUndef(state?.notchFilter)),
    manualNotchWidth: txAuxField(
      hasNotchCap, topFieldAvailable(state, `${base}manualNotchWidth`), numOrUndef(rx?.manualNotchWidth),
    ),
    agcMode: txAuxField(hasAgcCap, topFieldAvailable(state, `${base}agc`), numOrUndef(rx?.agc)),
    agcModes: caps.agcModes ?? [],
    agcTimeConstant: txAuxField(
      hasAgcCap, topFieldAvailable(state, `${base}agcTimeConstant`), numOrUndef(rx?.agcTimeConstant),
    ),
  };
}

/**
 * RF front-end facts (MOR-1262 decomposition slice 6A, MOR-1292; extended by
 * slice 6A′, MOR-1293 with `digiSel`/`ipPlus` — the family-11 enumeration
 * gap the 6A re-verify flagged): preamp, attenuator, RF gain, squelch,
 * DIGI-SEL, IP+. A separate group from `dsp` — family enumeration is
 * explicit and CLOSED (NR/NB/notch/AGC are family 5, `deriveDsp` above;
 * never duplicated here).
 *
 * Evidence gate (N3), purely caps-driven, same shape as `deriveDsp`'s own
 * gate (no raw-state fallback): `hasCap(caps, 'preamp'|'attenuator'|
 * 'rf_gain'|'squelch'|'digisel'|'ip_plus')`, copied verbatim from the shipped
 * `toRfFrontEndProps` (`lib/runtime/props/panel-props.ts`)'s own
 * `showPre`/`showAtt`/`showRfGain`/`showSquelch`/`showDigiSel`/`showIpPlus`
 * gates. The group itself emits only when at least one of the six is
 * declared.
 *
 * `preamp`/`attenuator`/`rfGain`/`squelch`/`digiSel`/`ipPlus` are read
 * straight off the active receiver with NO scale conversion (see
 * `RfFrontEndViewModel`'s doc comment for why there is no "real function" to
 * consume for these six, unlike `dsp`'s `nrLevel`/`nbDepth`) —
 * `numOrUndef(rx?.preamp)`/`boolOrUndef(rx?.digisel)` etc., never a `?? 0`/
 * `?? false` stand-in, so an unobserved control never reports a fabricated
 * reading.
 *
 * The field-freshness gate is this file's own `topFieldAvailable`
 * (`isFieldAvailable`, the same "operational" discipline `deriveTxAux`/
 * `deriveDsp` use), NOT the shipped panel's looser `activeFieldShown` (which
 * treats a stale field as still "shown" for UX continuity, `panel-props.ts`)
 * — for `preamp`/`attenuator`/`rfGain`/`squelch`, that is a deliberate
 * deviation (a fact-layer reading must degrade a stale field to `unknown`;
 * the looser gate is presentation policy, not a fact). `digiSel`/`ipPlus`
 * carry NO such deviation: the shipped panel already gates them on the
 * STRICT `activeFieldAvailable` (`panel-props.ts`'s own `digiSelAvailable`/
 * `ipPlusAvailable`), which calls the exact same `isFieldAvailable` this
 * file's `topFieldAvailable` does — so this file's gate is parity-exact for
 * these two, not a deviation, per the MOR-1292 re-verify's finding.
 *
 * `preValues`/`attValues` are the capability-derived choice sets
 * (`Capabilities.preValues`/`.attValues`, verbatim) — see
 * `RfFrontEndViewModel`'s doc comment. Deliberately `?? []`, never the
 * shipped panel's `[0, 1, 2]`/`[0, 6, 12, 18]` IC-7610-shaped UI-convenience
 * fallback (X6200 lesson: no radio-specific tables in the fact layer).
 *
 * THE MUTEX is NOT derived here — it lives in `toRadioViewModel`, below,
 * where it reads THIS function's own `digiSel` field back rather than
 * re-reading `rx?.digisel` a second time (one raw read per fact, same
 * discipline as every other field here).
 */
function deriveRfFrontEnd(
  state: ServerState | null, caps: Capabilities | null,
): RfFrontEndViewModel | undefined {
  if (!caps) return undefined;
  const hasPreCap = hasCap(caps, 'preamp');
  const hasAttCap = hasCap(caps, 'attenuator');
  const hasRfGainCap = hasCap(caps, 'rf_gain');
  const hasSquelchCap = hasCap(caps, 'squelch');
  const hasDigiSelCap = hasCap(caps, 'digisel');
  const hasIpPlusCap = hasCap(caps, 'ip_plus');
  if (!hasPreCap && !hasAttCap && !hasRfGainCap && !hasSquelchCap && !hasDigiSelCap && !hasIpPlusCap) {
    return undefined;
  }

  const onSub = state?.active === 'SUB';
  const rx = onSub ? state?.sub : state?.main;
  const base = onSub ? 'sub.' : 'main.';

  return {
    preamp: txAuxField(hasPreCap, topFieldAvailable(state, `${base}preamp`), numOrUndef(rx?.preamp)),
    preValues: caps.preValues ?? [],
    attenuator: txAuxField(hasAttCap, topFieldAvailable(state, `${base}att`), numOrUndef(rx?.att)),
    attValues: caps.attValues ?? [],
    rfGain: txAuxField(hasRfGainCap, topFieldAvailable(state, `${base}rfGain`), numOrUndef(rx?.rfGain)),
    squelch: txAuxField(hasSquelchCap, topFieldAvailable(state, `${base}squelch`), numOrUndef(rx?.squelch)),
    digiSel: txAuxField(hasDigiSelCap, topFieldAvailable(state, `${base}digisel`), boolOrUndef(rx?.digisel)),
    ipPlus: txAuxField(hasIpPlusCap, topFieldAvailable(state, `${base}ipplus`), boolOrUndef(rx?.ipplus)),
  };
}

/**
 * THE MUTEX (MOR-479, MOR-1293): the shipped `toRfFrontEndProps` disables the
 * PRE control while DIGI-SEL is ON — `const preDisabled = rx?.digisel ??
 * false` (`panel-props.ts`) — because the IC-7610 silently ignores a PREAMP
 * set in that state. Expressed here as a `disabledReasons` entry rather than
 * a bespoke field (the gap-ticket ruling, MOR-1292 re-verify §5), and
 * consumes THIS group's own `digiSel` fact rather than re-deriving the raw
 * `rx?.digisel ?? false` read a second time — the fact already carries the
 * two-level availability the raw read has no way to express.
 *
 * FAIL-CLOSED (the discriminating deviation from v2's own naive read): the
 * shipped `rx?.digisel ?? false` treats an UNOBSERVED digisel as "off", so a
 * radio that has never reported DIGI-SEL would silently show PREAMP enabled.
 * This function does the opposite for an `unknown` reading — mutex ACTIVE,
 * same as `on` — because "we don't know DIGI-SEL is off" is not evidence
 * that it's safe to optimistically enable PREAMP; it is exactly the class of
 * fabricated-default this whole contract exists to forbid (MOR-988 §3.2).
 * Gated on `preamp.availability.structural` — a radio with no preamp
 * capability at all gets no `rfFrontEnd.preamp` disable entry, mutex or not.
 */
function deriveRfFrontEndMutex(rfFrontEnd: RfFrontEndViewModel | undefined): Reason | null {
  if (!rfFrontEnd) return null;
  const { preamp, digiSel } = rfFrontEnd;
  if (!preamp.availability.structural || !digiSel.availability.structural) return null;
  const mutexActive = digiSel.reading.status === 'unknown' || digiSel.reading.value === true;
  return mutexActive ? { field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' } : null;
}

/**
 * Band facts (MOR-1262 decomposition slice 7A, MOR-1294): current band, the
 * capability-derived band choice set with its per-band TX permits, and the
 * tuning envelope frequency entry validates against. A separate group from
 * `rfFrontEnd` — family enumeration is explicit and CLOSED.
 *
 * Evidence gate (N3), purely caps-driven like `deriveRfFrontEnd`'s: no
 * declared `freqRanges` ⇒ NO group. `freqRanges` is a REQUIRED capability
 * field (always present, possibly empty), so "was it observed" carries no
 * evidence here — the same reasoning `deriveModeFilter` applies to
 * `modes`/`filters`. There is no `band`/`band_select` capability TAG to gate
 * on: the shipped `BandSelector.svelte` gates purely on `freqRanges` being
 * non-empty, and that gate is copied rather than replaced.
 *
 * `flattenBands`/`findActiveBand` are the ONE shipped band-plan derivation
 * (`$lib/radio/band-plan`, moved there from `components-v2/controls/band-utils`
 * by this slice precisely so it could be CONSUMED rather than forked) —
 * called with `caps.freqRanges` from THIS request's own `caps` argument, not
 * the capabilities STORE singleton the v2 callers read (`getCapabilities()?.
 * freqRanges ?? []`). Same discipline as `filterPassband`'s `pbtRangeFromCaps`
 * (MOR-1284 F1) and `dsp`'s `controlRangeFromCapsOrDefault` (MOR-1290 F1): a
 * fact-layer value is a pure function of `(state, caps)`, never of
 * module-global state that can differ from the `caps` already in hand.
 *
 * SAFETY (MOR-1294). Every permit here comes from `getFrequencyPermit(hz,
 * caps.txBands)`, the SAME single derivation `deriveTxCapabilities` calls for
 * the model's top-level `txPermit`. What differs between the two permit facts
 * is only the FREQUENCY ARGUMENT, and that difference is the whole point:
 *  - `bandChoices[].defaultHzTxPermit` samples the band's own `defaultHz` —
 *    the picker label, "may I key where selecting this band lands me".
 *  - `currentBandTx` samples the LIVE observed frequency — "may I key right
 *    now". It is NEVER inherited from the point sample above (verify F1): a
 *    `txBands` segment narrower than its band-plan band (WARC segments,
 *    regional sub-bands, 60m channels) makes the inherited answer fail OPEN,
 *    e.g. live 14.300 MHz reading `allowed` off a 14.000–14.150 allocation
 *    whose 14.074 default happens to be inside. Same derivation, correct
 *    argument — not a second derivation.
 *
 * Two things this deliberately does NOT do:
 *  - it never treats a band's presence in the PLAN as permission to key. The
 *    plan (`freqRanges`) is what the radio can TUNE; `caps.txBands` is where
 *    it may TRANSMIT, and they routinely differ (a general-coverage receiver
 *    tunes far outside its TX allocations). Reading permission off the plan
 *    is the fail-open defect the slice's safety note names.
 *  - it never states a permit on unknown input. `currentBandTx` is `'allowed'`
 *    only when the ACTIVE RECEIVER is positively confirmed (MOR-1356 — the
 *    permit is scoped to that receiver's frequency, so an unconfirmed
 *    identity makes it a permit for a guess; `activeReceiverId` is the ONE
 *    gate, shared with the model's `activeReceiver` fact), the current band
 *    is POSITIVELY known, has a choice entry, AND
 *    the live-frequency permit is POSITIVELY `allowed`; everything else
 *    collapses to `'denied'` exactly as the shipped `getTxPermit` does
 *    ("unknown fails closed", `$lib/utils/tx-permit`). An unknown input must
 *    not enable a TX-adjacent affordance. `validateRadioViewModel` enforces
 *    the known-band half of that invariant structurally.
 */
function deriveBand(
  state: ServerState | null, caps: Capabilities | null, activeReceiver: ReceiverId | null,
): BandViewModel | undefined {
  if (!caps) return undefined;
  const freqRanges = caps.freqRanges ?? [];
  if (freqRanges.length === 0) return undefined;

  const bandChoices: BandChoice[] = flattenBands(freqRanges)
    // A band whose boundaries or default frequency are not finite numbers is
    // OMITTED rather than emitted with a fabricated value: there is no
    // frequency to tune to and therefore no permit to state. Omission is the
    // fail-closed outcome — `currentBandTx` finds no entry for such a band
    // and reads 'denied' even when `findActiveBand` still names it.
    .filter((band) => [band.start, band.end, band.defaultFreq].every((hz) => numOrUndef(hz) !== undefined))
    .map((band) => ({
      name: band.name,
      startHz: band.start,
      endHz: band.end,
      defaultHz: band.defaultFreq,
      // `?? null` is the contract's own "no BSR index declared", not a
      // fabricated 0 — index 0 is a real band-stacking register on Icom rigs.
      bsrCode: numOrUndef(band.bsrCode) ?? null,
      // POINT SAMPLE at defaultHz — the picker label, never the live answer.
      defaultHzTxPermit: getFrequencyPermit(band.defaultFreq, caps.txBands),
    }));

  const onSub = state?.active === 'SUB';
  const rx = onSub ? state?.sub : state?.main;
  // MOR-1356: which receiver the raw `state.active` names is an ORDINARY fact
  // — every reading below keeps reading it ungated, per this file's own
  // convention. The PERMIT is the exception: `activeReceiver` (passed in by
  // the caller, MOR-1421) is the SAME gate the model's own `activeReceiver`
  // fact uses, so a receiver identity the model itself reports as unknown
  // cannot carry TX permission (see `currentBandTx`).
  const activeConfirmed = activeReceiver !== null;
  const freqObserved = topFieldAvailable(state, onSub ? 'sub.freqHz' : 'main.freqHz');
  const freqHz = numOrUndef(rx?.freqHz);
  // Never over a `?? 14074000` stand-in, where the shipped `toBandSelectorProps`
  // fabricates exactly that; an unobserved or plan-less frequency stays unknown.
  const currentName = freqObserved && freqHz !== undefined
    ? findActiveBand(freqHz, freqRanges) ?? undefined
    : undefined;
  const currentBand = txAuxField(bandChoices.length > 0, freqObserved, currentName);
  const reading = currentBand.reading;
  const currentChoice = reading.status === 'known'
    ? bandChoices.find((band) => band.name === reading.value)
    : undefined;
  // THE LIVE-FREQUENCY PERMIT (verify F1). Evaluated at `freqHz`, the
  // frequency the operator is actually on — never inherited from
  // `currentChoice.defaultHzTxPermit`, which only samples the band's default.
  // The `currentChoice`/`reading` conjunction is fail-closed reinforcement,
  // not the permit itself: a frequency inside `txBands` but outside every
  // plan band, or inside a band whose entry was dropped as malformed, still
  // reads 'denied' (and would otherwise break the contract's own
  // "allowed ⇒ currentBand known" invariant).
  const liveFreqPermit = freqObserved && freqHz !== undefined
    ? getFrequencyPermit(freqHz, caps.txBands)
    : getFrequencyPermit(null, caps.txBands);
  const currentBandTx: TxPermit = activeConfirmed && reading.status === 'known'
    && currentChoice !== undefined && liveFreqPermit.status === 'allowed' ? 'allowed' : 'denied';

  const starts = freqRanges.map((range) => range.start).filter((hz) => Number.isFinite(hz));
  const ends = freqRanges.map((range) => range.end).filter((hz) => Number.isFinite(hz));
  return {
    currentBand,
    bandChoices,
    currentBandTx,
    tuneMinHz: starts.length > 0 ? Math.min(...starts) : null,
    tuneMaxHz: ends.length > 0 ? Math.max(...ends) : null,
  };
}

/**
 * RIT/XIT facts (MOR-1262 decomposition slice 8A, MOR-1295): the RIT/XIT
 * enables and their shared frequency offset. A separate group from `txAux`
 * — RIT/XIT is not a TX-adjacent control (it offsets the RX/TX pair without
 * transmitting), and family enumeration stays explicit and closed.
 *
 * Evidence gate (N3), purely caps-driven: `hasCap(caps, 'rit'|'xit')` — the
 * shipped `RitXitPanel`'s own `shouldShowPanel(hasRit, hasXit)` gate,
 * verbatim, no raw-field fallback (same shape as `deriveBand`'s
 * `freqRanges`-only gate).
 *
 * `ritOffset`/`xitOffset` deliberately read the SAME raw field
 * (`state.ritFreq`) and the SAME freshness signal — see `RitXitViewModel`'s
 * doc comment for why duplicating, not deriving one from the other, is the
 * parity-correct shape.
 */
function deriveRitXit(state: ServerState | null, caps: Capabilities | null): RitXitViewModel | undefined {
  const hasRitCap = hasCap(caps, 'rit');
  const hasXitCap = hasCap(caps, 'xit');
  if (!hasRitCap && !hasXitCap) return undefined;
  const offsetObserved = topFieldAvailable(state, 'ritFreq');
  const offsetRaw = numOrUndef(state?.ritFreq);
  return {
    ritActive: txAuxField(hasRitCap, topFieldAvailable(state, 'ritOn'), boolOrUndef(state?.ritOn)),
    ritOffset: txAuxField(hasRitCap, offsetObserved, offsetRaw),
    xitActive: txAuxField(hasXitCap, topFieldAvailable(state, 'ritTx'), boolOrUndef(state?.ritTx)),
    xitOffset: txAuxField(hasXitCap, offsetObserved, offsetRaw),
  };
}

/**
 * Antenna facts (MOR-1262 decomposition slice 8A, MOR-1295): selected TX
 * antenna port, the port-dependent RX-antenna override, and the declared
 * port count. See `AntennaViewModel`'s doc comment for why ATU/tuner state
 * is deliberately absent (family 1, `txAux.atu`) and why `antennaCount` is
 * this group's whole evidence gate.
 *
 * `rxAnt` selects `rxAntenna1`/`rxAntenna2` by the RAW `txAntenna` reading —
 * `toAntennaProps`'s own `txAntenna === 2 ? rxAntenna2 : rxAntenna1` — and,
 * per the "never derive from a half-observed pair" lesson (4A′/5A), is
 * operational only when `txAntenna` ITSELF was honestly observed; an
 * unobserved port never silently resolves to port 1's reading.
 */
function deriveAntenna(state: ServerState | null, caps: Capabilities | null): AntennaViewModel | undefined {
  const antennaCount = caps?.antennas ?? 0;
  if (antennaCount <= 1) return undefined;
  const hasRxAntennaCap = hasCap(caps, 'rx_antenna');
  const txAntennaObserved = topFieldAvailable(state, 'txAntenna');
  const txAntennaRaw = numOrUndef(state?.txAntenna);
  const rxAntennaRaw = txAntennaRaw === 2 ? state?.rxAntenna2 : state?.rxAntenna1;
  const rxAntennaObserved = txAntennaObserved && txAntennaRaw !== undefined
    && topFieldAvailable(state, txAntennaRaw === 2 ? 'rxAntenna2' : 'rxAntenna1');
  return {
    txAntenna: txAuxField(true, txAntennaObserved, txAntennaRaw),
    rxAnt: txAuxField(hasRxAntennaCap, rxAntennaObserved, boolOrUndef(rxAntennaRaw)),
    antennaCount,
  };
}

/**
 * Scan facts (MOR-1262 decomposition slice 8A, MOR-1295): scanning, scan
 * type, and the masked resume mode. No capability tag exists for `scan`
 * anywhere in v2 (the shipped `ScanPanel` renders unconditionally), so —
 * like `deriveMeters` — evidence is per-field "was this ever reported",
 * not a capability check; see `ScanViewModel`'s doc comment.
 */
function deriveScan(state: ServerState | null): ScanViewModel | undefined {
  const raw = [state?.scanning, state?.scanType, state?.scanResumeMode];
  if (!raw.some((v) => v !== undefined)) return undefined;
  const resumeRaw = numOrUndef(state?.scanResumeMode);
  return {
    scanning: txAuxField(
      state?.scanning !== undefined, topFieldAvailable(state, 'scanning'), boolOrUndef(state?.scanning),
    ),
    scanType: txAuxField(
      state?.scanType !== undefined, topFieldAvailable(state, 'scanType'), numOrUndef(state?.scanType),
    ),
    // The shipped `& 0x0F` mask (`toScanProps`), applied verbatim — the raw
    // field carries a direction bit this contract does not interpret.
    scanResumeMode: txAuxField(
      state?.scanResumeMode !== undefined, topFieldAvailable(state, 'scanResumeMode'),
      resumeRaw !== undefined ? (resumeRaw & 0x0f) : undefined,
    ),
  };
}

/** The v2 wire encoding (`BREAK_IN_LABELS`, `components-v2/panels/
 *  cw-panel-logic.ts`), decoded ONCE here. Unlike v2's `formatBreakIn`, an
 *  unrecognised int returns `undefined` (⇒ `unknown` reading) instead of
 *  falling back to OFF — an unreadable break-in state must never present as
 *  "the key is safe". Same shape as `atuStatus` above. */
const breakInMode = (v: unknown): BreakInMode | undefined =>
  v === 0 ? 'off' : v === 1 ? 'semi' : v === 2 ? 'full' : undefined;

/**
 * CW-keyer facts (MOR-1262 decomposition slice 9A, MOR-1296) — SAFETY-CRITICAL.
 * See `CwKeyerViewModel`'s doc comment for the group shape, the closed family
 * enumeration (sidetone LEVEL is `txAux.monitorLevel`, current MODE is
 * `modeFilter.currentMode`, keyer TYPE has no state field at all) and the
 * "no second permit" rule. This function emits READINGS ONLY; every disable —
 * the APF/TPF mode mutex and the break-in TX gate alike — is produced by
 * `deriveCwKeyerReasons` below, which consumes facts, never raw state.
 *
 * Evidence gate (N3): `hasCap(caps, 'cw')`, the shipped panel's own single
 * gate (`LeftSidebar.svelte`/`RightSidebar.svelte` render the CW panel only
 * under `hasCapability('cw')`, and `CwPanel.svelte` wraps its whole body in
 * `{#if showCw}`), copied rather than replaced. One v2 gate, not four: a radio
 * declaring `twin_peak` but not `cw` shows nothing in v2 and gets no group
 * here either. The per-control `break_in`/`apf`/`twin_peak` tags are
 * `toCwProps`'s own sub-gates and become per-field STRUCTURAL availability,
 * exactly as `deriveRitXit` treats `rit`/`xit`.
 *
 * `reversePaddle` consumes `toCwProps`'s own `(state?.dashRatio ?? 0) < 0`
 * predicate — but over an HONEST input: v2's `?? 0` makes an unreported dash
 * ratio read as "not reversed", where an unobserved field here yields
 * `unknown`. Same deviation-from-v2-fabrication story as `pitchHz`/
 * `keyerSpeed`, which never fall back to v2's 600 Hz / 12 WPM.
 */
function deriveCwKeyer(state: ServerState | null, caps: Capabilities | null): CwKeyerViewModel | undefined {
  if (!hasCap(caps, 'cw')) return undefined;
  const hasBreakInCap = hasCap(caps, 'break_in');
  const hasApfCap = hasCap(caps, 'apf');
  const hasTwinPeakCap = hasCap(caps, 'twin_peak');
  const onSub = state?.active === 'SUB';
  const rx = onSub ? state?.sub : state?.main;
  const base = onSub ? 'sub.' : 'main.';
  const dashRatio = numOrUndef(state?.dashRatio);
  return {
    breakIn: txAuxField(hasBreakInCap, topFieldAvailable(state, 'breakIn'), breakInMode(state?.breakIn)),
    breakInDelay: txAuxField(
      hasBreakInCap, topFieldAvailable(state, 'breakInDelay'), numOrUndef(state?.breakInDelay),
    ),
    keyerSpeed: txAuxField(true, topFieldAvailable(state, 'keySpeed'), numOrUndef(state?.keySpeed)),
    pitchHz: txAuxField(true, topFieldAvailable(state, 'cwPitch'), numOrUndef(state?.cwPitch)),
    reversePaddle: txAuxField(
      true, topFieldAvailable(state, 'dashRatio'), dashRatio === undefined ? undefined : dashRatio < 0,
    ),
    apf: txAuxField(hasApfCap, topFieldAvailable(state, `${base}apfTypeLevel`), numOrUndef(rx?.apfTypeLevel)),
    twinPeak: txAuxField(
      hasTwinPeakCap, topFieldAvailable(state, `${base}twinPeakFilter`), boolOrUndef(rx?.twinPeakFilter),
    ),
  };
}

/**
 * Every CW-keyer disable, in one place, derived from FACTS only (MOR-1296).
 *
 * THE APF/TPF MUTEX (MOR-479 lineage, MOR-1293 precedent): `toCwProps`
 * disables APF outside CW/CW-R and TPF outside RTTY/RTTY-R — its own comment
 * calls this a mirror of the MOR-479 preamp mutex — so it is expressed the
 * way the DIGI-SEL/PREAMP mutex is: `disabledReasons` entries with the generic
 * `'mutually-exclusive-control'` code, never bespoke `apfDisabled`/
 * `tpfDisabled` booleans. The mode comes from THIS model's own
 * `modeFilter.currentMode` fact, never a second `rx?.mode` read, and an
 * `unknown` (or structurally-absent) mode leaves BOTH controls disabled —
 * fail-closed, matching what v2's `?? 'USB'` fallback happens to produce
 * while resting on a fabricated value this contract refuses to invent.
 *
 * THE BREAK-IN TX GATE (safety constraint 2, "no second permit"): break-in
 * keys the transmitter, so its affordance is gated on the model's ONE
 * `txPermit` — the value already computed by `deriveTxCapabilities` and
 * passed in here, NOT a second `getFrequencyPermit` call and NOT `state.ptt`
 * (invariant R9). The reason CODES mirror the model-level `txPermit` entries
 * `toRadioViewModel` already emits, so there is one vocabulary for one
 * permit. Anything other than a positively `'allowed'` permit — denied,
 * ranges-unconfigured, tx-target-unknown — disables break-in; that
 * fail-closed pairing is additionally enforced by `validateRadioViewModel`.
 */
function deriveCwKeyerReasons(
  cwKeyer: CwKeyerViewModel | undefined, modeFilter: ModeFilterViewModel | undefined,
  txPermit: FrequencyPermit,
): Reason[] {
  if (!cwKeyer) return [];
  const modeReading = modeFilter?.currentMode.reading;
  const mode = modeReading?.status === 'known' ? modeReading.value : undefined;
  const reasons: Reason[] = [];
  if (cwKeyer.apf.availability.structural && mode !== 'CW' && mode !== 'CW-R') {
    reasons.push({ field: 'cwKeyer.apf', code: 'mutually-exclusive-control' });
  }
  if (cwKeyer.twinPeak.availability.structural && mode !== 'RTTY' && mode !== 'RTTY-R') {
    reasons.push({ field: 'cwKeyer.twinPeak', code: 'mutually-exclusive-control' });
  }
  if (cwKeyer.breakIn.availability.structural && txPermit.status !== 'allowed') {
    reasons.push({
      field: 'cwKeyer.breakIn',
      code: txPermit.status === 'denied' ? 'out-of-band'
        : txPermit.reason === 'ranges-unconfigured' ? 'capability-unavailable' : 'tx-target-unknown',
    });
  }
  return reasons;
}

/**
 * Scope-control facts (MOR-1262 decomposition slice 11A/MOR-1298, extended
 * by slice 11A′/MOR-1299 with mode/edge/hold/refDb, and slice 11A″/MOR-1330
 * with duringTx/centerType/vbwNarrow/rbw). See `ScopeControlsViewModel`'s
 * doc comment for the field set (including why `fixedEdge` is excluded),
 * the parity story against `SpectrumToolbar.svelte`'s/
 * `ScopeSettingsPopover.svelte`'s own `scopeControls?.<leaf> ?? <default>`
 * reads, and the doubly-applied X6200 capability-gating lesson.
 *
 * Evidence gate (N3): `hasCap(caps, 'scope')`, the same single gate the
 * shipped toolbar uses to render at all (`{#if hasCapability('scope')}`,
 * three call sites in `SpectrumToolbar.svelte`) — one v2 gate, not a
 * per-field OR-of-evidence like `deriveTxAux`, because there is no
 * scope-adjacent state that reports independently of the `scope` capability
 * the way TX telemetry does.
 *
 * `mode`/`edge`/`span`/`speed`/`hold`/`refDb`/`duringTx`/`centerType`/
 * `vbwNarrow`/`rbw` are structurally available whenever the group is (every
 * scope-bearing single-RX radio supports them — the backend spec declares
 * all ten as read-only ingress leaves with no additional capability
 * distinction); `dual`/`receiver` additionally require `hasCap(caps,
 * 'dual_rx')` — the only generic tag available to gate "does dual-scope /
 * receiver-select make sense here" without a radio-specific table.
 */
function deriveScopeControls(
  state: ServerState | null, caps: Capabilities | null,
): ScopeControlsViewModel | undefined {
  if (!hasCap(caps, 'scope')) return undefined;
  const hasReceiverSelect = hasCap(caps, 'dual_rx');
  const sc = state?.scopeControls;
  return {
    mode: txAuxField(true, topFieldAvailable(state, 'scopeControls.mode'), numOrUndef(sc?.mode)),
    edge: txAuxField(true, topFieldAvailable(state, 'scopeControls.edge'), numOrUndef(sc?.edge)),
    span: txAuxField(true, topFieldAvailable(state, 'scopeControls.span'), numOrUndef(sc?.span)),
    speed: txAuxField(true, topFieldAvailable(state, 'scopeControls.speed'), numOrUndef(sc?.speed)),
    hold: txAuxField(true, topFieldAvailable(state, 'scopeControls.hold'), boolOrUndef(sc?.hold)),
    refDb: txAuxField(true, topFieldAvailable(state, 'scopeControls.refDb'), numOrUndef(sc?.refDb)),
    dual: txAuxField(
      hasReceiverSelect, topFieldAvailable(state, 'scopeControls.dual'), boolOrUndef(sc?.dual),
    ),
    receiver: txAuxField(
      hasReceiverSelect, topFieldAvailable(state, 'scopeControls.receiver'), numOrUndef(sc?.receiver),
    ),
    duringTx: txAuxField(
      true, topFieldAvailable(state, 'scopeControls.duringTx'), boolOrUndef(sc?.duringTx),
    ),
    centerType: txAuxField(
      true, topFieldAvailable(state, 'scopeControls.centerType'), numOrUndef(sc?.centerType),
    ),
    vbwNarrow: txAuxField(
      true, topFieldAvailable(state, 'scopeControls.vbwNarrow'), boolOrUndef(sc?.vbwNarrow),
    ),
    rbw: txAuxField(true, topFieldAvailable(state, 'scopeControls.rbw'), numOrUndef(sc?.rbw)),
  };
}

/**
 * The App-owned scope-display snapshot the `scopeDisplay` facts are read
 * against (MOR-1262 slice 12A, MOR-1301). Like `RxAudioSnapshot`, this is an
 * INPUT: `defaultScopeStatus` lives on the `FrontendRuntime` singleton
 * (`lib/runtime/frontend-runtime.ts`), not on `ServerState`/`Capabilities`,
 * so a fact derivation may not reach for it directly (MOR-988 §3.2
 * determinism) — the caller reads its own already-live
 * `runtime.defaultScopeStatus` plus the radio-power state it already tracks
 * and hands the values in. Field names mirror `DefaultScopeStatus` verbatim
 * (`source`/`available`/`resourceSelected`/`demand`/`lifecycle`/`transport`/
 * `frameSeen`) so a caller can spread `runtime.defaultScopeStatus` in
 * directly; `isPoweredOff` is the one addition, the status bar's own
 * override input to the same classification (see `classifyScopeHealth`).
 *
 * `hardwareConnected` (MOR-1312, slice 12B) is a SECOND addition: mirrors
 * `runtime.scope.hardwareScopeConnected` verbatim, the hardware channel's own
 * transport regardless of `source` — NOT an input to `classifyScopeHealth`
 * (see `ScopeDisplayViewModel`'s CORRECTION note for why it stays a separate
 * leaf instead).
 */
export interface ScopeDisplaySnapshot {
  source: ScopeSourceKind | null;
  available: boolean;
  resourceSelected: boolean;
  demand: number;
  lifecycle: ResourceHealth;
  // `ConnectionState`'s own literal union, hand-copied rather than imported —
  // see the import comment above (this file's transport-purity guard).
  transport: 'disconnected' | 'connecting' | 'connected' | 'reconnecting';
  frameSeen: boolean;
  isPoweredOff: boolean;
  hardwareConnected: boolean;
}

/**
 * Mirrors `hasAnyScope()` (`$lib/stores/capabilities.svelte.ts`) — the real
 * gate the status bar's scope indicator uses to decide whether to render at
 * all — re-driven on THIS function's own `caps` argument rather than that
 * store singleton, which a fact derivation may not read (determinism, N2).
 * A one-line boolean; agreement with the store function is a direct
 * expression match, not something worth a live-store parity test (which
 * would need `setCapabilities`/`afterEach` cleanup under the fast pool,
 * MOR-1272 — avoided here because it is avoidable).
 */
function hasAnyScopeCap(caps: Capabilities | null): boolean {
  return caps?.scope === true || caps?.scopeSource === 'audio_fft';
}

/**
 * Byte-identical to `deriveScopeIndicatorState` (`components-v2/layout/
 * StatusBar.svelte`) — the shipped status-bar scope indicator's own state
 * machine, reproduced here because `lib/runtime` may not import
 * `components-v2` (ADR 2026-04-12, `eslint.config.js`'s `FORBIDDEN` import
 * boundary), so the real function cannot be called from this file. Parity
 * with the real function, across a full discriminating-combo matrix, is
 * pinned in `__tests__/scope-display-adapter.test.ts` rather than assumed —
 * the same "agree with the real projector" discipline `projectModInputSource`
 * above uses for the same reason (that one duplicates `app-authority.ts`'s
 * `projectInputs`, blocked by the same boundary).
 */
function classifyScopeHealth(s: ScopeDisplaySnapshot): ScopeHealthState {
  if (s.isPoweredOff) return 'disconnected';
  if (s.source === null || !s.available || !s.resourceSelected || s.demand === 0) return 'inactive';
  if (s.lifecycle === 'failed') return 'failed';
  if (s.lifecycle === 'starting') return 'starting';
  if (s.transport === 'connecting') return 'connecting';
  if (s.transport === 'reconnecting') return 'reconnecting';
  if (s.transport === 'disconnected') return 'disconnected';
  if (!s.frameSeen) return 'waiting';
  return s.lifecycle === 'streaming' ? 'connected' : 'inactive';
}

/**
 * Scope-display facts (MOR-1262 decomposition slice 12A, MOR-1301 — the
 * FINAL A-slice of the vocabulary program). See `ScopeDisplayViewModel`'s
 * doc comment for the group-shape rationale (why scope tuning and scope
 * pixels are both deliberately absent).
 *
 * Evidence gate: NO snapshot ⇒ NO group (same `deriveRxAudio` discipline —
 * `defaultScopeStatus` is App-owned, not this layer's to guess), AND
 * `hasAnyScopeCap(caps)` ⇒ NO group otherwise (a radio with neither a
 * hardware scope nor an audio-FFT source has no indicator to state a fact
 * about, mirroring the real status bar rendering nothing at all).
 *
 * Both leaves share the one structural gate: unlike `scopeControls`'s
 * per-leaf capability split, there is exactly one v2 reader here (the status
 * bar's single indicator), so there is exactly one gate.
 */
function deriveScopeDisplay(
  caps: Capabilities | null, snapshot: ScopeDisplaySnapshot | null | undefined,
): ScopeDisplayViewModel | undefined {
  if (!snapshot || !hasAnyScopeCap(caps)) return undefined;
  return {
    source: txAuxField(true, snapshot.source !== null, snapshot.source ?? undefined),
    health: txAuxField(true, true, classifyScopeHealth(snapshot)),
    // MOR-1312 (12B): read straight from the snapshot, never through
    // `classifyScopeHealth` — see `ScopeDisplayViewModel`'s CORRECTION note.
    hardwareConnected: txAuxField(true, true, snapshot.hardwareConnected),
  };
}

/**
 * The App-owned RX-audio snapshot the `rxAudio` facts are read against
 * (MOR-1262 slice 3A). It is an INPUT, deliberately: audio lifetime belongs to
 * the App (MOR-1058) and building a view model must never open, start or probe
 * the audio path (MOR-972 P0). Nothing in this file imports `audio-manager`,
 * `ws-client` or `AudioContext`; the caller reads its own already-live state
 * (`runtime.audio`, `runtime.connectionAudio`, the routing prefs
 * `AudioRoutingControl` restores) and hands the values in. Pinned by
 * `__tests__/rx-audio-purity.isolated.test.ts`.
 */
export interface RxAudioSnapshot {
  /** `AudioUiState` (`lib/runtime/props/panel-props.ts`), verbatim. */
  muted: boolean;
  rxEnabled: boolean;
  /** 0..100 browser RX volume. */
  volume: number;
  /** Audio-WS link health (`runtime.connectionAudio`). */
  connected: boolean;
  /** Browser-side routing prefs; absent/null ⇒ never restored, so the routing
   *  facts read `unknown` rather than the control's own 'both'/false defaults. */
  routing?: { focus: AudioFocus; splitStereo: boolean } | null;
}

/**
 * Projects the active DATA group's MOD-input source exactly as the App TX
 * authority does (`tx-controller/app-authority.ts::projectInputs`): the same
 * three-part `seen()` gate and the same `Number.isSafeInteger` value check.
 * Agreement with the real projector is pinned in
 * `__tests__/rx-audio-adapter.test.ts` rather than assumed.
 */
function projectModInputSource(state: ServerState | null): ModInputSource {
  const rx = state?.active === 'SUB' ? state.sub : state?.main;
  const key = modInputStateKey(rx?.dataMode ?? 0);
  const source = state?.[key];
  return seen(state, key) && typeof source === 'number' && Number.isSafeInteger(source)
    ? { status: 'known', source }
    : { status: 'unknown' };
}

/**
 * RX audio-chain facts. Same positive-evidence discipline as `deriveTxAux`
 * (N3) and `deriveMeters`: NO App snapshot ⇒ NO group (there is no honest
 * monitor mode to state, and guessing one from a store this layer does not own
 * is the MOR-972 failure), and a radio with no AF control, no live audio, no
 * dual-RX routing and no MOD-input routing gets no all-unknowns placeholder.
 *
 * SAFETY: `modInputReadiness` is `deriveTxCapabilities`'s own conclusion,
 * passed straight through. It is NOT re-derived here — the "web voice TX =
 * noise" guard has exactly one derivation in this codebase and this contract
 * exposes it, the same way the meters group exposes the TX authority's.
 */
function deriveRxAudio(
  state: ServerState | null, caps: Capabilities | null, facts: TxCapabilityFacts,
  modInputSource: ModInputSource, audio: RxAudioSnapshot | null | undefined,
): RxAudioViewModel | undefined {
  if (!audio) return undefined;
  const hasLiveAudio = hasCap(caps, 'audio');
  // `toRxAudioProps`'s own gate: the radio's AF control, or the browser stream.
  const hasAfLevel = hasCap(caps, 'af_level') || hasLiveAudio;
  const hasDualRx = hasCap(caps, 'dual_rx');
  const hasModInput = facts.modInputRoutingAvailable;
  if (!hasAfLevel && !hasLiveAudio && !hasDualRx && !hasModInput) return undefined;
  // Byte-identical to `toRxAudioProps`'s monitor-mode derivation; parity across
  // the whole matrix is pinned in `__tests__/rx-audio-adapter.test.ts`.
  const monitorMode: MonitorMode = audio.muted
    ? 'mute'
    : audio.rxEnabled && hasLiveAudio ? 'live' : 'local';
  const onSub = state?.active === 'SUB';
  const rx = onSub ? state?.sub : state?.main;
  const afObserved = state !== null && topFieldAvailable(state, onSub ? 'sub.afLevel' : 'main.afLevel');
  // In `live` mode AF is the browser volume the App already owns, so it is
  // known by construction; otherwise it is the radio's own gated field — and
  // `unknown` when unobserved, where the shipped panel substitutes 0.5.
  const live = monitorMode === 'live';
  const afLevel = live ? numOrUndef(audio.volume / 100) : (afObserved ? numOrUndef(rx?.afLevel) : undefined);
  const routing = audio.routing ?? null;
  const source = modInputSource.status === 'known' ? modInputSource.source : undefined;
  return {
    monitorMode,
    liveAudio: { structural: hasLiveAudio, operational: hasLiveAudio && audio.connected },
    // `txAuxField` is the shared `{reading, availability}` builder — `RxAudioField`
    // IS `TxAuxField` (see the contract's alias), so there is one builder, not a fork.
    afLevel: txAuxField(hasAfLevel, live || afObserved, afLevel),
    routingFocus: txAuxField(hasDualRx, routing !== null, routing?.focus),
    routingSplit: txAuxField(hasDualRx, routing !== null, routing?.splitStereo),
    modInputSource: txAuxField(hasModInput, source !== undefined, source),
    modInputReadiness: facts.modInputReadiness,
  };
}

/**
 * `null` when there is nothing safe to render at all: capabilities have not
 * loaded, or they describe a topology that contradicts itself
 * (`derivePresentationCapabilities` diagnoses that, and a contradictory
 * topology must not be guessed into one of the four canonical shapes).
 */
export function toRadioViewModel(
  state: ServerState | null, caps: Capabilities | null,
  tx?: MetersTxAuthority | null,
  rxAudioSnapshot?: RxAudioSnapshot | null,
  scopeDisplaySnapshot?: ScopeDisplaySnapshot | null,
): RadioViewModel | null {
  if (!caps) return null;
  const presentation = derivePresentationCapabilities(caps);
  const topology = presentation.topology;
  if (!topology) return null;

  const activeId = activeReceiverId(state, topology.structuralCount === 1);
  const activeReceiver: { status: 'known'; receiver: ReceiverId } | { status: 'unknown' } =
    activeId !== null ? { status: 'known', receiver: activeId } : { status: 'unknown' };

  // TX identity and permit come from the SAME derivation the App TX authority
  // uses (`deriveTxCapabilities`), so the surfaces cannot disagree with the
  // controller about what the radio would key.
  const observedTarget = seen(state, 'txTarget') && state
    ? state.txTarget
    : {
      status: 'unknown' as const,
      reason: state?.fieldStatus?.txTarget?.availability === 'stale'
        ? 'stale' as const : 'not-observed' as const,
    };
  // MOR-1274: the REAL projected MOD-input source, not a stub — it is the sole
  // input to `modInputReadiness`, the "web voice TX = noise" guard the rxAudio
  // group exposes. No other fact this call returns depends on it.
  const modInputSource = projectModInputSource(state);
  const facts = deriveTxCapabilities(caps, { txTarget: observedTarget, modInputSource });
  const target = facts.txTarget;
  const txTarget = target.status === 'known'
    ? {
      status: 'known' as const, receiver: target.receiver, frequencyHz: target.frequencyHz,
      slot: (target.slot === null
        ? { kind: 'unslotted' } : { kind: 'slotted', id: target.slot }) as Slot,
    }
    : { status: 'unknown' as const, reason: target.reason };
  const txPermit = facts.frequencyPermit;

  const vfos = topology.structuralReceivers.flatMap((receiver) => {
    const key = RECEIVER_KEY[receiver];
    const rx = state?.[key] ?? null;
    const slots = topology.slots[receiver] ?? null;
    // Gated like every other fact — and it MUST be: the backend defaults
    // `activeSlot` to "A" (`state_schema.py`), so an ungated read marks
    // MAIN A active on evidence the radio never provided.
    const activeSlot = seen(state, `${key}.activeSlot`)
      && (rx?.activeSlot === 'A' || rx?.activeSlot === 'B') ? rx.activeSlot : null;
    const relativeIdentityUnknown = relativeVfoIdentityUnknown(state, caps, key);
    const positions: Position[] = slots === null
      ? [{ slot: { kind: 'unslotted' }, base: `${key}.`, filterKey: 'filter', src: rx }]
      : relativeIdentityUnknown
          ? [
            {
              slot: { kind: 'relative', role: 'selected' }, base: `${key}.`,
              filterKey: 'filter', src: rx,
            },
            {
              slot: { kind: 'relative', role: 'unselected' },
              base: `${key}.unselectedVfo.`, filterKey: 'filterNum',
              src: rx?.unselectedVfo ?? null,
            },
          ]
        : slots.every((id) => rx?.[SLOT_KEY[id]] != null)
          ? slots.map((id) => ({
            slot: { kind: 'slotted', id }, base: `${key}.${SLOT_KEY[id]}.`,
            filterKey: 'filterNum', src: rx?.[SLOT_KEY[id]] ?? null,
          }))
        // A slotted scheme whose slot view was never observed: ONE position of
        // unknown slot identity. Synthesising 'A' here is exactly the
        // fabrication MOR-988 §3.2 forbids.
        : [{ slot: { kind: 'unknown' }, base: `${key}.`, filterKey: 'filter', src: rx }];
    return positions.map(({ slot, base, filterKey, src }) => {
      // MOR-1335 (G4): the per-RECEIVER half of "active", named on its own so a
      // receiver-scoped intent has a slot to address on EVERY receiver — not
      // only on the active one. `activeSlot` is already the gated read above,
      // so an unobserved reading is `null` and BOTH slots fail closed here; an
      // unslotted (or unknown-slot) position is its receiver's only one.
      const isActiveSlot = slot.kind === 'relative' ? slot.role === 'selected'
        : slot.kind !== 'slotted' || slot.id === activeSlot;
      return {
        receiver,
        slot,
        label: slot.kind === 'slotted' ? `${receiver} ${slot.id}`
          : slot.kind === 'relative'
            ? (slot.role === 'selected' ? 'Selected VFO' : 'Unselected VFO')
            : receiver,
        ...readings(state, base, filterKey, src),
        // Unchanged in meaning, restated on the new fact: the radio-wide active
        // VFO IS the active receiver's active slot.
        isActive: activeReceiver.status === 'known' && activeReceiver.receiver === receiver
          && isActiveSlot,
        isActiveSlot,
        isTxTarget: txTarget.status === 'known' && txTarget.receiver === receiver
          && sameSlot(txTarget.slot, slot),
      };
    });
  });

  const split = boolFact(state, 'split', state?.split);
  const dualWatch = boolFact(state, 'dualWatch', state?.dualWatch);
  // Structural = the model has it. Operational = usable right now: the
  // hardware scope needs an observed scope-control block, the audio FFT needs
  // a live state payload for the stream it rides on.
  const hardwareScope = {
    structural: presentation.scope.hardwareScopeAvailable,
    operational: presentation.scope.hardwareScopeAvailable && state?.scopeControls != null,
  };
  const audioFftScope = {
    structural: presentation.scope.audioFftAvailable,
    operational: presentation.scope.audioFftAvailable && state !== null,
  };

  const disabledReasons: Reason[] = [];
  if (activeReceiver.status === 'unknown') {
    disabledReasons.push({ field: 'activeReceiver', code: 'field-not-observed' });
  }
  if (split.status === 'unknown') disabledReasons.push({ field: 'split', code: 'field-not-observed' });
  if (dualWatch.status === 'unknown') {
    disabledReasons.push({ field: 'dualWatch', code: 'field-not-observed' });
  }
  if (txTarget.status === 'unknown') {
    disabledReasons.push({
      field: 'txTarget',
      code: txTarget.reason === 'unsupported' ? 'capability-unavailable' : 'field-not-observed',
    });
  }
  if (txPermit.status === 'denied') disabledReasons.push({ field: 'txPermit', code: 'out-of-band' });
  if (txPermit.status === 'unknown') {
    disabledReasons.push({
      field: 'txPermit',
      code: txPermit.reason === 'ranges-unconfigured' ? 'capability-unavailable' : 'tx-target-unknown',
    });
  }
  for (const [field, availability] of [
    ['scope.hardwareScope', hardwareScope], ['scope.audioFftScope', audioFftScope],
  ] as const) {
    if (!availability.structural) disabledReasons.push({ field, code: 'capability-unavailable' });
    else if (!availability.operational) disabledReasons.push({ field, code: 'field-not-observed' });
  }
  // MOR-1256: `operationalReceivers` (presentation-capabilities.ts) had zero
  // consumers — a `dual-rx-unavailable` radio (structurally dual, no
  // `dual_rx` tag) kept SUB in `vfos` (correct: MOR-977 renders it PRESENT)
  // but nothing ever disabled it. One reason per structurally-present,
  // operationally-absent receiver, same shape as the scope facts above;
  // `dual-receiver-strips.ts`'s `isOperationalStrip` reads it back.
  for (const receiverId of topology.structuralReceivers) {
    if (!topology.operationalReceivers.includes(receiverId)) {
      disabledReasons.push({ field: `receiver.${receiverId}`, code: 'capability-unavailable' });
    }
  }

  // Conditional spread, not a plain `txAux: deriveTxAux(...)` property: an
  // object literal assigning a key to `undefined` still shows up in
  // `Object.keys()`, which would make "no TX capability" indistinguishable
  // from "has the group" to any consumer that inventories keys — same
  // reasoning as the validator's own omission below `validateRadioViewModel`.
  const txAux = deriveTxAux(state, caps);
  const meters = deriveMeters(state, caps, tx);
  const rxAudio = deriveRxAudio(state, caps, facts, modInputSource, rxAudioSnapshot);
  const modeFilter = deriveModeFilter(state, caps);
  const filterPassband = deriveFilterPassband(state, caps);
  const dsp = deriveDsp(state, caps);
  const rfFrontEnd = deriveRfFrontEnd(state, caps);
  const band = deriveBand(state, caps, activeId);
  const ritXit = deriveRitXit(state, caps);
  const antenna = deriveAntenna(state, caps);
  const scan = deriveScan(state);
  const cwKeyer = deriveCwKeyer(state, caps);
  const scopeControls = deriveScopeControls(state, caps);
  const scopeDisplay = deriveScopeDisplay(caps, scopeDisplaySnapshot);
  const rfFrontEndMutex = deriveRfFrontEndMutex(rfFrontEnd);
  if (rfFrontEndMutex) disabledReasons.push(rfFrontEndMutex);
  disabledReasons.push(...deriveCwKeyerReasons(cwKeyer, modeFilter, txPermit));

  return {
    topologyId: `${topology.structuralCount}/${topology.scheme}`,
    vfoScheme: topology.scheme,
    activeReceiver,
    vfos,
    split,
    dualWatch,
    txTarget,
    txPermit,
    scope: { hardwareScope, audioFftScope },
    disabledReasons,
    ...(txAux !== undefined ? { txAux } : {}),
    ...(meters !== undefined ? { meters } : {}),
    ...(rxAudio !== undefined ? { rxAudio } : {}),
    ...(modeFilter !== undefined ? { modeFilter } : {}),
    ...(filterPassband !== undefined ? { filterPassband } : {}),
    ...(dsp !== undefined ? { dsp } : {}),
    ...(rfFrontEnd !== undefined ? { rfFrontEnd } : {}),
    ...(band !== undefined ? { band } : {}),
    ...(ritXit !== undefined ? { ritXit } : {}),
    ...(antenna !== undefined ? { antenna } : {}),
    ...(scan !== undefined ? { scan } : {}),
    ...(cwKeyer !== undefined ? { cwKeyer } : {}),
    ...(scopeControls !== undefined ? { scopeControls } : {}),
    ...(scopeDisplay !== undefined ? { scopeDisplay } : {}),
  };
}
