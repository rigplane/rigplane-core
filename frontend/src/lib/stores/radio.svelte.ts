import type { ServerState, ReceiverState } from '../types/state';
import { setRadioPowerOn, setRigConnected, setRadioReady, setControlConnected, setRadioHealth } from './connection.svelte';

/**
 * Shared radio state — class-based $state pattern for cross-module reactivity.
 * Svelte 5 recommends class instances with $state properties for sharing
 * reactive state across modules and components.
 */
class RadioStore {
  current = $state<ServerState | null>(null);
}

export const radio = new RadioStore();

let lastRevision = -1;
let lastHealthRevision = -1;
const stateSubscribers = new Set<(state: ServerState | null) => void>();

function notifyRadioStateSubscribers(): void {
  for (const handler of stateSubscribers) {
    try {
      handler(radio.current);
    } catch (error) {
      console.warn('Local extension radio state subscriber failed', error);
    }
  }
}

export function subscribeRadioState(handler: (state: ServerState | null) => void): () => void {
  stateSubscribers.add(handler);
  handler(radio.current);
  return () => {
    stateSubscribers.delete(handler);
  };
}

// Optimistic patches: field → { value, expires, serverValueAtPatch }
// Kept until server confirms (value matches) OR hard timeout (5s)
const optimisticMain = new Map<string, { value: unknown; expires: number; serverValueAtPatch?: unknown }>();
const optimisticSub = new Map<string, { value: unknown; expires: number; serverValueAtPatch?: unknown }>();

// Optimistic patches for top-level fields (ptt, split, ritOn, compressorOn, etc.)
// Kept until server confirms or TTL expires.
const optimisticTopLevel = new Map<string, { value: unknown; expires: number }>();

// Top-level structural keys that should never be held optimistically
const STRUCTURAL_KEYS = new Set(['revision', 'main', 'sub', 'active', 'connection', 'updatedAt']);

function applyOptimistic(state: ServerState): ServerState {
  const now = Date.now();
  let result = state;

  for (const [map, key] of [[optimisticMain, 'main'], [optimisticSub, 'sub']] as const) {
    if (map.size === 0) continue;
    const serverRx = result[key];
    if (!serverRx) continue;
    const rx = { ...serverRx };
    let changed = false;
    for (const [field, entry] of map) {
      // Check if field is locked (rapid input protection)
      const lockKey = `${key}.${field}`;
      const lockExpires = lockedFields.get(lockKey);
      if (lockExpires && now < lockExpires) {
        // Field is locked - keep optimistic value, don't check server
        (rx as any)[field] = entry.value;
        changed = true;
        continue;
      } else if (lockExpires) {
        // Lock expired - clear it
        lockedFields.delete(lockKey);
      }

      const serverVal = (serverRx as any)[field];

      // Clear condition: hard timeout OR server confirmed
      let confirmed = now >= entry.expires;

      if (!confirmed) {
        if (field === 'freqHz' && typeof serverVal === 'number' && typeof entry.value === 'number') {
          // Frequency: tolerance-based (radio may snap to nearest step)
          confirmed = Math.abs(serverVal - entry.value) < 500; // 500 Hz tolerance
        } else {
          // All other fields: strict equality
          confirmed = serverVal === entry.value;
        }
      }

      // NOTE: Do NOT treat "server value changed from patch-time value" as confirmation.
      // With rapid discrete input (wheel/keyboard), a stale intermediate poll can differ from the
      // previous optimistic value while still not matching the latest target, which causes a false
      // confirmation and visible snap-back. We only clear on exact confirmation/tolerance or timeout.

      if (confirmed) {
        map.delete(field);
        continue;
      }
      // Server still has old value — keep optimistic override
      (rx as any)[field] = entry.value;
      changed = true;
    }
    if (changed) result = { ...result, [key]: rx };
  }

  // Apply top-level optimistic overrides (ptt, split, ritOn, etc.)
  if (optimisticTopLevel.size > 0) {
    const overrides: Record<string, unknown> = {};
    let changed = false;
    for (const [field, entry] of optimisticTopLevel) {
      const serverVal = (state as any)[field];
      const confirmed = now >= entry.expires || serverVal === entry.value;
      if (confirmed) {
        optimisticTopLevel.delete(field);
        continue;
      }
      // Server still has old value — keep optimistic override
      overrides[field] = entry.value;
      changed = true;
    }
    if (changed) result = { ...result, ...overrides };
  }

  return result;
}

/** Clear all radio state on disconnect. */
export function resetRadioState(): void {
  radio.current = null;
  lastRevision = -1;
  lastHealthRevision = -1;
  optimisticMain.clear();
  optimisticSub.clear();
  optimisticTopLevel.clear();
  lockedFields.clear();
  notifyRadioStateSubscribers();
}

