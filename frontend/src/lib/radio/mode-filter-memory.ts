/**
 * Per-mode filter memory (MOR-495).
 *
 * The IC-7610 (and other Icom rigs) keep a *per-mode* filter selection: the
 * front panel recalls FIL1/FIL2/FIL3 for whatever mode you switch back to.
 * The web UI, however, sent a mode-only CI-V 0x06 frame, which makes the radio
 * apply its mode-DEFAULT filter (e.g. USB → FIL2) instead of the remembered
 * one — so `USB(FIL1) → RTTY → USB` lands on FIL2 from the web but FIL1 from
 * the front panel.
 *
 * This module holds a session-scoped `mode → last-filter` map so the mode
 * handlers can re-send the destination mode's remembered filter (the 2-byte
 * 0x06 form).  It is a neutral, framework-free singleton in `$lib/radio`,
 * mirroring `pending-focus.ts`, so BOTH live mode handlers
 * (`components-v2/wiring/command-bus` for the mobile skin and
 * `lib/runtime/commands/panel-commands` for the desktop-v2 skin) share one
 * instance and stay DRY.
 *
 * The map seeds itself from observed radio state: every time the ACTIVE
 * receiver reports `(mode, filter)`, that pairing is recorded, so the memory
 * converges to "what filter each mode is actually on".
 */

import { subscribeRadioState } from '$lib/stores/radio.svelte';
import type { ServerState } from '$lib/types/state';

const modeFilter = new Map<string, number>();

// Self-seed: attach one subscription to the radio-state stream.
// `subscribeRadioState` fires immediately with the current state and on every
// update, so the map converges as the operator works.  Subscribed once
// (module singleton); never unsubscribed for the page lifetime.
//
// Triggered lazily on first `getModeFilter` call rather than at module import,
// so consumers can be unit-tested without a live store subscription.  The
// store binding access is guarded: some test suites mock
// `$lib/stores/radio.svelte` without `subscribeRadioState`, and the lookup
// must remain a no-throw best-effort side effect.
let seedingStarted = false;
export function startModeFilterSeeding(): void {
  if (seedingStarted) {
    return;
  }
  seedingStarted = true;
  try {
    subscribeRadioState((state) => seedFromState(state));
  } catch {
    // Store subscription unavailable (e.g. unit-test mock) — the map still
    // works via explicit recordModeFilter()/seedFromState() calls.
  }
}

function normalize(mode: string): string {
  return mode.toUpperCase();
}

/** Record the filter currently in use for `mode`. */
export function recordModeFilter(mode: string, filter: number): void {
  if (!mode || !Number.isFinite(filter)) {
    return;
  }
  modeFilter.set(normalize(mode), filter);
}

/**
 * Look up the remembered filter for `mode`, or `undefined` if this mode has
 * not been observed this session.  Callers send mode-only (radio default) on
 * `undefined`.
 *
 * Ensures the state subscription is attached lazily on first use so the map
 * starts seeding as soon as a mode handler is constructed.
 */
export function getModeFilter(mode: string): number | undefined {
  startModeFilterSeeding();
  return modeFilter.get(normalize(mode));
}

/**
 * Seed the map from a state snapshot: record the ACTIVE receiver's
 * `(mode, filter)`.  Tolerant of partial/absent state (no-op when unavailable).
 */
export function seedFromState(state: ServerState | null): void {
  if (!state) {
    return;
  }
  const rx = state.active === 'SUB' ? state.sub : state.main;
  if (rx && typeof rx.mode === 'string' && typeof rx.filter === 'number') {
    recordModeFilter(rx.mode, rx.filter);
  }
}

/** Test hook — clear the session map and re-arm lazy seeding. */
export function _resetModeFilterMemory(): void {
  modeFilter.clear();
  seedingStarted = false;
}
