/**
 * Pure decision core of the two MOD-input TX gates — the warn-only
 * guard and the opt-in auto-set.
 *
 * Every function here is a pure function of `(state, caps, …)`: no store
 * reads, no runes, no `localStorage`, no command dispatch. That purity is
 * what makes this module the Stryker mutation target (named in
 * `frontend/stryker.config.mjs`); the `.svelte.ts` wrappers
 * (`mod-input-auto`, `mod-input-tx-guard`) own the live-state reads, the
 * opt-in flag and latches, and the dispatch, and are the only importers.
 */

import { isFieldRead } from '$lib/state/field-status';
import {
  LAN_MOD_INPUT_SOURCE,
  modInputCommand,
  modInputSourceLabel,
  modInputStateKey,
  type ModInputCommand,
  type ModInputStateKey,
} from '$lib/radio/mod-input';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';

/** The auto-set transaction `planAutoLanSet` proposes; latched by the wrapper. */
export interface ModInputSetPlan {
  command: ModInputCommand;
  key: ModInputStateKey;
  source: number;
}

/** The restore command `restoreTarget` approves, when it returns one. */
export interface ModInputRestore {
  command: ModInputCommand;
  source: number;
}

function hasDataModeCapability(caps: Capabilities | null): boolean {
  return caps?.capabilities?.includes('data_mode') ?? false;
}

function activeDataMode(state: ServerState | null): number {
  const rx = state?.active === 'SUB' ? state.sub : state?.main;
  return rx?.dataMode ?? 0;
}

/**
 * Show the auto-set toggle only for a connected radio with the data_mode
 * capability whose active DATA group the backend has actually read.
 */
export function autoLanModInputAvailable(
  state: ServerState | null,
  caps: Capabilities | null,
): boolean {
  const key = modInputStateKey(activeDataMode(state));
  return (
    state !== null
    && hasDataModeCapability(caps)
    && isFieldRead(state, key)
  );
}

/**
 * The TX-start auto-set decision. Same quiet-gating as the guard:
 * no state, no data_mode capability, group not read, source unknown, or
 * already LAN → null (no change, no pending). Otherwise the per-group SET
 * command to issue for LAN and the source to remember for the restore.
 */
export function planAutoLanSet(
  state: ServerState | null,
  caps: Capabilities | null,
): ModInputSetPlan | null {
  if (!state) return null;
  if (!hasDataModeCapability(caps)) return null;
  const dataMode = activeDataMode(state);
  const key = modInputStateKey(dataMode);
  if (!isFieldRead(state, key)) return null;
  const source = state[key] ?? null;
  if (source === null || source === LAN_MOD_INPUT_SOURCE) return null;
  return { command: modInputCommand(dataMode), key, source };
}

/**
 * The confirmed-off restore decision: send the remembered source
 * back only while the group is still on LAN (or its current source is
 * unknown) — a manual mid-TX change away from LAN wins and returns null.
 */
export function restoreTarget(
  pending: ModInputSetPlan,
  currentSource: number | null,
): ModInputRestore | null {
  if (currentSource !== null && currentSource !== LAN_MOD_INPUT_SOURCE) return null;
  return { command: pending.command, source: pending.source };
}

/**
 * The active receiver group's MOD-input source when a declared LAN route
 * warrants a warning, or null when the guard must stay quiet.
 */
export function offendingSource(
  state: ServerState | null,
  caps: Capabilities | null,
): number | null {
  if (!state) return null;
  if (!hasDataModeCapability(caps)) return null;
  if (
    caps?.audioTxRoute !== 'lan'
    || caps.audioTxRequiredModInputSource !== LAN_MOD_INPUT_SOURCE
  ) return null;
  const rx = state.active === 'SUB' ? state.sub : state.main;
  const key = modInputStateKey(rx?.dataMode ?? 0);
  if (!isFieldRead(state, key)) return null;
  const source = state[key] ?? null;
  if (source === null || source === LAN_MOD_INPUT_SOURCE) return null;
  return source;
}

/**
 * The warning banner's props: visible while `armed` and an offending source
 * still stands, with its human label (`modInputSourceLabel`); quiet
 * otherwise. `armed` is the wrapper's TX-start latch, passed in.
 */
export function toModInputTxGuardProps(
  armed: boolean,
  state: ServerState | null,
  caps: Capabilities | null,
): { visible: boolean; sourceLabel: string | null } {
  const source = armed ? offendingSource(state, caps) : null;
  return {
    visible: source !== null,
    sourceLabel: modInputSourceLabel(source),
  };
}