export function setRadioState(state: ServerState): void {
  const isReset = lastRevision > 10 && state.revision < lastRevision / 2;
  const isInitial = radio.current === null;
  const nextHealthRevision = state.healthRevision ?? 0;
  const healthAdvanced = nextHealthRevision > lastHealthRevision;
  if (isReset) {
    console.warn(
      `Detected server restart: revision reset from ${lastRevision} to ${state.revision}`,
    );
  }
  if (isInitial || state.revision > lastRevision || healthAdvanced || isReset) {
    lastRevision = state.revision;
    lastHealthRevision = nextHealthRevision;
    radio.current = applyOptimistic(state);
    notifyRadioStateSubscribers();
    // Sync power status to connection store
    if (state.powerOn !== undefined) {
      setRadioPowerOn(state.powerOn);
    }
    // Sync connection readiness fields
    if (state.connection) {
      setRigConnected(state.connection.rigConnected);
      setRadioReady(state.connection.radioReady);
      setControlConnected(state.connection.controlConnected);
    }
    if (state.radioHealth !== undefined) {
      setRadioHealth(state.radioHealth);
    }
  }
}

const OPTIMISTIC_TTL = 5000; // hard timeout — normally cleared by server confirmation
const INPUT_LOCK_TTL = 1500; // cover command latency / polling lag for discrete inputs like wheel

/**
 * Optimistic update — instantly patch the active receiver's state
 * AND register patches so incoming polls don't revert them.
 */
// Field lock: prevent server updates from overwriting local changes during rapid input
const lockedFields = new Map<string, number>(); // `${receiver}.${field}` → expires timestamp

export function patchActiveReceiver(patch: Partial<ReceiverState>, lock = false): void {
  const s = radio.current;
  if (!s) return;
  const key = s.active === 'SUB' ? 'sub' : 'main';
  const map = key === 'sub' ? optimisticSub : optimisticMain;
  const expires = Date.now() + OPTIMISTIC_TTL;
  const currentRx = s[key];
  
  for (const [field, value] of Object.entries(patch)) {
    // Skip updating locked fields from WS echo (preserve user input lock)
    const lockKey = `${key}.${field}`;
    const lockExpires = lockedFields.get(lockKey);
    if (lockExpires && Date.now() < lockExpires && !lock) {
      // Field is locked by user input, don't overwrite with WS echo
      continue;
    }
    
    if (lock) {
      // Lock this field long enough to survive normal command latency + poll lag.
      // Drag keeps refreshing the lock continuously; wheel/keyboard are discrete and need longer.
      lockedFields.set(lockKey, Date.now() + INPUT_LOCK_TTL);
    }
    map.set(field, { value, expires, serverValueAtPatch: (currentRx as any)[field] });
  }
  radio.current = {
    ...s,
    [key]: { ...s[key], ...patch },
  };
  notifyRadioStateSubscribers();
}

/**
 * Optimistic update for a specific receiver (0 = MAIN, 1 = SUB).
 * Unlike patchActiveReceiver, this always targets the given receiver
 * regardless of which VFO is currently active.
 */
export function patchReceiver(receiver: 0 | 1, patch: Partial<ReceiverState>, lock = false): void {
  const s = radio.current;
  if (!s) return;
  const key = receiver === 1 ? 'sub' : 'main';
  const map = key === 'sub' ? optimisticSub : optimisticMain;
  const expires = Date.now() + OPTIMISTIC_TTL;
  const currentRx = s[key];

  for (const [field, value] of Object.entries(patch)) {
    const lockKey = `${key}.${field}`;
    const lockExpires = lockedFields.get(lockKey);
    if (lockExpires && Date.now() < lockExpires && !lock) {
      continue;
    }
    if (lock) {
      lockedFields.set(lockKey, Date.now() + INPUT_LOCK_TTL);
    }
    map.set(field, { value, expires, serverValueAtPatch: (currentRx as any)[field] });
  }
  radio.current = {
    ...s,
    [key]: { ...s[key], ...patch },
  };
  notifyRadioStateSubscribers();
}

/**
 * Optimistic update for top-level state fields (ptt, split, etc.)
 * Registers each patched field in the top-level optimistic map so that
 * incoming server polls don't immediately revert the optimistic value.
 */
export function patchRadioState(patch: Partial<ServerState>): void {
  const s = radio.current;
  if (!s) return;
  const expires = Date.now() + OPTIMISTIC_TTL;
  for (const [field, value] of Object.entries(patch)) {
    if (!STRUCTURAL_KEYS.has(field)) {
      optimisticTopLevel.set(field, { value, expires });
    }
  }
  radio.current = { ...s, ...patch };
  notifyRadioStateSubscribers();
}

// Convenience getters (still work in non-reactive contexts like callbacks)
export function getRadioState(): ServerState | null {
  return radio.current;
}

export function getMainReceiver(): ReceiverState | null {
  return radio.current?.main ?? null;
}

export function getSubReceiver(): ReceiverState | null {
  return radio.current?.sub ?? null;
}

export function getActiveReceiver(): ReceiverState | null {
  const s = radio.current;
  return s?.active === 'SUB' ? (s?.sub ?? null) : (s?.main ?? null);
}

export function getFrequency(): number {
  const s = radio.current;
  const active = s?.active === 'SUB' ? s?.sub : s?.main;
  return active?.freqHz ?? 0;
}

export function getMode(): string {
  const s = radio.current;
  const active = s?.active === 'SUB' ? s?.sub : s?.main;
  return active?.mode ?? '';
}

export function getIsTransmitting(): boolean {
  return radio.current?.ptt ?? false;
}

export function getLastRevision(): number {
  return lastRevision;
}
