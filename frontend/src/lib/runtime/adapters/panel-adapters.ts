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
 * reported symptom. Routed through `latestPendingParam` (below) — the same
 * decision table leg 2's four discrete accessors use — so a command now
 * stays pending through ack until the radio's own observed state confirms
 * `freqHz`, or the shared `ACK_CONFIRM_GRACE_MS` backstop elapses.
 */
export function getPendingFrequencyHz(receiver: 0 | 1): number | null {
  const value = latestPendingParam('set_freq', 'freq', receiver, 'freqHz');
  return typeof value === 'number' ? value : null;
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
 * Grace backstop (MOR-1488 review R2, timing revised R3) — retire an
 * acknowledged-pending command this long after its ack even with no
 * confirming observation. Covers the never-confirms-at-all classes the
 * sequence guard below cannot, because none of them ever produce a
 * confirming post-ack push to guard against: (1) MOR-1445 post-ack
 * execution failure — the server acks `ok:true` at enqueue time, and a
 * later failure reaches only a session notification (`server.py`
 * `commandExecutionFailed`) that `ws-client.ts`'s `_emitCommandResult`
 * never maps back to this command id; (2) MOR-1427 coalescing —
 * `_cmd_pending` keys a 50ms coalescing window by command NAME only, so a
 * superseded frame acks `ok:true`+`superseded` and never reaches the radio
 * at all; (3) link death after ack. Without this backstop, any of the three
 * leaves the marker claiming "in flight" forever. `Date.now()` itself is
 * not reactive — this only takes effect the next time something re-invokes
 * `latestPendingParam` (the next state push, or any other `$derived`
 * recompute), not the instant the clock crosses the threshold.
 *
 * 2000ms (R3, was 1500ms): review R3 found the commanded field's own
 * confirming re-read is a full poll round-robin away, not the next state
 * push — `_state_queries.py` schedules per-field reads across the
 * round-robin and `radio_poller.py:529-531` cycles roughly 25 queries at
 * ~25ms apiece, so worst case is ~1.3s for one full rotation. 1500ms left
 * too little margin: a command acked just after its field's slot in the
 * rotation could retire on the grace backstop moments before the actual
 * confirming readback arrives. 2000ms budgets a full rotation (≈1.3s) plus
 * headroom for scheduling jitter, matching the sequence guard below's
 * "mismatch is not evidence of failure, only of not-yet-observed" doctrine.
 *
 * 3000ms (MOR-1478): leg 2's ~1.3s round-robin is not the binding
 * constraint once leg 1 shares this table — `tuning-accumulator.ts:6,44`
 * records the observed `set_freq` confirm round trip at 0.5–2s on live
 * hardware, so a 2000ms budget expires exactly at the documented worst
 * case and drops the readout back to the stale pre-spin value for the
 * remainder — the MOR-1478 symptom itself. 3000ms keeps ~50% headroom
 * over the slowest documented confirm, matching the margin leg 2's own
 * 2000ms held over its 1.3s rotation.
 */
const ACK_CONFIRM_GRACE_MS = 3_000;

/**
 * Shared by `getPendingFrequencyHz` above (leg 1, MOR-1478) and the four
 * discrete accessors below (leg 2, MOR-1488): the freshest command matching
 * `intentName`/`receiver` that has not reached a terminal failure/expiry
 * status, or `undefined`. Same `>=` freshest-wins tie-break as leg 1's
 * original implementation (review B3) — no second, parallel pending-state
 * path, and now one decision table for both legs instead of two.
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
 * ack-time value.
 *
 * MOR-1488 review R3 (asymmetric settle, revises R2's "either way"):
 * `observationSeq` bumps on EVERY applied field observation — a 25ms meter
 * poll (`core.state_store._apply_one`) advances it exactly as much as a
 * genuine re-read of THIS command's field. But the commanded field's own
 * confirming re-read is scheduled a full poll round-robin away
 * (`_state_queries.py`, `radio_poller.py:529-531`, ~25 queries at ~25ms —
 * up to ~1.3s worst case), not on the very next push. R2 retired the
 * record on the first post-ack push regardless of match, which fires
 * ~50ms after ack (the next unrelated meter poll) — collapsing the
 * pending window back to a few frames, the exact symptom this PR set out
 * to fix. So as of R3: a post-ack push whose confirmed field MATCHES the
 * target is a real confirmation and clears the record (leg-1 "pending is
 * display-only, confirmed reading stays the group's sole selection source"
 * doctrine). A post-ack push that does NOT match is NOT evidence the value
 * failed to take — it is far more likely an unrelated field's observation
 * that simply hasn't reached this one's round-robin slot yet — so the
 * record stays pending and is left to the grace backstop above to bound.
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
  if (ackObservationSeq !== undefined && currentObservationSeq !== undefined
    && currentObservationSeq <= ackObservationSeq) {
    // No push observed since ack yet — stay pending regardless of any
    // coincidental match against the (necessarily stale) current snapshot.
    return latest.value;
  }
  // Either the guard has no sequencing data to work with (fall back to a
  // direct match, pre-R2 behavior) or a post-ack push has arrived: either
  // way, only a MATCH settles the record (R3) — a mismatch here is not
  // evidence the value failed to take (the commanded field's own
  // confirming re-read is a full round-robin away, see doc comment above),
  // so it stays pending for the grace backstop to bound instead.
  return confirmedReceiverState(receiver)?.[confirmedField] === latest.value ? undefined : latest.value;
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

// ── Generic armed/pending signal (MOR-1519) ──
/**
 * ARMED-SIGNAL CONTRACT — owner ruling MOR-1519: any control that does not
 * switch in real time but is instead confirmed by polling (the radio's own
 * observed state, same as every accessor above) needs a generic "in flight"
 * marker, so an operator gets feedback the instant they act instead of the
 * multi-second silent lag the ticket was filed against (MODE buttons: click
 * → nothing visible → the poll eventually confirms). `ArmedFact`/`armedFact`
 * below are a GENERIC READ over `latestPendingParam`'s decision table above
 * — not a second source of truth, not a reimplementation. Every honesty rule
 * documented on `latestPendingParam` and `ACK_CONFIRM_GRACE_MS` applies
 * unchanged:
 *  - `armed` goes true the instant a command dispatches (`status ===
 *    'pending'`) and stays true through the transport ack (`'acknowledged'`)
 *    until a confirming post-ack observation of the target value arrives, or
 *    `ACK_CONFIRM_GRACE_MS` elapses since ack with no confirmation.
 *  - A re-click while armed re-arms at the new target: `latestPendingParam`'s
 *    freshest-`createdAt`-wins tie-break already handles this, no separate
 *    "already armed" state to fight.
 *  - `armed` NEVER survives a terminal failure. A command that reaches
 *    `'failed'`/`'cancelled'`/`'timed-out'` fails `latestPendingParam`'s own
 *    `status !== 'pending' && status !== 'acknowledged'` scan guard and is
 *    excluded from consideration the instant it transitions — `armed` clears
 *    immediately, it must never present a failed command as still in flight.
 *
 * Contract for skins consuming this signal:
 *  - MAY style `armed` however fits: desktop-v2 renders it as a `data-armed`
 *    attribute on the actual `<button>` element (`ControlButton.svelte`,
 *    parity with `DspSurface.svelte`'s `data-pending-status` marker for
 *    NB/NR) with `opacity: 0.75` as the primary, font-independent visual
 *    channel plus an underline structural backstop (`ModePanel.svelte`); an
 *    LCD skin may prefer a glow or blink — that is a presentation choice,
 *    not part of this contract. NOTE (review F1/F2): the marker MUST sit on
 *    the actual interactive element, never a wrapper — a wrapper is not
 *    reachable by an attribute selector, and relying on CSS inheritance
 *    (e.g. `font-style`) is unsafe: the UA `<button>` stylesheet supplies
 *    its own `font-style: normal` that beats an inherited value, and this
 *    codebase's vendored font + `font-synthesis: none` (`app.css`) means an
 *    italic-only affordance can compute without ever rendering — verify any
 *    font-dependent channel AS RENDERED, not just as computed.
 *  - MUST NOT suppress the confirmed-vs-armed distinction, and MUST NOT ever
 *    present an armed (unconfirmed) value as confirmed — same doctrine as
 *    `data-freq-status='pending'` (`FrequencyDisplayInteractive.svelte`) and
 *    `data-pending-status='pending'` (`DspSurface.svelte`): a structural
 *    marker on the element, never a color-only tell.
 *  - `data-*` carries NO accessibility semantics on its own (review F3) — it
 *    is a hook for CSS and tests, not for assistive tech. A skin exposing
 *    `armed` MUST also pair the marked control with an `aria-describedby`
 *    announcement (a `.sr-only` element, same pattern as
 *    `DspSurface.svelte`'s pending-toggle announcement) so AT users get the
 *    same "still in flight" information sighted users get from the visual
 *    channel. `ModePanel.svelte` does this via `HardwareButton`'s
 *    `describedBy` prop.
 */
export interface ArmedFact<T> {
  /** True from command dispatch until a confirming observation (or grace
   *  expiry) clears the pending record — see the contract above. */
  armed: boolean;
  /** The in-flight target while `armed`; `null` otherwise. Pending is
   *  display-only (leg-1 doctrine) — never read this as an arithmetic base
   *  for a "toggle from pending" computation. */
  value: T | null;
}

function armedFact<T>(
  intentName: string, paramKey: string, receiver: 0 | 1, confirmedField: keyof ServerState['main'],
): ArmedFact<T> {
  const value = latestPendingParam(intentName, paramKey, receiver, confirmedField);
  return value === undefined ? { armed: false, value: null } : { armed: true, value: value as T };
}

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
  const receiver: 0 | 1 = state.active === 'SUB' ? 1 : 0;
  return armedFact<string>('set_mode', 'mode', receiver, 'mode');
}

