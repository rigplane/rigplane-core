/**
 * Pending-intent and armed decision table — the pure core split out of
 * `panel-adapters.ts`. Every function here is a function of its
 * arguments: the command lifecycle list, the server state (a captured
 * value for the armed folds, a lazily invoked reader for the pending
 * folds), a `nowMs` clock value, and a supersession predicate. No store
 * value, runtime singleton, or clock is read here; the `panel-adapters.ts`
 * wrappers own those reads. The exported
 * `getPending*`/`get*Armed` accessors and their doc comments stay in
 * `panel-adapters.ts`; these are their pure bodies.
 */

import type { CommandLifecycle } from '$lib/stores/commands.svelte';
import type { ServerState } from '$lib/types/state';
import { modInputCommand, modInputStateKey, type ModInputStateKey } from '$lib/radio/mod-input';

/** Lazy server-state reader; the wrapper passes `() => runtime.state`. */
export type ReadServerState = () => ServerState | null;
/** Supersession predicate over lifecycle records, injected by the wrapper. */
export type SupersededPredicate = (command: CommandLifecycle) => boolean;

/**
 * The receiver's confirmed (radio-observed) state slice, or `undefined`
 * while unobserved — `state.main`/`state.sub`, the same per-receiver
 * split `getPendingFrequencyHz`'s sibling accessors read.
 */
function confirmedReceiverState(
  state: ServerState | null, receiver: 0 | 1,
): ServerState['main'] | undefined {
  if (!state) return undefined;
  return receiver === 0 ? state.main : state.sub;
}

/**
 * Grace backstop — retire an
 * acknowledged-pending command this long after its ack even with no
 * observation of the commanded field. Covers the never-answered-at-all
 * classes the observation rule below cannot, because none of them ever
 * produce a post-ack read-back to end on: (1) post-ack execution
 * failure — the server acks `ok:true` at enqueue time, and a
 * later failure reaches only a session notification (`server.py`
 * `commandExecutionFailed`) that `ws-client.ts`'s `_emitCommandResult`
 * never maps back to this command id; (2) coalescing —
 * `_cmd_pending` keys a 50ms coalescing window by command NAME only, so a
 * superseded frame acks `ok:true`+`superseded` and never reaches the radio
 * at all; (3) link death after ack. Without this backstop, any of the three
 * leaves the marker claiming "in flight" forever. The clock itself is
 * not reactive — this only takes effect the next time something re-invokes
 * `latestPendingParam` with a fresh `nowMs` (the next state push, or any
 * other `$derived` recompute), not the instant the clock crosses the
 * threshold.
 */
const ACK_CONFIRM_GRACE_MS = 3_000;

/**
 * Shared by `getPendingFrequencyHz` (leg 1) and the four discrete
 * accessors (leg 2): the freshest command matching
 * `intentName`/`receiver` that has not reached a terminal failure/expiry
 * status, or `undefined`. Same `>=` freshest-wins tie-break as leg 1's
 * original implementation (review B3) — no second, parallel pending-state
 * path, and now one decision table for both legs instead of two.
 *
 * A live-bench finding: a transport `'acknowledged'` (the WS ack)
 * is NOT a confirming observation — it only proves the radio received the
 * command, typically within milliseconds, well before the next state poll
 * echoes the new value back (~500ms keep-alive, CLAUDE.md). Treating ack as
 * "no longer pending" (the pre-fix behavior) collapsed the italic pending
 * window to something imperceptible live, presenting an unconfirmed value
 * as confirmed.
 *
 * An acknowledged record ends on the commanded field's own post-ack
 * observation, whatever value that observation carries — the core
 * re-reads the written field at USER priority after every write
 * (`radio_poller.py: RadioPoller._request_post_write_readback`), and the
 * server publishes that read's own `lastObservedMonotonic` per field
 * (`runtime_helpers.py: _observed_field_status`). `ackFieldObservationTimes`
 * (`commands.svelte.ts: transition`) captures that marker at ack; a marker
 * strictly above it is the answer. Pinned by
 * `semantic-discrete-pending-wiring.component.test.ts`'s "clears the marker
 * when main.nb is re-observed after the ack still holding the old value"
 * and "does not clear the marker on a post-ack push that observed another
 * field".
 *
 * Without both markers (no boundary captured, or the field carries none)
 * there is no per-field evidence to read, and the record falls back to the
 * older rule: the `ackObservationSeq` sequence guard, then a direct
 * match against the current confirmed reading, bounded by the grace
 * backstop above.
 */
