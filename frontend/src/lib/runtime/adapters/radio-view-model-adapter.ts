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
} from '../../../semantic/radio-view-model';
import type { TxAuthoritySnapshot } from '../../../semantic/rx-tx-surface';
import { isFieldAvailable } from '$lib/state/field-status';
import { modInputStateKey } from '$lib/radio/mod-input';
import { resolveFilterModeConfig } from '$lib/runtime/props/panel-props';
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

type Slot = { kind: 'slotted'; id: VfoSlotId } | { kind: 'unslotted' } | { kind: 'unknown' };
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
  a.kind === 'slotted' && b.kind === 'slotted' ? a.id === b.id : a.kind === b.kind;

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
 *  - `pbtInner`/`pbtOuter` are OPTIONAL, gated on `hasCap(caps, 'pbt')` —
 *    `toFilterProps`'s own `hasPbt` capability, verbatim.
 *  - `ifShift` mirrors `toFilterProps`'s own conditional BYTE-FOR-BYTE: a
 *    radio with the `if_shift` capability reports its own raw field; one
 *    without it, but WITH `pbt`, gets `deriveIfShift(pbtInner, pbtOuter)` —
 *    the ONE shipped fallback, not a re-derivation. Structural is therefore
 *    the OR of both paths (same "real OR, not a stand-in for AND" discipline
 *    `deriveRxAudio`'s `hasAfLevel` uses); operational for the derived path
 *    requires BOTH pbtInner AND pbtOuter to be honestly observed — deriving
 *    from one observed and one silently-defaulted-to-128 input is exactly
 *    the fabrication `deriveModeFilter`'s F2 fix forbids, so neither pbtInner
 *    nor pbtOuter nor ifShift ever computes over the OTHER field's ` ?? 128`
 *    fallback the way `toFilterProps` does.
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
  // `__tests__/filter-passband-adapter.test.ts`. Computed only from the
  // field's OWN raw value — never from a `?? 128` stand-in — so an unobserved
  // pbtInner/pbtOuter never silently seeds a derived ifShift.
  const pbtScale = pbtRangeFromCaps(caps);
  const pbtInnerRaw = numOrUndef(rx?.pbtInner);
  const pbtOuterRaw = numOrUndef(rx?.pbtOuter);
  const pbtInnerHz = pbtInnerRaw !== undefined ? pbtRawToHz(pbtInnerRaw, pbtScale) : undefined;
  const pbtOuterHz = pbtOuterRaw !== undefined ? pbtRawToHz(pbtOuterRaw, pbtScale) : undefined;

  const ifShiftStructural = hasIfShiftCap || hasPbtCap;
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
    pbtInner: txAuxField(hasPbtCap, pbtInnerObserved, pbtInnerHz),
    pbtOuter: txAuxField(hasPbtCap, pbtOuterObserved, pbtOuterHz),
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
  // See the determinism pin in `__tests__/dsp-adapter.test.ts`. Computed only
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
 * The App-owned RX-audio snapshot the `rxAudio` facts are read against
 * (MOR-1262 slice 3A). It is an INPUT, deliberately: audio lifetime belongs to
 * the App (MOR-1058) and building a view model must never open, start or probe
 * the audio path (MOR-972 P0). Nothing in this file imports `audio-manager`,
 * `ws-client` or `AudioContext`; the caller reads its own already-live state
 * (`runtime.audio`, `runtime.connectionAudio`, the routing prefs
 * `AudioRoutingControl` restores) and hands the values in. Pinned by
 * `__tests__/rx-audio-purity.test.ts`.
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
): RadioViewModel | null {
  if (!caps) return null;
  const presentation = derivePresentationCapabilities(caps);
  const topology = presentation.topology;
  if (!topology) return null;

  const activeReceiver: { status: 'known'; receiver: ReceiverId } | { status: 'unknown' } =
    seen(state, 'active') && (state?.active === 'MAIN' || state?.active === 'SUB')
      ? { status: 'known', receiver: state.active }
      : { status: 'unknown' };

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
    const positions: Position[] = slots === null
      ? [{ slot: { kind: 'unslotted' }, base: `${key}.`, filterKey: 'filter', src: rx }]
      : slots.every((id) => rx?.[SLOT_KEY[id]] != null)
        ? slots.map((id) => ({
          slot: { kind: 'slotted', id }, base: `${key}.${SLOT_KEY[id]}.`,
          filterKey: 'filterNum', src: rx?.[SLOT_KEY[id]] ?? null,
        }))
        // A slotted scheme whose slot view was never observed: ONE position of
        // unknown slot identity. Synthesising 'A' here is exactly the
        // fabrication MOR-988 §3.2 forbids.
        : [{ slot: { kind: 'unknown' }, base: `${key}.`, filterKey: 'filter', src: rx }];
    return positions.map(({ slot, base, filterKey, src }) => ({
      receiver,
      slot,
      label: slot.kind === 'slotted' ? `${receiver} ${slot.id}` : receiver,
      ...readings(state, base, filterKey, src),
      isActive: activeReceiver.status === 'known' && activeReceiver.receiver === receiver
        && (slot.kind !== 'slotted' || slot.id === activeSlot),
      isTxTarget: txTarget.status === 'known' && txTarget.receiver === receiver
        && sameSlot(txTarget.slot, slot),
    }));
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
  const rfFrontEndMutex = deriveRfFrontEndMutex(rfFrontEnd);
  if (rfFrontEndMutex) disabledReasons.push(rfFrontEndMutex);

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
  };
}
