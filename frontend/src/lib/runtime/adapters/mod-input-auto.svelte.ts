/**
 * Opt-in auto LAN MOD-input for network voice TX (MOR-618, T4 of epic MOR-614).
 *
 * OFF by default — the default UX stays the MOR-617 warn + one-click guard.
 * When the user opts in (TX panel settings, persisted in localStorage):
 *
 *   - TX start (`tx-adapter.startTx`, before the MOR-617 guard arms): if the
 *     active DATA group's MOD-input source is known and != LAN(5), remember
 *     it, then set LAN through the same per-group SET command + optimistic
 *     patch as the ModePanel control (T1/MOR-615 backend, T2/MOR-616
 *     helpers). The optimistic LAN patch preempts the MOR-617 warning.
 *   - after a field-specific authoritative PTT-off confirmation, restore the
 *     remembered source only if the group is still on LAN (a manual mid-TX
 *     change wins).
 *
 * The pending transaction is intentionally memory-only. A disconnect,
 * reload, or cached state cannot authorize MOD restoration.
 *
 * This module never touches the audio byte path.
 */

import { getRadioState, patchRadioState } from '$lib/stores/radio.svelte';
import { getCapabilities } from '$lib/stores/capabilities.svelte';
import { getFieldAvailability } from '$lib/state/field-status';
import {
  LAN_MOD_INPUT_SOURCE,
  modInputCommand,
  modInputStateKey,
  type ModInputCommand,
  type ModInputStateKey,
} from '$lib/radio/mod-input';
import { sendCommand } from '$lib/transport/ws-client';
import type { ServerState } from '$lib/types/state';

/** localStorage key of the opt-in preference ('true' / 'false'). */
export const AUTO_LAN_PREF_KEY = 'rigplane:auto-lan-mod-input';

/** Legacy localStorage key retained only for safe migration cleanup. */
export const PENDING_RESTORE_KEY = 'rigplane:mod-input-tx-restore:v1';

interface PendingRestore {
  command: ModInputCommand;
  key: ModInputStateKey;
  source: number;
}

/* ── Preference (opt-in, default OFF) ────────────────────────────── */

function readStoredPref(): boolean {
  if (typeof localStorage === 'undefined') return false;
  try {
    return localStorage.getItem(AUTO_LAN_PREF_KEY) === 'true';
  } catch {
    return false;
  }
}

let enabled = $state(readStoredPref());

export function isAutoLanModInputEnabled(): boolean {
  return enabled;
}

export function setAutoLanModInputEnabled(on: boolean): void {
  enabled = on;
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(AUTO_LAN_PREF_KEY, on ? 'true' : 'false');
  } catch {
    /* ignore */
  }
}

export interface AutoLanModInputProps {
  /** Show the toggle: data_mode capability + active group observed. */
  available: boolean;
  /** Current opt-in value. */
  enabled: boolean;
}

/** Reactive props for the settings toggle (call inside `$derived`). */
export function deriveAutoLanModInputProps(): AutoLanModInputProps {
  const state = getRadioState();
  const caps = getCapabilities();
  const key = modInputStateKey(activeDataMode(state));
  const available =
    state !== null &&
    (caps?.capabilities?.includes('data_mode') ?? false) &&
    getFieldAvailability(state, key) !== 'missing';
  return { available, enabled };
}

/* ── Pending restore (memory only) ───────────────────────────────── */

/** Set while a web TX keyed by auto-set is in flight; owns the restore. */
let pending: PendingRestore | null = null;

function clearPersistedPending(): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.removeItem(PENDING_RESTORE_KEY);
  } catch {
    /* ignore */
  }
}

/* ── Auto-set / restore engine ───────────────────────────────────── */

function activeDataMode(state: ServerState | null): number {
  const rx = state?.active === 'SUB' ? state.sub : state?.main;
  return rx?.dataMode ?? 0;
}

/**
 * Opt-in TX-start hook (called by `tx-adapter.startTx` BEFORE the MOR-617
 * guard arms, so the optimistic LAN patch keeps the warning quiet).
 * Same quiet-gating as the guard: no state, no data_mode capability, group
 * not read, source unknown, or already LAN → no change, no pending.
 */
export function autoSetLanModInputForTx(): void {
  if (!enabled) return;
  const state = getRadioState();
  if (!state) return;
  const caps = getCapabilities();
  if (!(caps?.capabilities?.includes('data_mode') ?? false)) return;
  const dataMode = activeDataMode(state);
  const key = modInputStateKey(dataMode);
  if (getFieldAvailability(state, key) === 'missing') return;
  const source = state[key] ?? null;
  if (source === null || source === LAN_MOD_INPUT_SOURCE) return;

  pending = { command: modInputCommand(dataMode), key, source };
  // Same optimistic-patch + per-group SET path as the ModePanel control
  // (MOR-616); the backend confirms via write-through readback (MOR-615).
  patchRadioState({ [key]: LAN_MOD_INPUT_SOURCE } as Partial<ServerState>);
  sendCommand(pending.command, { source: LAN_MOD_INPUT_SOURCE });
}

/**
 * Confirmed-off hook. One-shot: consumes the pending restore whether or not a
 * command is sent. Skips the SET when the group is known to be off LAN already.
 */
export function restoreModInputAfterTx(): void {
  const p = pending;
  pending = null;
  clearPersistedPending();
  if (!p) return;
  const current = getRadioState()?.[p.key] ?? null;
  if (current !== null && current !== LAN_MOD_INPUT_SOURCE) return;
  patchRadioState({ [p.key]: p.source } as Partial<ServerState>);
  sendCommand(p.command, { source: p.source });
}

/**
 * Remove pre-authority-gate restore records from older frontend versions.
 * This migration never inspects cached radio state and never mutates the radio.
 */
export function clearLegacyPendingModInputRestore(): void {
  clearPersistedPending();
}