export function latestPendingParam(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate,
  intentName: string, paramKey: string, receiver: 0 | 1 | null,
  confirmedField: keyof ServerState['main'] | ModInputStateKey,
): unknown {
  let latest: {
    createdAt: number; value: unknown; status: string;
    updatedAt: number; ackObservationSeq: number | undefined;
    ackFieldObservationTimes: Readonly<Record<string, number>> | undefined;
  } | null = null;
  for (const command of commands) {
    if (command.name !== intentName) continue;
    if (receiver !== null && command.params.receiver !== receiver) continue;
    // Supersession is durable for the older record even after the newer
    // terminal record's bounded presentation retention expires. Never let a
    // superseded lifecycle become the newest selectable pending command.
    // Identity-less legacy/test-double records have no supersession key.
    if (command.id !== undefined && command.originalEpoch !== undefined
      && superseded(command)) continue;
    const value = command.params[paramKey];
    if (value === undefined) continue;
    if (!latest || command.createdAt >= latest.createdAt) {
      latest = {
        createdAt: command.createdAt, value, status: command.status,
        updatedAt: command.updatedAt, ackObservationSeq: command.ackObservationSeq,
        ackFieldObservationTimes: command.ackFieldObservationTimes,
      };
    }
  }
  // Select chronologically first, then inspect liveness. A newer terminal
  // same-key record is a barrier: filtering terminal records during the scan
  // would let an older superseded pending record resurface after the newer
  // command fails, times out, confirms, or is cancelled.
  if (!latest || (latest.status !== 'pending' && latest.status !== 'acknowledged')) return undefined;
  if (latest.status !== 'acknowledged') return latest.value;

  // Grace backstop: bounds the no-answer case (NAK, or nothing at all).
  if (nowMs - latest.updatedAt > ACK_CONFIRM_GRACE_MS) return undefined;

  const fieldPath = receiver === null
    ? String(confirmedField) : `${receiver === 1 ? 'sub' : 'main'}.${String(confirmedField)}`;
  const boundary = latest.ackFieldObservationTimes?.[fieldPath];
  const observedAt = readState()?.fieldStatus?.[fieldPath]?.lastObservedMonotonic;
  if (typeof boundary === 'number' && Number.isFinite(boundary)
    && typeof observedAt === 'number' && Number.isFinite(observedAt)) {
    return observedAt > boundary ? undefined : latest.value;
  }

  const ackObservationSeq = latest.ackObservationSeq;
  const currentObservationSeq = readState()?.observationSeq;
  if (ackObservationSeq !== undefined && currentObservationSeq !== undefined
    && currentObservationSeq <= ackObservationSeq) {
    return latest.value;
  }
  const confirmed = receiver === null
    ? readState()?.[confirmedField as ModInputStateKey]
    : confirmedReceiverState(readState(), receiver)?.[confirmedField as keyof ServerState['main']];
  return confirmed === latest.value ? undefined : latest.value;
}

export function pendingFrequencyHz(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): number | null {
  const value = latestPendingParam(commands, readState, nowMs, superseded, 'set_freq', 'freq', receiver, 'freqHz');
  return typeof value === 'number' ? value : null;
}

export function pendingFilterSelection(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): number | null {
  const value = latestPendingParam(commands, readState, nowMs, superseded, 'set_filter', 'filter', receiver, 'filter');
  return typeof value === 'number' ? value : null;
}

export function pendingPreampLevel(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): number | null {
  const value = latestPendingParam(commands, readState, nowMs, superseded, 'set_preamp', 'level', receiver, 'preamp');
  return typeof value === 'number' ? value : null;
}

export function pendingNbOn(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): boolean | null {
  const value = latestPendingParam(commands, readState, nowMs, superseded, 'set_nb', 'on', receiver, 'nb');
  return typeof value === 'boolean' ? value : null;
}

export function pendingNrOn(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): boolean | null {
  const value = latestPendingParam(commands, readState, nowMs, superseded, 'set_nr', 'on', receiver, 'nr');
  return typeof value === 'boolean' ? value : null;
}

export function pendingNarrow(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): boolean | null {
  const value = latestPendingParam(commands, readState, nowMs, superseded, 'set_narrow', 'on', receiver, 'narrow');
  return typeof value === 'boolean' ? value : null;
}

export function pendingNotchMode(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): 'off' | 'auto' | 'manual' | null {
  const auto = latestPendingParam(commands, readState, nowMs, superseded, 'set_auto_notch', 'on', receiver, 'autoNotch');
  const manual = latestPendingParam(commands, readState, nowMs, superseded, 'set_manual_notch', 'on', receiver, 'manualNotch');
  const autoBool = typeof auto === 'boolean' ? auto : null;
  const manualBool = typeof manual === 'boolean' ? manual : null;
  if (autoBool === true && manualBool === true) {
    return freshestNotchStrand(commands, superseded, receiver) === 'auto' ? 'auto' : 'manual';
  }
  if (autoBool === true) return 'auto';
  if (manualBool === true) return 'manual';
  if (autoBool === false && manualBool === false) return 'off';
  return null;
}