// ── Armed-signal adoption long tail (MOR-1536) ──
/**
 * Shared active-receiver split for the accessors below — the exact same
 * `state.active === 'SUB'` read `getModeArmed` above performs inline, and
 * the same one every `activeRx(state)`-based prop mapper in `panel-props.ts`
 * uses for AGC/filter/preamp/attenuator/data-mode/notch. `null` only when
 * there is no state yet (cold start).
 *
 * KNOWN BOUND (same class `getModeArmed` already documents): a handler that
 * targets a receiver other than `state.active` at dispatch time (the
 * focus-echo window, `consumePendingFocus()`) makes an accessor built on
 * this helper degrade to no-feedback for that one click. None of the
 * handlers this helper serves (`onAgcModeChange`, `onFilterChange`,
 * `onPreChange`, `onAttChange`, `onDataModeChange`, `onNotchModeChange`)
 * consume the focus-echo pending target the way `onModeChange` does, so in
 * practice they are not exposed to it today — noted for completeness, not
 * because a live gap is known.
 */
function activeReceiverOrNull(): 0 | 1 | null {
  const state = runtime.state;
  if (!state) return null;
  return state.active === 'SUB' ? 1 : 0;
}

/** AGC mode buttons' armed fact (`set_agc`). No `receiver` param — same
 *  single-active-receiver read `toAgcProps`'s `activeRx(state)` uses. */
