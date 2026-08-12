/**
 * Panel adapters — derive props and handlers for self-wiring panels.
 *
 * Each panel calls its derive function inside $derived() and its
 * handler function once at init. This replaces prop-passing from sidebars.
 *
 * Add new panel adapters here as panels are migrated to self-wiring.
 */

import { runtime } from '../frontend-runtime';
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
  makeKeyboardHandlers, makeSystemHandlers,
} from '../commands/panel-commands';
import { toRadioViewModel } from './radio-view-model-adapter';
import { getAppTxController, type AppTxController } from '../tx-controller/app-host';
import {
  hasAudioFft, hasDualReceiver, hasCapability,
} from '$lib/stores/capabilities.svelte';
import { recordQsy } from './qsy-history-adapter';
import { getCommandLifecycles } from '$lib/stores/commands.svelte';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';

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

// ── Pending frequency target (MOR-1441) ──
/**
 * The freshest still-in-flight `set_freq` target for `receiver` (`0` =
 * MAIN, `1` = SUB — the wire encoding `dispatchRadioIntent` uses), or
 * `null` when no `set_freq` intent is currently pending for it.
 *
 * This is the pending target the MOR-1425 tuning accumulator races toward
 * during a hot burst — read off the command-bus lifecycle list rather than
 * a second, parallel path into the accumulator's own internal (non-
 * reactive) map. `getCommandLifecycles()` is already the accumulator's own
 * echo/expiry authority: an ack, failure, cancellation, or timeout are
 * exactly the events that end a command's `'pending'` status, so a caller
 * reading this inside `$derived()` gets the snap-back-to-confirmed behavior
 * for free, with no separate polling or expiry timer to maintain.
 */
export function getPendingFrequencyHz(receiver: 0 | 1): number | null {
  let latest: { createdAt: number; freq: number } | null = null;
  for (const command of getCommandLifecycles()) {
    if (command.name !== 'set_freq' || command.status !== 'pending') continue;
    if (command.params.receiver !== receiver) continue;
    const freq = command.params.freq;
    if (typeof freq !== 'number') continue;
    // `>=`, not `>`: `getCommandLifecycles()` is in dispatch (array) order, so
    // on a same-millisecond `createdAt` tie the LATER entry in that order is
    // the actually-freshest one — `>` would freeze on the earlier of the two.
    if (!latest || command.createdAt >= latest.createdAt) latest = { createdAt: command.createdAt, freq };
  }
  return latest?.freq ?? null;
}

// ── Pending discrete-control targets (MOR-1441 leg 2) ──
/**
 * The receiver's confirmed (radio-observed) state slice, or `undefined`
 * while unobserved — `runtime.state.main`/`.sub`, the same per-receiver
 * split `getPendingFrequencyHz`'s sibling accessors read.
 */
function confirmedReceiverState(receiver: 0 | 1): ServerState['main'] | undefined {
  const state = runtime.state;
  if (!state) return undefined;
  return receiver === 0 ? state.main : state.sub;
}

/**
 * Grace backstop (MOR-1488 review R2) — retire an acknowledged-pending
 * command this long after its ack even with no confirming observation.
 * Covers the never-confirms-at-all classes the sequence guard below cannot,
 * because none of them ever produce a post-ack state push to guard against:
 * (1) MOR-1445 post-ack execution failure — the server acks `ok:true` at
 *     enqueue time, and a later failure reaches only a session notification
 *     (`server.py` `commandExecutionFailed`) that `ws-client.ts`'s
 *     `_emitCommandResult` never maps back to this command id; (2) MOR-1427
 *     coalescing — `_cmd_pending` keys a 50ms coalescing window by command
 *     NAME only, so a superseded frame acks `ok:true`+`superseded` and never
 *     reaches the radio at all; (3) link death after ack. Without this
 *     backstop, any of the three leaves the marker claiming "in flight"
 *     forever. `Date.now()` itself is not reactive — this only takes effect
 *     the next time something re-invokes `latestPendingParam` (the next
 *     state push, or any other `$derived` recompute), not the instant the
 *     clock crosses the threshold.
 */
const ACK_CONFIRM_GRACE_MS = 1_500;