export function pendingManualNotchWidth(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): number | null {
  const value = latestPendingParam(commands, readState, nowMs, superseded, 'set_manual_notch_width', 'value', receiver, 'manualNotchWidth');
  return typeof value === 'number' ? value : null;
}

/** Which of the two notch strands holds the freshest non-terminal record. */
function freshestNotchStrand(
  commands: readonly CommandLifecycle[], superseded: SupersededPredicate, receiver: 0 | 1,
): 'auto' | 'manual' {
  let autoAt = Number.NEGATIVE_INFINITY;
  let manualAt = Number.NEGATIVE_INFINITY;
  for (const command of commands) {
    if (command.params.receiver !== receiver) continue;
    if (command.id !== undefined && command.originalEpoch !== undefined
      && superseded(command)) continue;
    if (typeof command.params.on !== 'boolean' || command.params.on !== true) continue;
    if (command.status !== 'pending' && command.status !== 'acknowledged') continue;
    if (command.name === 'set_auto_notch' && command.createdAt >= autoAt) autoAt = command.createdAt;
    else if (command.name === 'set_manual_notch' && command.createdAt >= manualAt) manualAt = command.createdAt;
  }
  return autoAt >= manualAt ? 'auto' : 'manual';
}

export function pendingRepeaterTone(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): 'off' | 'tone' | 'tsql' | null {
  const tsql = latestPendingParam(commands, readState, nowMs, superseded, 'set_repeater_tsql', 'on', receiver, 'repeaterTsql');
  const tone = latestPendingParam(commands, readState, nowMs, superseded, 'set_repeater_tone', 'on', receiver, 'repeaterTone');
  if (tone === false) return 'off';
  if (tsql === true) return 'tsql';
  if (tsql === false || tone === true) return 'tone';
  return null;
}

export function pendingRepeaterShift(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): number | null {
  const value = latestPendingParam(commands, readState, nowMs, superseded, 'set_repeater_shift', 'direction', receiver, 'repeaterShift');
  return typeof value === 'number' ? value : null;
}

export function pendingToneFreq(
  commands: readonly CommandLifecycle[], readState: ReadServerState,
  nowMs: number, superseded: SupersededPredicate, receiver: 0 | 1,
): number | null {
  const tone = latestPendingParam(commands, readState, nowMs, superseded, 'set_tone_freq', 'freq', receiver, 'toneFreq');
  if (typeof tone === 'number') return tone;
  const tsql = latestPendingParam(commands, readState, nowMs, superseded, 'set_tsql_freq', 'freq', receiver, 'tsqlFreq');
  return typeof tsql === 'number' ? tsql : null;
}

/**
 * ARMED-SIGNAL CONTRACT — owner ruling: any control that does not
 * switch in real time but is instead confirmed by polling (the radio's own
 * observed state, same as every accessor above) needs a generic "in flight"
 * marker, so an operator gets feedback the instant they act instead of the
 * multi-second silent lag this fixes (MODE buttons: click
 * → nothing visible → the poll eventually confirms). `ArmedFact`/`armedFact`
 * below are a GENERIC READ over `latestPendingParam`'s decision table above
 * — not a second source of truth, not a reimplementation. Every honesty rule
 * documented on `latestPendingParam` and `ACK_CONFIRM_GRACE_MS` applies
 * unchanged:
 *  - `armed` goes true the instant a command dispatches (`status ===
 *    'pending'`), stays true through the transport ack (`'acknowledged'`),
 *    and goes false when `latestPendingParam` releases the record.
 *  - A re-click while armed re-arms at the new target: `latestPendingParam`'s
 *    freshest-`createdAt`-wins tie-break already handles this, no separate
 *    "already armed" state to fight.
 *  - `armed` NEVER survives a terminal outcome. `latestPendingParam` first
 *    selects the newest same-key record chronologically, then requires that
 *    exact record to be pending/acknowledged. Thus a newer failed, cancelled,
 *    timed-out, or confirmed record clears armed immediately and cannot let an
 *    older superseded command resurface as if it were still in flight.
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
 *
 * KNOWN DUPLICATION: the `.sr-only` rule itself is copy-pasted
 * verbatim into all five consumer panels (`ModePanel.svelte`,
 * `AgcPanel.svelte`, `FilterPanel.svelte`, `RfFrontEnd.svelte`,
 * `DspPanel.svelte`) rather than living in one shared file, unlike the
 * `data-armed` visual rule above which was consolidated into
 * `control-button-armed.css`. Accepted, not an oversight: Svelte's
 * `<style>` scoping makes a genuinely shared `.sr-only` class awkward to
 * factor out (either an unscoped `:global()` leak, or a shared import every
 * panel needs just for one utility class) for a one-line rule unlikely to
 * drift. Left as-is; do not refactor without a concrete reason.
 */
