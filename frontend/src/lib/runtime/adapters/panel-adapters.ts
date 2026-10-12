/**
 * Panel adapters — derive props and handlers for self-wiring panels.
 *
 * Each panel calls its derive function inside $derived() and its
 * handler function once at init. This replaces prop-passing from sidebars.
 *
 * Add new panel adapters here as panels are migrated to self-wiring.
 */

import { runtime, type ControlSessionSnapshot } from '../frontend-runtime';
import {
  toAgcProps, toModeProps, toAntennaProps,
  toRfFrontEndProps, toRitXitProps, toScanProps,
  toCwProps, toDspProps, toTxProps,
  toFilterProps, toBandSelectorProps,
  toAudioSpectrumProps, toMemoryPanelProps,
  toAmberTelemetryProps, toVfoControlProps,
} from '../props/panel-props';
import {
  makeAgcHandlers, makeModeHandlers, makeAntennaHandlers,
  makeRfFrontEndHandlers, makeRitXitHandlers, makeScanHandlers,
  makeCwPanelHandlers, makeDspHandlers,
  makeTxHandlers, makeFilterHandlers, makeBandHandlers,
  makePresetHandlers, makeAudioRoutingHandlers, makeRxAudioHandlers,
  makeVfoHandlers, makeScopeControlsHandlers, makeVoxHandlers, makeMemoryHandlers,
  makeKeyboardHandlers, makeSystemHandlers, makeRepeaterHandlers,
} from '../commands/panel-commands';
import { toRadioViewModel } from './radio-view-model-adapter';
import {
  getManagedAppTxController, type ManagedAppTxController,
} from '../tx-controller/managed-app-host';
import {
  hasAudioFft, hasDualReceiver, hasCapability,
} from '$lib/stores/capabilities.svelte';
import { recordQsy } from './qsy-history-adapter';
import {
  AF_LEVEL_COMMAND_DESCRIPTOR,
  BREAK_IN_DELAY_COMMAND_DESCRIPTOR,
  CW_PITCH_COMMAND_DESCRIPTOR,
  DSP_COMMAND_DESCRIPTORS,
  FILTER_SHAPE_COMMAND_DESCRIPTOR,
  FILTER_WIDTH_COMMAND_DESCRIPTOR,
  IF_SHIFT_COMMAND_DESCRIPTOR,
  KEY_SPEED_COMMAND_DESCRIPTOR,
  PBT_INNER_COMMAND_DESCRIPTOR,
  PBT_OUTER_COMMAND_DESCRIPTOR,
  RF_GAIN_COMMAND_DESCRIPTOR,
  RF_POWER_COMMAND_DESCRIPTOR,
  SQUELCH_COMMAND_DESCRIPTOR,
  TX_AUX_COMMAND_DESCRIPTORS,
  getCommandLifecycles,
  isCommandLifecycleSuperseded,
  type ControlFeedbackScope,
  type DspCommandFeedbackField,
  type StateBackedCommandDescriptor,
} from '$lib/stores/commands.svelte';
import {
  projectAfLevelControlFeedback,
  projectGlobalCwControlFeedback,
  projectRfPowerControlFeedback,
  projectTxAuxControlFeedback,
  type GlobalCwControl,
  type GlobalCwField,
  type TxAuxControlFeedbackField,
} from './level-feedback';
import { currentControlSessionEpoch } from '../commands/radio-intents';
import { getTuningBurstTargetHz } from '../commands/tuning-accumulator';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';
import {
  activeReceiverOrNull,
  agcArmed, attenuatorArmed, autoNotchArmed, dataModeArmed, filterArmed, filterShapeArmed,
  manualNotchArmed, modeArmed, modInputArmed, preampArmed,
  pendingFilterSelection, pendingFrequencyHz, pendingManualNotchWidth, pendingNbOn,
  pendingNarrow, pendingNotchMode, pendingNrOn, pendingPreampLevel,
  pendingRepeaterShift, pendingRepeaterTone, pendingToneFreq,
  type ArmedFact,
  type SupersededPredicate,
} from './pending-intent-table';
import {
  deriveIfShift, measuredPbtRawToHz, pbtRangeFromCaps,
} from '$lib/radio/filter-controls';
import {
  projectControlFeedback,
  type ControlFeedback,
  type ControlFeedbackOutcome,
  type ControlFeedbackPhase,
  type FilterWidthCommandLifecycleView,
  type FilterWidthLifecyclePresentation,
} from './control-feedback-projection';
import {
  dspControlFeedback,
  measuredPbtLatticeHz,
  pbtFeedbackOnMeasuredLattice,
  pbtStructural,
  receiverEchoControlFeedback,
  rfSqlControlFeedback,
  type RfSqlFeedbackLane,
} from './receiver-feedback';

// Re-export the control-feedback projection core (moved verbatim into
// './control-feedback-projection') so every existing importer of this
// module is unchanged.
export {
  projectControlFeedback,
  projectControlFeedbackToDisplay,
  projectDspControlFeedbackToDisplay,
} from './control-feedback-projection';
export type {
  ControlFeedback,
  ControlFeedbackOutcome,
  ControlFeedbackPhase,
  FilterWidthCommandLifecycleView,
  FilterWidthCommandOutcome,
  FilterWidthCommandPhase,
  FilterWidthLifecyclePresentation,
} from './control-feedback-projection';

// Re-export types for panel imports
export type {
  AgcProps, ModeProps, AntennaProps,
  RfFrontEndProps, RitXitProps, ScanProps,
  CwProps, DspProps, TxProps,
  FilterProps, BandSelectorProps,
  AudioSpectrumProps, MemoryPanelProps,
  AmberTelemetryProps, VfoControlProps,
} from '../props/panel-props';

// ── AGC ──
export function deriveAgcProps() {
  return toAgcProps(runtime.state, runtime.caps);
}
const _agcHandlers = makeAgcHandlers();
export function getAgcHandlers() { return _agcHandlers; }

// ── Mode ──
export function deriveModeProps() {
  return toModeProps(runtime.state, runtime.caps);
}
const _modeHandlers = makeModeHandlers();
export function getModeHandlers() { return _modeHandlers; }

// ── Antenna ──
export function deriveAntennaProps() {
  return toAntennaProps(runtime.state, runtime.caps);
}
const _antennaHandlers = makeAntennaHandlers();
export function getAntennaHandlers() { return _antennaHandlers; }

