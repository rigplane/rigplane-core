import type { ServerState, ReceiverState } from '../types/state';
import { setRadioPowerOn, setRigConnected, setRadioReady, setControlConnected, setRadioHealth } from './connection.svelte';
import { getFieldStatus, isFieldAvailable } from '../state/field-status';
import { capabilitiesMatchGeneration } from './capabilities.svelte';

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
let lastFreshnessRevision = -1;
let lastObservationSeq = -1;
let lastHealthRevision = -1;
const stateSubscribers = new Set<(state: ServerState | null) => void>();
const LIVE_METADATA_KEYS = new Set([
  'connection',
  'fieldStatus',
  'healthRevision',
  'publicStateSeq',
  'radioHealth',
  'transportSeq',
  'updatedAt',
  'wsClients',
]);

function stateRevision(state: ServerState): number {
  return state.stateRevision ?? state.revision;
}

function freshnessRevision(state: ServerState): number {
  return state.freshnessRevision ?? 0;
}

function observationSeq(state: ServerState): number {
  return state.observationSeq ?? 0;
}

function deliverySeq(state: ServerState | null): number {
  if (!state) return -1;
  return Math.max(state.publicStateSeq ?? 0, state.transportSeq ?? 0);
}

function valuesEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

function hasOnlyLiveMetadataChanges(current: ServerState | null, next: ServerState): boolean {
  if (!current) return false;
  const currentRecord = current as unknown as Record<string, unknown>;
  const nextRecord = next as unknown as Record<string, unknown>;
  const keys = new Set([...Object.keys(current), ...Object.keys(next)]);
  for (const key of keys) {
    if (valuesEqual(currentRecord[key], nextRecord[key])) {
      continue;
    }
    if (!LIVE_METADATA_KEYS.has(key)) {
      return false;
    }
  }
  return true;
}

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

// VFO readouts are observed radio truth. Commands may be pending elsewhere,
// but their requested values must never cover a newer StateStore snapshot.
// MOR-1405 owns removal of the remaining non-VFO optimistic surfaces.
const VFO_TRUTH_FIELDS = new Set([
  'freqHz', 'mode', 'filter', 'dataMode', 'activeSlot',
  'vfoA', 'vfoB', 'unselectedVfo', 'filterWidth', 'filterShape',
]);

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
      if (VFO_TRUTH_FIELDS.has(field)) {
        map.delete(field);
        lockedFields.delete(`${key}.${field}`);
        continue;
      }
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
      // For freq specifically, the overlay clears ONLY via the tolerance/value-match check above
      // (|serverVal - overlay| < 500) or the lowered freq TTL — never on a mere causal advance,
      // because an in-flight poll captured before an unlocked optimistic patch can carry the OLD
      // freq with an advanced observationSeq and would otherwise flash the stale value for one cycle.

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
  lastFreshnessRevision = -1;
  lastObservationSeq = -1;
  lastHealthRevision = -1;
  optimisticMain.clear();
  optimisticSub.clear();
  optimisticTopLevel.clear();
  lockedFields.clear();
  notifyRadioStateSubscribers();
}

function validProviderGeneration(value: unknown): value is number {
  return typeof value === 'number'
    && Number.isSafeInteger(value)
    && value >= 0;
}

function validCounter(value: unknown): value is number {
  return typeof value === 'number'
    && Number.isSafeInteger(value)
    && value >= 0;
}

function validReceiver(value: unknown): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const receiver = value as Record<string, unknown>;
  return typeof receiver.freqHz === 'number'
    && Number.isFinite(receiver.freqHz)
    && typeof receiver.mode === 'string'
    && (typeof receiver.filter === 'number' || receiver.filter === null)
    && typeof receiver.dataMode === 'number'
    && Number.isFinite(receiver.dataMode)
    && typeof receiver.sMeter === 'number'
    && Number.isFinite(receiver.sMeter)
    && typeof receiver.att === 'number'
    && Number.isFinite(receiver.att)
    && typeof receiver.preamp === 'number'
    && Number.isFinite(receiver.preamp)
    && typeof receiver.nb === 'boolean'
    && typeof receiver.nr === 'boolean'
    && typeof receiver.afLevel === 'number'
    && Number.isFinite(receiver.afLevel)
    && typeof receiver.rfGain === 'number'
    && Number.isFinite(receiver.rfGain)
    && typeof receiver.squelch === 'number'
    && Number.isFinite(receiver.squelch);
}

