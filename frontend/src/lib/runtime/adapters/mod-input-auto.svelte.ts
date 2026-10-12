/**
 * Opt-in auto LAN MOD-input for network voice TX (MOR-618, T4 of epic MOR-614).
 *
 * OFF by default — the default UX stays the MOR-617 warn + one-click guard.
 * When the user opts in (TX panel settings, persisted in localStorage):
 *
 *   - TX start (`tx-adapter.startTx`, before the MOR-617 guard arms): if the
 *     active DATA group's MOD-input source is known and != LAN(5), remember
 *     it, then dispatch the per-group SET through the typed intent facade
 *     (T1/MOR-615 backend, T2/MOR-616 helpers). The Store is never written
 *     optimistically (MOR-1409 A08): the MOR-617 warning stays armed
 *     truthfully until provider readback confirms LAN.
 *   - after a field-specific authoritative PTT-off confirmation, restore the
 *     remembered source only if the group is still on LAN (a manual mid-TX
 *     change wins).
 *
 * The pending transaction is intentionally memory-only. A disconnect,
 * reload, or cached state cannot authorize MOD restoration.
 *
 * The gating decisions are pure and live in `./mod-input-gates.ts`; this
 * wrapper owns the opt-in flag, the pending latch, `localStorage` and the
 * dispatch. This module never touches the audio byte path.
 */

import { getRadioState } from '$lib/stores/radio.svelte';
import { getCapabilities } from '$lib/stores/capabilities.svelte';
import { LAN_MOD_INPUT_SOURCE } from '$lib/radio/mod-input';
import { dispatchRadioIntent } from '../commands/radio-intents';
import {
  autoLanModInputAvailable,
  planAutoLanSet,
  restoreTarget,
  type ModInputSetPlan,
} from './mod-input-gates';

/** localStorage key of the opt-in preference ('true' / 'false'). */
export const AUTO_LAN_PREF_KEY = 'rigplane:auto-lan-mod-input';

/** Legacy localStorage key retained only for safe migration cleanup. */
export const PENDING_RESTORE_KEY = 'rigplane:mod-input-tx-restore:v1';

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
  const available = autoLanModInputAvailable(getRadioState(), getCapabilities());
  return { available, enabled };
}

/* ── Pending restore (memory only) ───────────────────────────────── */

/** Set while a web TX keyed by auto-set is in flight; owns the restore. */
let pending: ModInputSetPlan | null = null;

function clearPersistedPending(): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.removeItem(PENDING_RESTORE_KEY);
  } catch {
    /* ignore */
  }
}

/* ── Auto-set / restore engine ───────────────────────────────────── */

/**
 * Opt-in TX-start hook (called by `tx-adapter.startTx` BEFORE the MOR-617
 * guard arms; the guard stays truthfully visible until readback confirms).
 * The quiet-gating decision is `planAutoLanSet` in `./mod-input-gates.ts`.
 */
export function autoSetLanModInputForTx(): void {
  if (!enabled) return;
  const plan = planAutoLanSet(getRadioState(), getCapabilities());
  if (!plan) return;

  pending = plan;
  // Same per-group SET path as the ModePanel control (MOR-616), routed
  // through the typed facade; the backend confirms via write-through
  // readback (MOR-615).
  dispatchRadioIntent({
    name: plan.command,
    params: { source: LAN_MOD_INPUT_SOURCE },
  });
}

/**
 * Confirmed-off hook. One-shot: consumes the pending restore whether or not a
 * command is sent. Skips the SET when the group is known to be off LAN already
 * (`restoreTarget` in `./mod-input-gates.ts`).
 */
export function restoreModInputAfterTx(): void {
  const p = pending;
  pending = null;
  clearPersistedPending();
  if (!p) return;
  const current = getRadioState()?.[p.key] ?? null;
  const target = restoreTarget(p, current);
  if (target) {
    dispatchRadioIntent({ name: target.command, params: { source: target.source } });
  }
}

/**
 * Remove pre-authority-gate restore records from older frontend versions.
 * This migration never inspects cached radio state and never mutates the radio.
 */
export function clearLegacyPendingModInputRestore(): void {
  clearPersistedPending();
}