// ── RF Front End ──
export function deriveRfFrontEndProps() {
  return toRfFrontEndProps(runtime.state, runtime.caps);
}
/**
 * `onRfGainChange`/`onSquelchChange` dispatch `set_rf_gain`/`set_squelch`
 * over the wire as a raw 0-255 integer (`radio-intents.ts`'s `level:
 * 'integer'`, matching `core.radio_protocol.set_rf_gain`'s "0-255 scale"
 * contract) and refuse anything else (`Number.isSafeInteger` guard,
 * `panel-commands.ts`). Every RF-gain/squelch slider in this codebase
 * (`RfFrontEnd.svelte`'s `ValueControl`/`DualParamRenderer`,
 * `RfFrontEndSurface.svelte`'s raw `<input type="range">`) instead reports
 * the radio's own normalized 0..1 reading — so an intermediate drag (e.g.
 * 0.34) silently failed the integer guard, and only the two endpoints (0 and
 * 1, which happen to already be safe integers) ever dispatched. That is the
 * MOR-1447 regression: dragging snapped to 0%/100% only.
 *
 * Applied here for `getRfFrontEndHandlers()` (the legacy `RfFrontEnd.svelte`
 * panel's singleton seam) only — NOT inside `bindSemanticSurfaceHandlers()`,
 * which is pinned to hand back each family's exact, unwrapped factory object
 * (`semantic-surface-handler-binder.isolated.test.ts`).
 * `SemanticRadioSurfaces.svelte` performs the equivalent conversion itself at
 * its `RF_FRONT_END_LEVEL_INTENT` seam for that path.
 */
function withNormalizedRfLevels(
  handlers: ReturnType<typeof makeRfFrontEndHandlers>,
): ReturnType<typeof makeRfFrontEndHandlers> {
  return {
    ...handlers,
    onRfGainChange: (level: number) => handlers.onRfGainChange(Math.round(level * 255)),
    onSquelchChange: (level: number) => handlers.onSquelchChange(Math.round(level * 255)),
  };
}
const _rfHandlers = withNormalizedRfLevels(makeRfFrontEndHandlers());
export function getRfFrontEndHandlers() { return _rfHandlers; }

// ── RIT/XIT ──
export function deriveRitXitProps() {
  return toRitXitProps(runtime.state, runtime.caps);
}
const _ritXitHandlers = makeRitXitHandlers();
export function getRitXitHandlers() { return _ritXitHandlers; }

// ── Scan ──
export function deriveScanProps() {
  return toScanProps(runtime.state);
}
const _scanHandlers = makeScanHandlers();
export function getScanHandlers() { return _scanHandlers; }

// ── CW ──
export function deriveCwProps() {
  return toCwProps(runtime.state, runtime.caps);
}
const _cwHandlers = makeCwPanelHandlers();
export function getCwHandlers() { return _cwHandlers; }

// ── DSP ──
export function deriveDspProps() {
  return toDspProps(runtime.state, runtime.caps);
}
const _dspHandlers = makeDspHandlers();
export function getDspHandlers() { return _dspHandlers; }

// ── TX ──
export function deriveTxProps() {
  return toTxProps(runtime.state, runtime.caps);
}
const _txHandlers = makeTxHandlers();
export function getTxHandlers() { return _txHandlers; }

// ── Filter ──
export function deriveFilterProps() {
  return toFilterProps(runtime.state, runtime.caps);
}
const _filterHandlers = makeFilterHandlers();
export function getFilterHandlers() { return _filterHandlers; }

// ── Band Selector ──
export function deriveBandSelectorProps() {
  return toBandSelectorProps(runtime.state);
}
const _bandHandlers = makeBandHandlers();
export function getBandHandlers() { return _bandHandlers; }
const _presetHandlers = makePresetHandlers();
export function getPresetHandlers() { return _presetHandlers; }

// ── A04 semantic composition bindings ──
// This binder deliberately creates fresh family objects for each mounted
// composition root: filter handlers retain per-instance debounce state.
export function bindSemanticSurfaceHandlers() {
  return Object.freeze({
    agc: makeAgcHandlers(),
    antenna: makeAntennaHandlers(),
    audioRouting: makeAudioRoutingHandlers(),
    band: makeBandHandlers(),
    cw: makeCwPanelHandlers(),
    dsp: makeDspHandlers(),
    filter: makeFilterHandlers(),
    mode: makeModeHandlers(),
    // NOT wrapped with `withNormalizedRfLevels` here: `bindSemanticSurfaceHandlers()`
    // is pinned (`semantic-surface-handler-binder.isolated.test.ts`) to hand
    // back each family's EXACT factory object, unreshaped — the wrapping this
    // module does for `getRfFrontEndHandlers()` below would break that
    // identity contract. `SemanticRadioSurfaces.svelte` does the equivalent
    // normalized-to-raw conversion itself, at its `RF_FRONT_END_LEVEL_INTENT`
    // seam, for the same reason.
    rfFrontEnd: makeRfFrontEndHandlers(),
    ritXit: makeRitXitHandlers(),
    rxAudio: makeRxAudioHandlers(),
    scan: makeScanHandlers(),
    scopeControls: makeScopeControlsHandlers(),
    tx: makeTxHandlers(),
    vfo: makeVfoHandlers(),
    vox: makeVoxHandlers(),
  });
}

// ── Keyboard / System (MOR-1409 A13a) ──
// These two families had no sanctioned adapter-layer path: they are absent
// from this module AND from `bindSemanticSurfaceHandlers()`'s frozen object,
// so the three layouts could only reach them through the `wiring/command-bus`
// shim, which A15 deleted. Singletons, like every other non-binder accessor
// here — neither family holds per-instance state.
const _keyboardHandlers = makeKeyboardHandlers();
export function getKeyboardHandlers() { return _keyboardHandlers; }
const _systemHandlers = makeSystemHandlers();
export function getSystemHandlers() { return _systemHandlers; }
/** MOR-2111 — the repeater panel's command handlers, a singleton like
 *  `getSystemHandlers`. */
const _repeaterHandlers = makeRepeaterHandlers();
export function getRepeaterHandlers() { return _repeaterHandlers; }

// ── Active frequency (MOR-1409 A15) ──
/**
 * The active VFO's observed frequency, or `null` when it has not been
 * observed. Read-only and stateless: it derives from the same view model
 * every other honest projection reads.
 *
 * Deliberately NOT `?? 0`. The store accessor this replaces for presentation
 * (`getFrequency()`) returns `active?.freqHz ?? 0`, and a `0` here would be a
 * radio-truth claim the radio never made — the exact fabrication A15 exists
 * to remove. Callers must treat `null` as "unknown", not as "zero".
 */
export function getActiveFrequencyHz(): number | null {
  const view = toRadioViewModel(runtime.state, runtime.caps);
  return view?.vfos.find((candidate) => candidate.isActive)?.frequencyHz ?? null;
}