function validConnection(value: unknown): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const connection = value as Record<string, unknown>;
  return typeof connection.rigConnected === 'boolean'
    && typeof connection.radioReady === 'boolean'
    && typeof connection.controlConnected === 'boolean';
}

function validTxTarget(value: unknown): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const target = value as Record<string, unknown>;
  if (target.status === 'unknown') return typeof target.reason === 'string';
  return target.status === 'known'
    && (target.receiver === 'MAIN' || target.receiver === 'SUB')
    && (target.slot === 'A' || target.slot === 'B' || target.slot === null)
    && (typeof target.frequencyHz === 'number' || target.frequencyHz === null);
}

/** Runtime contract shared by WebSocket and the remaining legacy HTTP writer. */
export function isValidServerState(value: unknown): value is ServerState {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const state = value as Record<string, unknown>;
  if (
    state.stateContractVersion !== 1
    || !validProviderGeneration(state.providerGeneration)
    || !validCounter(state.revision)
    || !validCounter(state.stateRevision)
    || !validCounter(state.freshnessRevision)
    || !validCounter(state.observationSeq)
    || typeof state.updatedAt !== 'string'
    || (state.active !== 'MAIN' && state.active !== 'SUB')
    || typeof state.ptt !== 'boolean'
    || typeof state.split !== 'boolean'
    || typeof state.dualWatch !== 'boolean'
    || typeof state.tunerStatus !== 'number'
    || !Number.isFinite(state.tunerStatus)
    || !validReceiver(state.main)
    || (state.sub !== undefined && state.sub !== null && !validReceiver(state.sub))
    || !validConnection(state.connection)
    || !validTxTarget(state.txTarget)
  ) return false;
  for (const key of ['healthRevision', 'publicStateSeq', 'transportSeq'] as const) {
    if (state[key] !== undefined && !validCounter(state[key])) return false;
  }
  if (state.wsClients !== undefined) {
    if (!state.wsClients || typeof state.wsClients !== 'object' || Array.isArray(state.wsClients)) return false;
    const clients = state.wsClients as Record<string, unknown>;
    if (!validCounter(clients.scope) || !validCounter(clients.control) || !validCounter(clients.audio)) return false;
  }
  for (const key of ['fieldStatus', 'radioHealth'] as const) {
    if (state[key] !== undefined && (!state[key] || typeof state[key] !== 'object' || Array.isArray(state[key]))) return false;
  }
  return true;
}

/**
 * The generated public contract omits ``sub`` for a single-receiver radio,
 * while the established client-side merged-state type keeps a structural
 * receiver slot for consumers that index by MAIN/SUB. This is a local alias
 * of the observed MAIN receiver, never a newly observed SUB radio: capability
 * gates remain the authority for whether a SUB surface can be displayed.
 */
function normalizeValidatedState(state: ServerState): ServerState {
  const wireState = state as unknown as { sub?: ReceiverState | null };
  if (wireState.sub !== undefined && wireState.sub !== null) return state;
  return { ...state, sub: { ...state.main } };
}

function hasCurrentEpoch(state: ServerState): boolean {
  const record = state as unknown as Record<string, unknown>;
  return isValidServerState(state)
    && capabilitiesMatchGeneration(record.providerGeneration);
}

function clearGenerationBookkeeping(): void {
  lastRevision = -1;
  lastFreshnessRevision = -1;
  lastObservationSeq = -1;
  lastHealthRevision = -1;
  optimisticMain.clear();
  optimisticSub.clear();
  optimisticTopLevel.clear();
  lockedFields.clear();
}

