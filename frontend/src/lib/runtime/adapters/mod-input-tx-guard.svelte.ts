/**
 * MOD-input TX preflight guard (MOR-617, T3 of epic MOR-614).
 *
 * Root cause this guards against: when the active DATA group's MOD-input
 * source is not LAN, network voice TX from the web UI modulates from
 * MIC/ACC/USB instead of the network audio stream — open-mic feedback or
 * dead air. The guard is armed at the moment the web TX audio path starts
 * (see `tx-adapter.startTx`) and surfaces a non-blocking warning with a
 * one-click "Set LAN" fix that reuses the existing per-group SET command
 * (T1/MOR-615 backend, T2/MOR-616 helpers).
 *
 * Warn-only by design: TX is never blocked and the rig is never changed
 * silently — auto-set is a separate opt-in (T4/MOR-618).
 *
 * Visibility is derived reactively from live state, so the warning clears
 * as soon as the source becomes LAN (optimistic patch or readback) and
 * reappears if the radio rejects the change and readback reverts it. The
 * same gating as the ModePanel control applies (data_mode capability +
 * fieldStatus not missing), so radios without MOD-input routing never warn.
 */

import { getRadioState } from '$lib/stores/radio.svelte';
import { getCapabilities } from '$lib/stores/capabilities.svelte';
import { getFieldAvailability } from '$lib/state/field-status';
import {
  LAN_MOD_INPUT_SOURCE,
  modInputSourceLabel,
  modInputStateKey,
} from '$lib/radio/mod-input';
import { makeModeHandlers } from '../commands/panel-commands';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';

export interface ModInputTxGuardProps {
  /** True while the armed warning should be shown. */
  visible: boolean;
  /** Human label of the offending source (e.g. "MIC"); null when unknown. */
  sourceLabel: string | null;
}

/** Latched at TX start; cleared on dismiss or a clean TX start. */
let armed = $state(false);

/**
 * The active receiver group's MOD-input source when it warrants a warning,
 * or null when the guard must stay quiet: no state, no data_mode capability,
 * group not yet read (fieldStatus missing), source unknown (null) or
 * already LAN.
 */
function offendingSource(
  state: ServerState | null,
  caps: Capabilities | null,
): number | null {
  if (!state) return null;
  if (!(caps?.capabilities?.includes('data_mode') ?? false)) return null;
  const rx = state.active === 'SUB' ? state.sub : state.main;
  const key = modInputStateKey(rx?.dataMode ?? 0);
  if (getFieldAvailability(state, key) === 'missing') return null;
  const source = state[key] ?? null;
  if (source === null || source === LAN_MOD_INPUT_SOURCE) return null;
  return source;
}

/**
 * Evaluate the preflight at network-voice-TX start. Arms the warning when
 * the active group's source is known and not LAN; otherwise clears any
 * stale latch. Never blocks the TX path.
 */
export function armModInputTxGuard(): void {
  armed = offendingSource(getRadioState(), getCapabilities()) !== null;
}

/** User dismissed the warning — stay quiet until the next TX start. */
export function dismissModInputTxGuard(): void {
  armed = false;
}

/** Reactive props for the warning banner (call inside `$derived`). */
export function deriveModInputTxGuardProps(): ModInputTxGuardProps {
  const source = armed ? offendingSource(getRadioState(), getCapabilities()) : null;
  return {
    visible: source !== null,
    sourceLabel: modInputSourceLabel(source),
  };
}

// Reuse the existing ModePanel command path (MOR-616): optimistic patch of
// the active group's state key + the per-group SET command over WS.
const modeHandlers = makeModeHandlers();

const handlers = {
  /** One-click fix: route the active DATA group's MOD input to LAN. */
  onSetLan: () => {
    modeHandlers.onModInputChange(LAN_MOD_INPUT_SOURCE);
  },
  onDismiss: () => {
    dismissModInputTxGuard();
  },
};

export function getModInputTxGuardHandlers() {
  return handlers;
}