const readRuntimeState = (): ServerState | null => runtime.state;
const lifecycleSuperseded: SupersededPredicate = (command) => isCommandLifecycleSuperseded(command);

// ── Pending frequency target (MOR-1441, MOR-1478) ──
/**
 * The freshest still-in-flight `set_freq` target for `receiver` (`0` =
 * MAIN, `1` = SUB — the wire encoding `dispatchRadioIntent` uses), or
 * `null` when no `set_freq` intent is currently pending — or acknowledged
 * but not yet confirmed by the radio's own observed state — for it.
 *
 * This is the pending target the MOR-1425 tuning accumulator races toward
 * during a hot burst — read off the command-bus lifecycle list rather than
 * a second, parallel path into the accumulator's own internal (non-
 * reactive) map.
 *
 * MOR-1478 (live-bench finding, same root cause as MOR-1488's leg-2 fix):
 * a transport ack is not a confirming observation. During a long web-
 * driven tuning spin the MOR-1425 accumulator emits a steady stream of
 * `set_freq` commands; each one's WS ack lands within milliseconds, well
 * before the next confirming poll echoes `main.freqHz`/`sub.freqHz` back
 * (~500ms keep-alive, CLAUDE.md). Releasing pending as soon as a command
 * acked (the pre-fix behavior, `command.status !== 'pending'`) let the
 * readout crawl through the burst and then, on the LAST command's ack,
 * briefly present the STALE pre-spin confirmed value as though it were
 * current for the ~500ms until the next poll actually caught up — the
 * reported symptom. Routed through `latestPendingParam` — the same
 * decision table leg 2's four discrete accessors use — so a command now
 * stays pending through ack.
 */
