/**
 * MOD-input TX preflight guard (MOR-617, T3 of epic MOR-614).
 *
 * When browser TX declares that its audio route requires LAN, the guard
 * warns if the active DATA group's MOD-input source does not match. It is
 * armed when the web TX audio path starts (see `tx-adapter.startTx`) and
 * offers a one-click "Set LAN" fix through the existing per-group SET
 * command (T1/MOR-615 backend, T2/MOR-616 helpers).
 *
 * Warn-only by design: TX is never blocked and the rig is never changed
 * silently — auto-set is a separate opt-in (T4/MOR-618).
 *
 * Visibility is derived reactively from live state, so the warning clears
 * as soon as the source becomes LAN (optimistic patch or readback) and
 * reappears if the radio rejects the change and readback reverts it. The
 * same compatibility gate as the ModePanel control applies (data_mode
 * capability + `isFieldRead`): it rejects the three explicit absence values
 * while preserving the legacy no-entry fallback.
 *
 * The projection decisions are pure and live in `./mod-input-gates.ts`;
 * this wrapper owns the `armed` latch, the live-state reads and the
 * one-click dispatch.
 */

import { getRadioState } from '$lib/stores/radio.svelte';
import { getCapabilities } from '$lib/stores/capabilities.svelte';
import { LAN_MOD_INPUT_SOURCE } from '$lib/radio/mod-input';
import { makeModeHandlers } from '../commands/panel-commands';
import { offendingSource, toModInputTxGuardProps } from './mod-input-gates';

export interface ModInputTxGuardProps {
  /** True while the armed warning should be shown. */
  visible: boolean;
  /** Human label of the offending source (e.g. "MIC"); null when unknown. */
  sourceLabel: string | null;
}

/** Latched at TX start; cleared on dismiss or a clean TX start. */
let armed = $state(false);

/**
 * Evaluate the preflight at browser-audio-TX start. Arms the warning when
 * the declared LAN requirement is not met; otherwise clears any stale latch.
 * Never blocks the TX path.
 */
export function armModInputTxGuard(): void {
  armed = offendingSource(getRadioState(), getCapabilities()) !== null;
}

/** User dismissed the warning — stay quiet until the next TX start. */
export function dismissModInputTxGuard(): void {
  armed = false;
}

/**
 * Reactive props for the warning banner (call inside `$derived`). Reads
 * the stores only while armed, so a disarmed banner does not depend on
 * radio state and does not recompute on every state update.
 */
export function deriveModInputTxGuardProps(): ModInputTxGuardProps {
  return armed
    ? toModInputTxGuardProps(true, getRadioState(), getCapabilities())
    : toModInputTxGuardProps(false, null, null);
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