export function getAgcArmed(): ArmedFact<number> {
  const receiver = activeReceiverOrNull();
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>('set_agc', 'mode', receiver, 'agc');
}

/** Filter-select (FIL1/2/3) armed fact (`set_filter`). This is the exact
 *  same `latestPendingParam('set_filter', 'filter', receiver, 'filter')`
 *  call `getPendingFilterSelection(receiver)` above already makes — this
 *  wrapper only supplies the no-receiver-arg, `ArmedFact`-shaped read
 *  `FilterPanel.svelte` needs (parity with `getModeArmed`'s shape), not a
 *  second implementation. */
export function getFilterArmed(): ArmedFact<number> {
  const receiver = activeReceiverOrNull();
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>('set_filter', 'filter', receiver, 'filter');
}

/** Preamp-level armed fact (`set_preamp`). Same underlying primitive as
 *  `getPendingPreampLevel(receiver)` above, `ArmedFact`-shaped. */
export function getPreampArmed(): ArmedFact<number> {
  const receiver = activeReceiverOrNull();
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>('set_preamp', 'level', receiver, 'preamp');
}

/** Attenuator armed fact (`set_attenuator`, param `db`, confirmed field
 *  `att`) — `makeRfFrontEndHandlers().onAttChange` dispatches `db`, not
 *  `level`. Wired only for `RfFrontEnd.svelte`'s 2-value HardwareButton
 *  toggle (the live-bench IC-7300/FTX-1 shape, both `attValues.length ===
 *  2`, `rigs/ic7300.toml`/`rigs/ftx1.toml`); the `>2`-value
 *  `AttenuatorControl.svelte` branch is unwired (see PR body). */
export function getAttenuatorArmed(): ArmedFact<number> {
  const receiver = activeReceiverOrNull();
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>('set_attenuator', 'db', receiver, 'att');
}

/** Data-mode armed fact (`set_data_mode`) — `ModePanel.svelte`'s DATA
 *  button/grid, distinct from `getModeArmed` above (a different intent
 *  entirely; a mode change and a data-mode change can be in flight at the
 *  same time and must not be conflated). */
export function getDataModeArmed(): ArmedFact<number> {
  const receiver = activeReceiverOrNull();
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>('set_data_mode', 'mode', receiver, 'dataMode');
}

/** Auto-notch armed fact (`set_auto_notch`). Notch mode is written as TWO
 *  independent boolean commands (`set_auto_notch`/`set_manual_notch`,
 *  `makeDspHandlers().onNotchModeChange`), never a single `notchMode`
 *  intent — combining them into one derived fact would be exactly the
 *  re-derivation the ARMED-SIGNAL CONTRACT forbids, so this stays two
 *  accessors, one per real command, same as the two real buttons
 *  (`DspPanel.svelte`'s NOTCH and A-NOTCH). */
export function getAutoNotchArmed(): ArmedFact<boolean> {
  const receiver = activeReceiverOrNull();
  if (receiver === null) return { armed: false, value: null };
  return armedFact<boolean>('set_auto_notch', 'on', receiver, 'autoNotch');
}

/** Manual-notch armed fact (`set_manual_notch`) — see `getAutoNotchArmed`'s
 *  doc comment for why this is a separate accessor, not a combined one. */
export function getManualNotchArmed(): ArmedFact<boolean> {
  const receiver = activeReceiverOrNull();
  if (receiver === null) return { armed: false, value: null };
  return armedFact<boolean>('set_manual_notch', 'on', receiver, 'manualNotch');
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