export interface ArmedFact<T> {
  armed: boolean;
  /** The in-flight target while `armed`; `null` otherwise. Pending is
   *  display-only (leg-1 doctrine) — never read this as an arithmetic base
   *  for a "toggle from pending" computation. */
  value: T | null;
}

export function armedFact<T>(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
  intentName: string, paramKey: string, receiver: 0 | 1, confirmedField: keyof ServerState['main'],
): ArmedFact<T> {
  const value = latestPendingParam(commands, () => state, nowMs, superseded, intentName, paramKey, receiver, confirmedField);
  return value === undefined ? { armed: false, value: null } : { armed: true, value: value as T };
}

export function modeArmed(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
): ArmedFact<string> {
  if (!state) return { armed: false, value: null };
  const receiver: 0 | 1 = state.active === 'SUB' ? 1 : 0;
  return armedFact<string>(commands, state, nowMs, superseded, 'set_mode', 'mode', receiver, 'mode');
}

/**
 * Shared active-receiver split for the accessors below — the exact same
 * `state.active === 'SUB'` read `getModeArmed` performs inline, and
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
export function activeReceiverOrNull(state: ServerState | null): 0 | 1 | null {
  if (!state) return null;
  return state.active === 'SUB' ? 1 : 0;
}

export function agcArmed(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
): ArmedFact<number> {
  const receiver = activeReceiverOrNull(state);
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>(commands, state, nowMs, superseded, 'set_agc', 'mode', receiver, 'agc');
}

export function filterArmed(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
): ArmedFact<number> {
  const receiver = activeReceiverOrNull(state);
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>(commands, state, nowMs, superseded, 'set_filter', 'filter', receiver, 'filter');
}

export function filterShapeArmed(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
): ArmedFact<number> {
  const receiver = activeReceiverOrNull(state);
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>(commands, state, nowMs, superseded, 'set_filter_shape', 'shape', receiver, 'filterShape');
}

export function preampArmed(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
): ArmedFact<number> {
  const receiver = activeReceiverOrNull(state);
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>(commands, state, nowMs, superseded, 'set_preamp', 'level', receiver, 'preamp');
}

export function attenuatorArmed(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
): ArmedFact<number> {
  const receiver = activeReceiverOrNull(state);
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>(commands, state, nowMs, superseded, 'set_attenuator', 'db', receiver, 'att');
}

export function dataModeArmed(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
): ArmedFact<number> {
  const receiver = activeReceiverOrNull(state);
  if (receiver === null) return { armed: false, value: null };
  return armedFact<number>(commands, state, nowMs, superseded, 'set_data_mode', 'mode', receiver, 'dataMode');
}

export function modInputArmed(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
): ArmedFact<number> {
  const rx = state?.active === 'SUB' ? state.sub : state?.main;
  const dataMode = rx?.dataMode;
  if (!Number.isSafeInteger(dataMode) || (dataMode as number) < 0 || (dataMode as number) > 3) {
    return { armed: false, value: null };
  }
  const key = modInputStateKey(dataMode as number);
  const value = latestPendingParam(commands, () => state, nowMs, superseded, modInputCommand(dataMode as number), 'source', null, key);
  return typeof value === 'number' ? { armed: true, value } : { armed: false, value: null };
}

export function autoNotchArmed(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
): ArmedFact<boolean> {
  const receiver = activeReceiverOrNull(state);
  if (receiver === null) return { armed: false, value: null };
  return armedFact<boolean>(commands, state, nowMs, superseded, 'set_auto_notch', 'on', receiver, 'autoNotch');
}

export function manualNotchArmed(
  commands: readonly CommandLifecycle[], state: ServerState | null,
  nowMs: number, superseded: SupersededPredicate,
): ArmedFact<boolean> {
  const receiver = activeReceiverOrNull(state);
  if (receiver === null) return { armed: false, value: null };
  return armedFact<boolean>(commands, state, nowMs, superseded, 'set_manual_notch', 'on', receiver, 'manualNotch');
}