export function getPendingFrequencyHz(receiver: 0 | 1): number | null {
  return pendingFrequencyHz(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

// ── Tuning burst target (MOR-2464) ──
/**
 * The display-only per-gesture tuning target for `receiver` while local
 * input (arrows/wheel/click) is visually live, or `null`. Reads the
 * tuning accumulator's own publication — the accumulated target,
 * paced-unsent steps included — which unlike `getPendingFrequencyHz`
 * cannot be dropped mid-gesture by an intermediate post-ack field
 * observation. Held only for a short visual idle past the last input
 * (200ms, `DEFAULT_VISUAL_HOLD_MS` in `tuning-accumulator.ts`), after
 * which pending and confirmed truth reconcile the display.
 */
export function getTuningBurstFrequencyHz(receiver: 0 | 1): number | null {
  return getTuningBurstTargetHz(receiver);
}

/** Compatibility name; identity is the sole store-registered descriptor. */
export { FILTER_WIDTH_COMMAND_DESCRIPTOR as FILTER_WIDTH_FEEDBACK_DESCRIPTOR } from '$lib/stores/commands.svelte';

/** Read-only Break-in Delay projection; StateStore remains confirmed truth. */
export function getBreakInDelayControlFeedback(): Readonly<ControlFeedback<number>> {
  return projectControlFeedback(
    BREAK_IN_DELAY_COMMAND_DESCRIPTOR, runtime.state, getCommandLifecycles(),
    { control: 'break-in-delay', receiver: 0 }, currentControlSessionEpoch(), isCommandLifecycleSuperseded,
  );
}

/** Full Filter Width projection for feedback-aware scalar renderers. */
export function getFilterWidthControlFeedback(): Readonly<ControlFeedback<number>> {
  const receiver: 0 | 1 = runtime.state?.active === 'SUB' ? 1 : 0;
  return projectControlFeedback(
    FILTER_WIDTH_COMMAND_DESCRIPTOR, runtime.state, getCommandLifecycles(),
    { control: 'filter-width', receiver }, currentControlSessionEpoch(), isCommandLifecycleSuperseded,
  );
}

/**
 * Full Filter Shape (SHARP/SOFT) projection for the shared choice seam
 * (MOR-1689). Same read shape as `getFilterWidthControlFeedback` above —
 * StateStore stays the only confirmed truth, the freshest in-flight
 * `set_filter_shape` target rides the phase/transition machinery, and the
 * separate `getFilterShapeArmed` below keeps serving the MOR-1519 armed
 * marker (both read the SAME lifecycle through the SAME decision table).
 */
export function getFilterShapeControlFeedback(): Readonly<ControlFeedback<number>> {
  const receiver: 0 | 1 = runtime.state?.active === 'SUB' ? 1 : 0;
  return projectControlFeedback(
    FILTER_SHAPE_COMMAND_DESCRIPTOR, runtime.state, getCommandLifecycles(),
    { control: 'filter-shape', receiver }, currentControlSessionEpoch(), isCommandLifecycleSuperseded,
  );
}

function getGlobalCwControlFeedback(
  currentControlSession: ControlSessionSnapshot | undefined,
  descriptor: StateBackedCommandDescriptor<number>,
  control: GlobalCwControl,
  field: GlobalCwField,
): Readonly<ControlFeedback<number>> {
  const state = runtime.state;
  const caps = runtime.caps;
  const commands = getCommandLifecycles();
  const session = currentControlSession ?? runtime.controlSession;
  const epoch = Number.isSafeInteger(session.epoch) && session.epoch >= 0 ? session.epoch : -1;
  return projectGlobalCwControlFeedback(
    descriptor, state, caps, commands, epoch,
    (command) => isCommandLifecycleSuperseded(command),
    session.state === 'connected', control, field,
  );
}

/** Radio-global CW Pitch projection; receiver 0 is only a stable scope encoding. */
export function getCwPitchControlFeedback(
  currentControlSession?: ControlSessionSnapshot,
): Readonly<ControlFeedback<number>> {
  return getGlobalCwControlFeedback(
    currentControlSession, CW_PITCH_COMMAND_DESCRIPTOR, 'cw-pitch', 'cwPitch',
  );
}

/** Radio-global Keyer Speed projection; receiver 0 is only a stable scope encoding. */
export function getKeySpeedControlFeedback(
  currentControlSession?: ControlSessionSnapshot,
): Readonly<ControlFeedback<number>> {
  return getGlobalCwControlFeedback(
    currentControlSession, KEY_SPEED_COMMAND_DESCRIPTOR, 'keyer-speed', 'keySpeed',
  );
}

export type { TxAuxControlFeedbackField };

/** Qualified radio-global TX/VOX feedback; receiver 0 is only stable identity. */
export function getTxAuxControlFeedback(
  field: TxAuxControlFeedbackField,
  currentControlSession?: ControlSessionSnapshot,
): Readonly<ControlFeedback<number>> {
  const state = runtime.state;
  const caps = runtime.caps;
  const commands = getCommandLifecycles();
  const session = currentControlSession ?? runtime.controlSession;
  const epoch = Number.isSafeInteger(session.epoch) && session.epoch >= 0 ? session.epoch : -1;
  const descriptor = TX_AUX_COMMAND_DESCRIPTORS[field];
  return projectTxAuxControlFeedback(
    descriptor, state, caps, commands, epoch,
    (command) => isCommandLifecycleSuperseded(command),
    session.state === 'connected', field,
  );
}

export type DspControlFeedbackField = DspCommandFeedbackField;

/** Qualified raw DSP feedback; NB Width and NB Depth have stable radio-global identity. */
export function getDspControlFeedback(
  field: DspControlFeedbackField,
  currentControlSession?: ControlSessionSnapshot,
): Readonly<ControlFeedback<number>> {
  const state = runtime.state;
  const caps = runtime.caps;
  const commands = getCommandLifecycles();
  const session = currentControlSession ?? runtime.controlSession;
  return dspControlFeedback(
    field, session, state, caps, commands,
    DSP_COMMAND_DESCRIPTORS[field], isCommandLifecycleSuperseded,
  );
}

/** One qualified receiver/session snapshot for the two-lane RF/SQL owner. */
export function getRfSqlControlFeedback(
  currentControlSession?: ControlSessionSnapshot,
): Readonly<{ rf: RfSqlFeedbackLane; sql: RfSqlFeedbackLane }> | null {
  const state = runtime.state;
  const caps = runtime.caps;
  const commands = getCommandLifecycles();
  const session = currentControlSession ?? runtime.controlSession;
  return rfSqlControlFeedback(
    session, state, caps, commands,
    RF_GAIN_COMMAND_DESCRIPTOR, SQUELCH_COMMAND_DESCRIPTOR, isCommandLifecycleSuperseded,
  );
}

/** Qualified AF-level command feedback for `receiver`, or for the
 *  active receiver when none is named. */
export function getAfLevelControlFeedback(
  currentControlSession?: ControlSessionSnapshot,
  receiver?: 'MAIN' | 'SUB',
): Readonly<ControlFeedback<number>> {
  const { state, caps } = runtime;
  const commands = getCommandLifecycles();
  const session = currentControlSession ?? runtime.controlSession;
  const epoch = Number.isSafeInteger(session.epoch) && session.epoch >= 0 ? session.epoch : -1;
  return projectAfLevelControlFeedback(
    AF_LEVEL_COMMAND_DESCRIPTOR, state, caps, commands, epoch,
    (command) => isCommandLifecycleSuperseded(command),
    session.state === 'connected', receiver,
  );
}

/** Qualified RF-power command feedback on the normalized `powerLevel` scale. */
export function getRfPowerControlFeedback(
  currentControlSession?: ControlSessionSnapshot,
): Readonly<ControlFeedback<number>> {
  const { state, caps } = runtime;
  const commands = getCommandLifecycles();
  const session = currentControlSession ?? runtime.controlSession;
  const epoch = Number.isSafeInteger(session.epoch) && session.epoch >= 0 ? session.epoch : -1;
  return projectRfPowerControlFeedback(
    RF_POWER_COMMAND_DESCRIPTOR, state, caps, commands, epoch,
    (command) => isCommandLifecycleSuperseded(command),
    session.state === 'connected',
  );
}

function getReceiverEchoControlFeedback(
  currentControlSession: ControlSessionSnapshot | undefined,
  descriptor: StateBackedCommandDescriptor<number>,
  control: string,
  field: 'pbtInner' | 'pbtOuter' | 'ifShift',
  structuralFor: (caps: Capabilities | null | undefined, tags: readonly string[]) => boolean,
): Readonly<ControlFeedback<number>> {
  const state = runtime.state;
  const caps = runtime.caps;
  const commands = getCommandLifecycles();
  const session = currentControlSession ?? runtime.controlSession;
  return receiverEchoControlFeedback(
    session, state, caps, commands, descriptor, control, field, structuralFor,
    isCommandLifecycleSuperseded,
  );
}

/** Qualified raw PBT Inner feedback (MOR-2425); receiver derives from active-receiver truth. */
export function getPbtInnerControlFeedback(
  currentControlSession?: ControlSessionSnapshot,
): Readonly<ControlFeedback<number>> {
  return getReceiverEchoControlFeedback(
    currentControlSession, PBT_INNER_COMMAND_DESCRIPTOR, 'pbt-inner', 'pbtInner', pbtStructural,
  );
}

/** Qualified raw PBT Outer feedback (MOR-2425); receiver derives from active-receiver truth. */
export function getPbtOuterControlFeedback(
  currentControlSession?: ControlSessionSnapshot,
): Readonly<ControlFeedback<number>> {
  return getReceiverEchoControlFeedback(
    currentControlSession, PBT_OUTER_COMMAND_DESCRIPTOR, 'pbt-outer', 'pbtOuter', pbtStructural,
  );
}

/** Qualified PBT Inner feedback in the display unit (Hz on the measured lattice). */
export function getPbtInnerHzControlFeedback(
  currentControlSession?: ControlSessionSnapshot,
): Readonly<ControlFeedback<number>> {
  return pbtFeedbackOnMeasuredLattice(
    getPbtInnerControlFeedback(currentControlSession),
    measuredPbtLatticeHz(runtime.state, runtime.caps),
  );
}

/** Qualified PBT Outer feedback in the display unit (Hz on the measured lattice). */
export function getPbtOuterHzControlFeedback(
  currentControlSession?: ControlSessionSnapshot,
): Readonly<ControlFeedback<number>> {
  return pbtFeedbackOnMeasuredLattice(
    getPbtOuterControlFeedback(currentControlSession),
    measuredPbtLatticeHz(runtime.state, runtime.caps),
  );
}

/**
 * IF-shift feedback is always Hz-domain, never PBT's raw BCD domain: real on
 * a radio with its own `if_shift` command (raw IS Hz there, identity-
 * mapped), or derived from the two PBT feedbacks by converting each side's
 * raw value on the measured lattice first. `domain` is a real, checkable
 * field — not a comment that can rot — so a caller or test can assert it
 * instead of trusting prose about which domain `confirmed`/`target` are in.
 */
export interface IfShiftControlFeedback extends ControlFeedback<number> {
  readonly domain: 'hz';
}

/**
 * Non-terminal phases in lifecycle order, least advanced first. Read only
 * from a side whose `busy` is true, which (per `projectControlFeedback`)
 * means its `phase` is one of exactly these four — never `'idle'` or a
 * terminal phase. A `'queued'` (held-for-tx) overlay can land on a command
 * that is already `'acknowledged'` underneath
 * (`commands.svelte.ts: applyCommandLifecycleProjection`'s `'held'` branch
 * accepts both `'pending'` and `'acknowledged'`), so it ranks ahead of
 * `'awaiting-confirmation'` here: a held command has made less progress
 * toward confirmation than one merely awaiting its echo, regardless of the
 * status it was held from.
 */
const IN_FLIGHT_ORDER: readonly ControlFeedbackPhase[] = Object.freeze([
  'submitted', 'queued', 'dispatched', 'awaiting-confirmation',
]);

/**
 * The least-advanced of two busy sides' phases, by `IN_FLIGHT_ORDER` — the
 * derived IF-shift feedback is never more settled than the side that has
 * made the least progress toward confirmation.
 */
function mergeBusyPhase(
  inner: Readonly<ControlFeedback<number>>, outer: Readonly<ControlFeedback<number>>,
): ControlFeedbackPhase {
  if (inner.busy && outer.busy) {
    return IN_FLIGHT_ORDER.indexOf(inner.phase) <= IN_FLIGHT_ORDER.indexOf(outer.phase)
      ? inner.phase : outer.phase;
  }
  return inner.busy ? inner.phase : outer.phase;
}

/**
 * Terminal outcomes ranked worst-first, used only when both sides' records
 * are live and terminal: a non-`'confirmed'` outcome on either side beats a
 * `'confirmed'` one on the other. The order among the non-`'confirmed'`
 * outcomes is otherwise arbitrary but fixed, so two differently-failed
 * sides still resolve to one deterministic outcome.
 */
const TERMINAL_OUTCOME_PRIORITY: readonly ControlFeedbackOutcome[] = Object.freeze([
  'failed', 'timed-out', 'cancelled', 'superseded', 'confirmed',
]);

/**
 * Combines two non-busy sides' phase/outcome. `outcome === null` here means
 * idle under `getPbtInner/OuterControlFeedback`'s own definition: no
 * lifecycle ever ran, or one did and its record already retired
 * (`commands.svelte.ts: retainTerminalOutcome` expires each record
 * `OUTCOME_RETENTION_MS` after its own terminal transition, independent of
 * the other side's), or `projectControlFeedback` filtered it out
 * (`locallyObsolete`, provider-generation mismatch). Idle is therefore not
 * "no lifecycle ran" — it can equally mean "ran and the evidence is gone" —
 * which is why an idle side may never promote the other to `'confirmed'`:
 *
 *   idle | idle                -> idle       (case A: no evidence either side)
 *   idle | confirmed           -> idle       (case B: a lone confirmed half
 *                                              is not corroborated once the
 *                                              other half's record is gone;
 *                                              not reported)
 *   idle | non-confirmed       -> that outcome (case C: while its record
 *                                                stays live)
 *   live terminal | live terminal -> `TERMINAL_OUTCOME_PRIORITY` (case D)
 *
 * `'confirmed'` is reachable only through case D with both sides confirmed:
 * a half-failed gesture is never derived as `'confirmed'`, at any point in
 * its retention window or after.
 */
function mergeTerminalOutcome(
  inner: Readonly<ControlFeedback<number>>, outer: Readonly<ControlFeedback<number>>,
): { phase: ControlFeedbackPhase; outcome: Readonly<{ phase: ControlFeedbackOutcome; error?: string }> | null } {
  const bothIdle = inner.outcome === null && outer.outcome === null;
  if (bothIdle) return { phase: 'idle', outcome: null }; // case A

  const bothLiveTerminal = inner.outcome !== null && outer.outcome !== null;
  if (bothLiveTerminal) { // case D
    const winner = TERMINAL_OUTCOME_PRIORITY.indexOf(inner.outcome.phase)
      <= TERMINAL_OUTCOME_PRIORITY.indexOf(outer.outcome.phase) ? inner : outer;
    return { phase: winner.phase, outcome: winner.outcome };
  }

  const live = inner.outcome !== null ? inner : outer; // exactly one side idle
  if (live.outcome!.phase === 'confirmed') return { phase: 'idle', outcome: null }; // case B
  return { phase: live.phase, outcome: live.outcome }; // case C
}

function unavailableIfShiftFeedback(
  scope: Readonly<ControlFeedbackScope>, sessionEpoch: number,
  providerGeneration: number | null,
): Readonly<IfShiftControlFeedback> {
  return Object.freeze({
    confirmed: null, target: null, requestedTarget: null,
    phase: 'unavailable' as const, busy: false, availability: 'unavailable' as const,
    outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration, sessionEpoch, scope,
    repeatPolicy: 'latest-target-wins' as const, domain: 'hz' as const,
  });
}

/**
 * Qualified IF-shift feedback (MOR-2425). Real on a radio with its own
 * `if_shift` command (Yaesu FTX-1) — gated on the SAME predicate
 * (`caps.capabilities.includes('if_shift')`) `radio-view-model-adapter.ts`'s
 * `ifShiftControlStructural` uses to decide whether to show a real IF-shift
 * control at all. Derived from the two PBT feedbacks on a PBT-only radio
 * (Icom IC-7300): each side's raw value converts on the MEASURED lattice
 * (MOR-2497 — the current mode's declared `pbtStepHz` and the observed
 * filter width, the same conversion `deriveFilterPassband` reads its
 * `ifShift` fallback with) before combining with `deriveIfShift` — this is
 * that same formula's pending-target-aware lifecycle layer, not a second,
 * independent re-derivation of the confirmed reading. No lattice — a legacy
 * payload without `pbtStepHz`, or a width that has not been observed —
 * converts nothing, so the derived feedback is unavailable: no fabricated
 * +/-1200-scale Hz, the same fail-closed refusal the reading path applies.
 */
export function getIfShiftControlFeedback(
  currentControlSession?: ControlSessionSnapshot,
): Readonly<IfShiftControlFeedback> {
  const caps = runtime.caps;
  const tags = Array.isArray(caps?.capabilities) ? caps.capabilities : [];

  if (tags.includes('if_shift')) {
    const real = getReceiverEchoControlFeedback(
      currentControlSession, IF_SHIFT_COMMAND_DESCRIPTOR, 'if-shift', 'ifShift',
      (_c, t) => t.includes('if_shift'),
    );
    return Object.freeze({ ...real, domain: 'hz' as const });
  }

  const state = runtime.state;
  const session = currentControlSession ?? runtime.controlSession;
  const epoch = Number.isSafeInteger(session.epoch) && session.epoch >= 0 ? session.epoch : -1;
  const receiver: 0 | 1 = state?.active === 'SUB' ? 1 : 0;
  const scope = Object.freeze({ control: 'if-shift', receiver });
  const inner = getPbtInnerControlFeedback(currentControlSession);
  const outer = getPbtOuterControlFeedback(currentControlSession);
  const hasDeclaredScale = pbtRangeFromCaps(caps) !== undefined;
  const providerGeneration = typeof inner.providerGeneration === 'number' ? inner.providerGeneration : null;
  if (inner.availability !== 'available' || outer.availability !== 'available' || !hasDeclaredScale
    || inner.confirmed === null || outer.confirmed === null) {
    return unavailableIfShiftFeedback(scope, epoch, providerGeneration);
  }
  // MOR-2497: raw -> Hz on the measured lattice — the mode's declared step
  // at the observed width — the same conversion the reading path uses. A
  // raw that converts to nothing (no step for the mode, no observed width)
  // fails the whole derived feedback closed rather than fabricating Hz.
  const lattice = measuredPbtLatticeHz(state, caps);
  const toHz = (raw: number): number | null => lattice === null
    ? null : measuredPbtRawToHz(raw, lattice.filterWidthHz, lattice.stepHz);
  const pairHz = (a: number, b: number): number | null => {
    const innerHz = toHz(a);
    const outerHz = toHz(b);
    return innerHz === null || outerHz === null ? null : deriveIfShift(innerHz, outerHz);
  };
  const confirmed = pairHz(inner.confirmed, outer.confirmed);
  if (confirmed === null) {
    return unavailableIfShiftFeedback(scope, epoch, providerGeneration);
  }
  const busy = inner.busy || outer.busy;
  const innerForTarget = inner.busy && inner.target !== null ? inner.target : inner.confirmed;
  const outerForTarget = outer.busy && outer.target !== null ? outer.target : outer.confirmed;
  const target = busy ? pairHz(innerForTarget, outerForTarget) : null;
  const innerForRequested = inner.requestedTarget ?? inner.confirmed;
  const outerForRequested = outer.requestedTarget ?? outer.confirmed;
  const requestedTarget = inner.requestedTarget === null && outer.requestedTarget === null
    ? null : pairHz(innerForRequested, outerForRequested);
  const { phase, outcome } = busy
    ? { phase: mergeBusyPhase(inner, outer), outcome: null }
    : mergeTerminalOutcome(inner, outer);
  // Idle carries no ids anywhere else in this file (`empty()`,
  // `unavailableIfShiftFeedback`) — including here, so that
  // `mergeTerminalOutcome`'s idle-on-a-retired-confirmation case (case B
  // above) does not compose a fresh id out of the surviving side's live
  // outcome and trigger a one-shot announcement for a phase nobody reports.
  const lifecycleId = phase === 'idle' || (inner.lifecycleId === null && outer.lifecycleId === null)
    ? null : JSON.stringify([inner.lifecycleId, outer.lifecycleId]);
  const transitionId = phase === 'idle' || (inner.transitionId === null && outer.transitionId === null)
    ? null : JSON.stringify([inner.transitionId, outer.transitionId]);
  return Object.freeze({
    confirmed, target, requestedTarget, phase, busy, availability: 'available' as const,
    outcome, lifecycleId, transitionId,
    providerGeneration, sessionEpoch: inner.sessionEpoch,
    scope, repeatPolicy: 'latest-target-wins' as const, domain: 'hz' as const,
  });
}

function filterWidthPresentation(
  feedback: Readonly<ControlFeedback<number>>,
): Readonly<FilterWidthLifecyclePresentation> {
  const status = feedback.phase === 'submitted' ? 'pending'
    : feedback.phase === 'queued' || feedback.phase === 'dispatched' ? 'pending'
    : feedback.phase === 'awaiting-confirmation' ? 'acknowledged'
    : feedback.phase === 'superseded' ? 'cancelled' : feedback.phase;
  const error = feedback.outcome?.error;
  return Object.freeze({
    lifecycleId: feedback.lifecycleId!, transitionId: feedback.transitionId!,
    receiver: feedback.scope.receiver, sessionEpoch: feedback.sessionEpoch,
    target: feedback.requestedTarget!, status: status as FilterWidthLifecyclePresentation['status'],
    ...(error === undefined ? {} : { error }),
  });
}

export function getFilterWidthCommandLifecycle(): FilterWidthCommandLifecycleView {
  const feedback = getFilterWidthControlFeedback();
  if (feedback.availability === 'unavailable') {
    return { confirmed: null, target: null, phase: 'unavailable', busy: false, outcome: null, presentation: null };
  }
  if (feedback.phase === 'idle') {
    return { confirmed: feedback.confirmed, target: null, phase: 'idle', busy: false, outcome: null, presentation: null };
  }
  const presentation = filterWidthPresentation(feedback);
  if (feedback.phase === 'confirmed') return { confirmed: feedback.confirmed, target: null,
    phase: 'confirmed', busy: false, outcome: { phase: 'confirmed' }, presentation };
  if (feedback.phase === 'failed' || feedback.phase === 'timed-out'
    || feedback.phase === 'cancelled' || feedback.phase === 'superseded') {
    return { confirmed: feedback.confirmed, target: null, phase: 'idle', busy: false,
      outcome: { phase: feedback.phase, error: feedback.outcome?.error }, presentation };
  }
  return { confirmed: feedback.confirmed, target: feedback.target,
    phase: feedback.phase === 'awaiting-confirmation' ? 'acknowledged' : 'pending',
    busy: true, outcome: null, presentation };
}

// ── Pending discrete-control targets (MOR-1441 leg 2) ──
/** Freshest unconfirmed `set_filter` target for `receiver`, or `null`.
 *  `FilterSurface` renders this as a marker on the targeted choice only —
 *  the confirmed reading stays the group's sole selection source (leg-1
 *  lesson: pending is display-only). */
export function getPendingFilterSelection(receiver: 0 | 1): number | null {
  return pendingFilterSelection(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

/** Freshest unconfirmed `set_preamp` target for `receiver`, or `null`. Never
 *  touches the MOR-1447 combined-knob/change-guard machinery, which reads
 *  only confirmed fields. */
export function getPendingPreampLevel(receiver: 0 | 1): number | null {
  return pendingPreampLevel(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

/** Freshest unconfirmed `set_nb` target for `receiver`, or `null`. */
export function getPendingNbOn(receiver: 0 | 1): boolean | null {
  return pendingNbOn(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

/** Freshest unconfirmed `set_nr` target for `receiver`, or `null`. */
export function getPendingNrOn(receiver: 0 | 1): boolean | null {
  return pendingNrOn(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

/** Freshest unconfirmed `set_narrow` target for `receiver`, or `null`
 *  (MOR-2640). Same single-strand boolean shape as `getPendingNbOn`/
 *  `getPendingNrOn` above — the same decision table, so confirmation,
 *  failure and supersession clear it by the same rule. */
export function getPendingNarrow(receiver: 0 | 1): boolean | null {
  return pendingNarrow(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

/**
 * Freshest unconfirmed notch-mode choice for `receiver`, or `null`.
 * Notch mode is written as TWO independent boolean commands (`set_auto_notch`
 * /`set_manual_notch`, `makeDspHandlers().onNotchModeChange`), so the single
 * requested choice is folded from both strands over the SAME lifecycle
 * decision table (`latestPendingParam`) the sibling accessors above use —
 * confirmation, failure and supersession clear it by the same rule:
 *  - an `on:true` auto strand requests 'auto', an `on:true` manual strand
 *    requests 'manual';
 *  - 'off' needs the MOR-1541 pair: BOTH strands in flight with `on:false`
 *    (a lone `on:false` strand is a half-arrived pair, not yet 'off');
 *  - when both strands carry a pending `on:true`, the FRESHEST strand wins
 *    (latest-target-wins, same tie-break `latestPendingParam` uses).
 */
export function getPendingNotchMode(receiver: 0 | 1): 'off' | 'auto' | 'manual' | null {
  return pendingNotchMode(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

/** Freshest unconfirmed `set_manual_notch_width` target for `receiver`, or
 *  `null`. One strand, one param (`value`), confirmed against
 *  `manualNotchWidth` — the same decision table the sibling accessors use,
 *  so confirmation, failure and supersession clear it by the same rule. */
export function getPendingManualNotchWidth(receiver: 0 | 1): number | null {
  return pendingManualNotchWidth(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

// ── Repeater panel pending targets (MOR-2111) ──
/**
 * Freshest unconfirmed repeater tone-mode target for `receiver`, or `null`,
 * collapsed from the in-flight `set_repeater_tone`/`set_repeater_tsql`
 * pair: tone off means OFF (TONE→OFF, and TSQL→OFF, which sends both), then
 * TSQL on means TSQL, then TSQL off or tone on means TONE.
 */
export function getPendingRepeaterTone(receiver: 0 | 1): 'off' | 'tone' | 'tsql' | null {
  return pendingRepeaterTone(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

/** Freshest unconfirmed repeater shift direction for `receiver`, or `null`. */
export function getPendingRepeaterShift(receiver: 0 | 1): number | null {
  return pendingRepeaterShift(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

/** Freshest unconfirmed CTCSS tone frequency (centiHz) for `receiver`, or
 *  `null`. The panel writes `set_tone_freq` in OFF/TONE and `set_tsql_freq`
 *  in TSQL, so both in-flight names count. */
export function getPendingToneFreq(receiver: 0 | 1): number | null {
  return pendingToneFreq(getCommandLifecycles(), readRuntimeState, Date.now(), lifecycleSuperseded, receiver);
}

// ── Generic armed/pending signal (MOR-1519) ──
export type { ArmedFact } from './pending-intent-table';

/**
 * MODE buttons' armed fact (MOR-1519, first consumer of the generic signal
 * above). No `receiver` param: `ModePanel` renders a single mode grid for
 * the ACTIVE receiver only, the same single-receiver read `toModeProps`'s
 * `activeRx(state)` already performs (`panel-props.ts`) — `state.active`
 * mirrors that helper's `'SUB' ? sub : main` split.
 *
 * KNOWN BOUND: `onModeChange` (`panel-commands.ts`) can target a DIFFERENT
 * receiver than `state.active` currently reports during the focus-echo
 * window (`consumePendingFocus()`) — armed degrades to no-feedback for that
 * click. Same staleness class `currentMode`/`toModeProps` already carries
 * (both read `state.active`); not addressed by this accessor.
 */
export function getModeArmed(): ArmedFact<string> {
  const state = runtime.state;
  if (!state) return { armed: false, value: null };
  return modeArmed(getCommandLifecycles(), state, Date.now(), lifecycleSuperseded);
}

// ── Armed-signal adoption long tail (MOR-1536) ──
/** AGC mode buttons' armed fact (`set_agc`). No `receiver` param — same
 *  single-active-receiver read `toAgcProps`'s `activeRx(state)` uses. */
export function getAgcArmed(): ArmedFact<number> {
  const state = runtime.state;
  if (activeReceiverOrNull(state) === null) return { armed: false, value: null };
  return agcArmed(getCommandLifecycles(), state, Date.now(), lifecycleSuperseded);
}

/** Filter-select (FIL1/2/3) armed fact (`set_filter`). This is the same
 *  pending read `getPendingFilterSelection(receiver)` above makes — this
 *  wrapper only supplies the no-receiver-arg, `ArmedFact`-shaped read
 *  `FilterPanel.svelte` needs (parity with `getModeArmed`'s shape), not a
 *  second implementation. */
export function getFilterArmed(): ArmedFact<number> {
  const state = runtime.state;
  if (activeReceiverOrNull(state) === null) return { armed: false, value: null };
  return filterArmed(getCommandLifecycles(), state, Date.now(), lifecycleSuperseded);
}

/** Filter-shape (SHARP/SOFT) armed fact (`set_filter_shape`, param `shape`,
 *  confirmed field `filterShape`). A DIFFERENT intent from `getFilterArmed`
 *  above (`set_filter`): a shape change and a filter selection can be in
 *  flight at the same time and must not be conflated — same separation as
 *  `getDataModeArmed` from `getModeArmed`. */
export function getFilterShapeArmed(): ArmedFact<number> {
  const state = runtime.state;
  if (activeReceiverOrNull(state) === null) return { armed: false, value: null };
  return filterShapeArmed(getCommandLifecycles(), state, Date.now(), lifecycleSuperseded);
}

/** Preamp-level armed fact (`set_preamp`). Same underlying primitive as
 *  `getPendingPreampLevel(receiver)` above, `ArmedFact`-shaped. */
export function getPreampArmed(): ArmedFact<number> {
  const state = runtime.state;
  if (activeReceiverOrNull(state) === null) return { armed: false, value: null };
  return preampArmed(getCommandLifecycles(), state, Date.now(), lifecycleSuperseded);
}

/** Attenuator armed fact (`set_attenuator`, param `db`, confirmed field
 *  `att`) — `makeRfFrontEndHandlers().onAttChange` dispatches `db`, not
 *  `level`. Wired only for `RfFrontEnd.svelte`'s 2-value HardwareButton
 *  toggle (the live-bench IC-7300/FTX-1 shape, both `attValues.length ===
 *  2`, `rigs/ic7300.toml`/`rigs/ftx1.toml`); the `>2`-value
 *  `AttenuatorControl.svelte` branch is unwired (see PR body). */
export function getAttenuatorArmed(): ArmedFact<number> {
  const state = runtime.state;
  if (activeReceiverOrNull(state) === null) return { armed: false, value: null };
  return attenuatorArmed(getCommandLifecycles(), state, Date.now(), lifecycleSuperseded);
}

/** Data-mode armed fact (`set_data_mode`) — `ModePanel.svelte`'s DATA
 *  button/grid, distinct from `getModeArmed` above (a different intent
 *  entirely; a mode change and a data-mode change can be in flight at the
 *  same time and must not be conflated). */
export function getDataModeArmed(): ArmedFact<number> {
  const state = runtime.state;
  if (activeReceiverOrNull(state) === null) return { armed: false, value: null };
  return dataModeArmed(getCommandLifecycles(), state, Date.now(), lifecycleSuperseded);
}

/** Active DATA group's MOD-input source pending over the same lifecycle
 *  decision table, using its top-level readback rather than a receiver field. */
export function getModInputArmed(): ArmedFact<number> {
  const state = runtime.state;
  const rx = state?.active === 'SUB' ? state.sub : state?.main;
  const dataMode = rx?.dataMode;
  if (!Number.isSafeInteger(dataMode) || (dataMode as number) < 0 || (dataMode as number) > 3) {
    return { armed: false, value: null };
  }
  return modInputArmed(getCommandLifecycles(), state, Date.now(), lifecycleSuperseded);
}

/** Auto-notch armed fact (`set_auto_notch`). Notch mode is written as TWO
 *  independent boolean commands (`set_auto_notch`/`set_manual_notch`,
 *  `makeDspHandlers().onNotchModeChange`), never a single `notchMode`
 *  intent — combining them into one derived fact would be exactly the
 *  re-derivation the ARMED-SIGNAL CONTRACT forbids, so this stays two
 *  accessors, one per real command, same as the two real buttons
 *  (`DspPanel.svelte`'s NOTCH and A-NOTCH). */
export function getAutoNotchArmed(): ArmedFact<boolean> {
  const state = runtime.state;
  if (activeReceiverOrNull(state) === null) return { armed: false, value: null };
  return autoNotchArmed(getCommandLifecycles(), state, Date.now(), lifecycleSuperseded);
}

/** Manual-notch armed fact (`set_manual_notch`) — see `getAutoNotchArmed`'s
 *  doc comment for why this is a separate accessor, not a combined one. */
export function getManualNotchArmed(): ArmedFact<boolean> {
  const state = runtime.state;
  if (activeReceiverOrNull(state) === null) return { armed: false, value: null };
  return manualNotchArmed(getCommandLifecycles(), state, Date.now(), lifecycleSuperseded);
}

export function deriveAudioRoutingConfig() { return runtime.audioRouting; }
const _audioRoutingHandlers = makeAudioRoutingHandlers();
export function getAudioRoutingHandlers() { return _audioRoutingHandlers; }
const _vfoHandlers = makeVfoHandlers();
export function getVfoHandlers() { return _vfoHandlers; }

export type VfoTunerRead = Readonly<{
  tx: ReturnType<ManagedAppTxController['snapshot']>;
  view: ReturnType<typeof toRadioViewModel>;
}>;
export type VfoTunerContext = Readonly<{ read(): VfoTunerRead }>;

/** Captures the App TX facade once, while read() projects only live read-only facts. */
export function bindVfoTunerContext(): VfoTunerContext {
  const tx = getManagedAppTxController();
  return Object.freeze({
    read: () => {
      const snapshot = tx.snapshot();
      return Object.freeze({
        tx: snapshot,
        view: toRadioViewModel(runtime.state, runtime.caps, snapshot),
      });
    },
  });
}

// ── Audio Spectrum ──
export function deriveAudioSpectrumProps() {
  return toAudioSpectrumProps(runtime.state, runtime.caps);
}

// ── Memory Panel ──
export function deriveMemoryPanelProps() {
  return toMemoryPanelProps(runtime.state, runtime.caps);
}
const _memoryHandlers = makeMemoryHandlers();
export function getMemoryHandlers() { return _memoryHandlers; }

// ── Amber Telemetry ──
export function deriveAmberTelemetryProps() {
  return toAmberTelemetryProps(runtime.state);
}

// ── VFO Control Panel ──
export function deriveVfoControlProps() {
  return toVfoControlProps(runtime.state, runtime.caps);
}

// ── AmberScope (LCD skin) ──
// Bundles radio.current + capabilities reads for the AmberScope panel
// so it doesn't import `$lib/stores/*` directly. AmberScope has ~12
// `hasCapability(name)` checks; we expose the function on props rather
// than inflate the type with a dozen booleans. See audit Cluster B.
export interface AmberScopeProps {
  radioState: ServerState | null;
  caps: Capabilities | null;
  hasAudioFft: boolean;
  hasDualReceiver: boolean;
  hasCapability: (name: string) => boolean;
}

export function deriveAmberScopeProps(): AmberScopeProps {
  return {
    radioState: runtime.state,
    caps: runtime.caps,
    hasAudioFft: hasAudioFft(),
    hasDualReceiver: hasDualReceiver(),
    hasCapability,
  };
}

// ── AmberCockpit (LCD skin) ──
// Merge point for Cluster B (radio.current), Cluster A (capabilities), and
// Cluster D (qsy-history write) — see audit doc Batch 5. Same shape as
// AmberScope (~16 hasCapability(name) checks → expose the function rather
// than inflate the type). The qsy-history write happens via the handler
// bundle below (`onTuningChange`) so the panel never imports the qsy store.
export interface AmberCockpitProps {
  radioState: ServerState | null;
  caps: Capabilities | null;
  hasAudioFft: boolean;
  hasDualReceiver: boolean;
  hasCapability: (name: string) => boolean;
}

export function deriveAmberCockpitProps(): AmberCockpitProps {
  return {
    radioState: runtime.state,
    caps: runtime.caps,
    hasAudioFft: hasAudioFft(),
    hasDualReceiver: hasDualReceiver(),
    hasCapability,
  };
}

export interface AmberCockpitHandlers {
  /**
   * Record a tuning change into the QSY history ring buffer (#836). The
   * underlying store debounces internally — caller may invoke this from a
   * reactive `$effect` whenever active-receiver freq/mode changes.
   */
  onTuningChange: (freqHz: number, mode: string) => void;
}

const _amberCockpitHandlers: AmberCockpitHandlers = {
  onTuningChange: (freqHz: number, mode: string) => {
    if (freqHz > 0) recordQsy(freqHz, mode);
  },
};

export function getAmberCockpitHandlers(): AmberCockpitHandlers {
  return _amberCockpitHandlers;
}