/**
 * Shared by the four accessors below: the freshest command matching
 * `intentName`/`receiver` that has not reached a terminal failure/expiry
 * status, or `undefined`. Same authority and `>=` freshest-wins tie-break
 * as `getPendingFrequencyHz` above (leg 1, review B3) — no second, parallel
 * pending-state path.
 *
 * MOR-1488 (live-bench finding): a transport `'acknowledged'` (the WS ack)
 * is NOT a confirming observation — it only proves the radio received the
 * command, typically within milliseconds, well before the next state poll
 * echoes the new value back (~500ms keep-alive, CLAUDE.md). Treating ack as
 * "no longer pending" (the pre-fix behavior) collapsed the italic pending
 * window to something imperceptible live, presenting an unconfirmed value
 * as confirmed.
 *
 * MOR-1488 review R2 (sequence guard, closes F2): matching the CURRENT
 * confirmed snapshot at ack time is not enough on its own — that snapshot
 * can predate the command entirely. A fast double-toggle (confirmed
 * nb:false → click ON → click OFF before either is observed) acks the OFF
 * command while the receiver state still reflects the value from BEFORE
 * both clicks; OFF's target (false) happens to equal that stale snapshot,
 * so a plain match would clear the marker immediately even though nothing
 * has actually been re-observed since. `command.ackObservationSeq`
 * (`commands.svelte.ts`, captured the instant a command reaches
 * 'acknowledged') fixes this: the command stays pending until the runtime's
 * current `observationSeq` (`$lib/stores/radio.svelte` — the one counter
 * that increments on every applied state push regardless of whether any
 * field's value actually changed; see `ackObservationSeq`'s own doc comment
 * for why `stateRevision` cannot serve this role) has advanced PAST the
 * ack-time value. The FIRST post-ack push settles the record either way and
 * it is no longer read as pending: if the confirmed field now matches the
 * target, the command is excluded (leg-1 "pending is display-only,
 * confirmed reading stays the group's sole selection source" doctrine); if
 * it still does not match, the command is ALSO excluded — the value did not
 * take, and continuing to claim "in flight" past that point would itself be
 * a fabrication. This bounds the pending window to exactly one poll cycle
 * past ack.
 *
 * When no `ackObservationSeq` was captured (no radio state had ever been
 * observed at ack time — a real gap only in cold-start/test-double
 * scenarios) or the runtime currently has no observed state either, the
 * sequence guard has nothing to compare against and falls back to a direct
 * match against the current confirmed reading (the pre-R2 behavior).
 *
 * The match itself intentionally reads the receiver's plain schema value
 * (`confirmedReceiverState(receiver)?.[confirmedField]`), not `fieldStatus`
 * freshness — a field that has never been observed at all reads as
 * `undefined` here, which never `===`-matches a real target value, so an
 * unobserved field is correctly treated as "not yet confirmed" rather than
 * silently matching.
 */
function latestPendingParam(
  intentName: string, paramKey: string, receiver: 0 | 1, confirmedField: keyof ServerState['main'],
): unknown {
  let latest: {
    createdAt: number; value: unknown; status: string;
    updatedAt: number; ackObservationSeq: number | undefined;
  } | null = null;
  for (const command of getCommandLifecycles()) {
    if (command.name !== intentName) continue;
    if (command.status !== 'pending' && command.status !== 'acknowledged') continue;
    if (command.params.receiver !== receiver) continue;
    const value = command.params[paramKey];
    if (value === undefined) continue;
    if (!latest || command.createdAt >= latest.createdAt) {
      latest = {
        createdAt: command.createdAt, value, status: command.status,
        updatedAt: command.updatedAt, ackObservationSeq: command.ackObservationSeq,
      };
    }
  }
  if (!latest) return undefined;
  if (latest.status !== 'acknowledged') return latest.value;

  // Grace backstop: fires regardless of what the sequence guard below would
  // otherwise decide — see the constant's own doc comment.
  if (Date.now() - latest.updatedAt > ACK_CONFIRM_GRACE_MS) return undefined;

  const ackObservationSeq = latest.ackObservationSeq;
  const currentObservationSeq = runtime.state?.observationSeq;
  if (ackObservationSeq === undefined || currentObservationSeq === undefined) {
    // No sequencing data to guard with — fall back to a direct match.
    return confirmedReceiverState(receiver)?.[confirmedField] === latest.value ? undefined : latest.value;
  }
  if (currentObservationSeq <= ackObservationSeq) {
    // No push observed since ack yet — stay pending regardless of any
    // coincidental match against the (necessarily stale) current snapshot.
    return latest.value;
  }
  // First post-ack push: settle the record either way (see doc comment).
  return undefined;
}

/** Freshest unconfirmed `set_filter` target for `receiver`, or `null`.
 *  `FilterSurface` renders this as a marker on the targeted choice only —
 *  the confirmed reading stays the group's sole selection source (leg-1
 *  lesson: pending is display-only). */
export function getPendingFilterSelection(receiver: 0 | 1): number | null {
  const value = latestPendingParam('set_filter', 'filter', receiver, 'filter');
  return typeof value === 'number' ? value : null;
}

/** Freshest unconfirmed `set_preamp` target for `receiver`, or `null`. Never
 *  touches the MOR-1447 combined-knob/change-guard machinery, which reads
 *  only confirmed fields. */
export function getPendingPreampLevel(receiver: 0 | 1): number | null {
  const value = latestPendingParam('set_preamp', 'level', receiver, 'preamp');
  return typeof value === 'number' ? value : null;
}

/** Freshest unconfirmed `set_nb` target for `receiver`, or `null`. */
export function getPendingNbOn(receiver: 0 | 1): boolean | null {
  const value = latestPendingParam('set_nb', 'on', receiver, 'nb');
  return typeof value === 'boolean' ? value : null;
}

/** Freshest unconfirmed `set_nr` target for `receiver`, or `null`. */
export function getPendingNrOn(receiver: 0 | 1): boolean | null {
  const value = latestPendingParam('set_nr', 'on', receiver, 'nr');
  return typeof value === 'boolean' ? value : null;
}

const _audioRoutingHandlers = makeAudioRoutingHandlers();
export function getAudioRoutingHandlers() { return _audioRoutingHandlers; }
const _vfoHandlers = makeVfoHandlers();
export function getVfoHandlers() { return _vfoHandlers; }

export type VfoTunerRead = Readonly<{
  tx: ReturnType<AppTxController['snapshot']>;
  view: ReturnType<typeof toRadioViewModel>;
}>;
export type VfoTunerContext = Readonly<{ read(): VfoTunerRead }>;

/** Captures the App TX facade once, while read() projects only live read-only facts. */
export function bindVfoTunerContext(): VfoTunerContext {
  const tx = getAppTxController();
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