export function setRadioState(state: ServerState): boolean {
  if (!hasCurrentEpoch(state)) return false;

  const nextState = normalizeValidatedState(state);

  const nextGeneration = (nextState as unknown as Record<string, unknown>).providerGeneration as number;
  const currentGeneration = radio.current
    ? (radio.current as unknown as Record<string, unknown>).providerGeneration
    : null;
  if (currentGeneration !== null && currentGeneration !== nextGeneration) {
    // A new provider is a new revision domain. Never carry an optimistic
    // patch or lock from the retired provider into its observed state.
    clearGenerationBookkeeping();
  }
  const nextStateRevision = stateRevision(nextState);
  const nextFreshnessRevision = freshnessRevision(nextState);
  const nextObservationSeq = observationSeq(nextState);
  const isInitial = radio.current === null;
  const nextHealthRevision = nextState.healthRevision ?? 0;
  const healthAdvanced = nextHealthRevision > lastHealthRevision;
  const freshnessAdvanced = nextFreshnessRevision > lastFreshnessRevision;
  const observationAdvanced = nextObservationSeq > lastObservationSeq;
  const semanticAdvanced = nextStateRevision > lastRevision;
  const semanticCurrent = nextStateRevision === lastRevision;
  const liveMetadataAdvanced = semanticCurrent
    && deliverySeq(nextState) > deliverySeq(radio.current)
    && hasOnlyLiveMetadataChanges(radio.current, nextState);
  const metadataAdvanced = semanticCurrent && (
    freshnessAdvanced
    || observationAdvanced
    || healthAdvanced
    || liveMetadataAdvanced
  );
  if (
    isInitial
    || semanticAdvanced
    || metadataAdvanced
  ) {
    lastRevision = nextStateRevision;
    lastFreshnessRevision = nextFreshnessRevision;
    lastObservationSeq = nextObservationSeq;
    lastHealthRevision = nextHealthRevision;
    radio.current = applyOptimistic(nextState);
    notifyRadioStateSubscribers();
    // Sync power status to connection store
    if (nextState.powerOn !== undefined) {
      setRadioPowerOn(nextState.powerOn);
    }
    // Sync connection readiness fields
    if (nextState.connection) {
      setRigConnected(nextState.connection.rigConnected);
      setRadioReady(nextState.connection.radioReady);
      setControlConnected(nextState.connection.controlConnected);
    }
    if (nextState.radioHealth !== undefined) {
      setRadioHealth(nextState.radioHealth);
    }
    return true;
  }
  return false;
}

const OPTIMISTIC_TTL = 5000; // hard timeout — normally cleared by server confirmation
const OPTIMISTIC_FREQ_TTL = 1500; // shorter timeout for freq overlay — falls back to server sooner
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
  const currentRx = s[key];
  const accepted: Partial<ReceiverState> = {};

  for (const [field, value] of Object.entries(patch)) {
    if (VFO_TRUTH_FIELDS.has(field)) continue;
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
    const expires = Date.now() + (field === 'freqHz' ? OPTIMISTIC_FREQ_TTL : OPTIMISTIC_TTL);
    map.set(field, { value, expires, serverValueAtPatch: (currentRx as any)[field] });
    (accepted as any)[field] = value;
  }
  if (Object.keys(accepted).length === 0) return;
  radio.current = {
    ...s,
    [key]: { ...s[key], ...accepted },
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
  const currentRx = s[key];
  const accepted: Partial<ReceiverState> = {};

  for (const [field, value] of Object.entries(patch)) {
    if (VFO_TRUTH_FIELDS.has(field)) continue;
    const lockKey = `${key}.${field}`;
    const lockExpires = lockedFields.get(lockKey);
    if (lockExpires && Date.now() < lockExpires && !lock) {
      continue;
    }
    if (lock) {
      lockedFields.set(lockKey, Date.now() + INPUT_LOCK_TTL);
    }
    const expires = Date.now() + (field === 'freqHz' ? OPTIMISTIC_FREQ_TTL : OPTIMISTIC_TTL);
    map.set(field, { value, expires, serverValueAtPatch: (currentRx as any)[field] });
    (accepted as any)[field] = value;
  }
  if (Object.keys(accepted).length === 0) return;
  radio.current = {
    ...s,
    [key]: { ...s[key], ...accepted },
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

export function getRadioFieldStatus(path: string) {
  return getFieldStatus(radio.current, path);
}

export function isRadioFieldAvailable(path: string): boolean {
  return isFieldAvailable(radio.current, path);
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
