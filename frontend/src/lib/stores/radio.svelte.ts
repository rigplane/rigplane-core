import type { ServerState, ReceiverState } from '../types/state';
import { setRadioPowerOn, setRigConnected, setRadioReady, setControlConnected, setRadioHealth } from './connection.svelte';
import { getFieldStatus, isFieldAvailable } from '../state/field-status';
import { capabilitiesMatchGeneration, getCapabilities } from './capabilities.svelte';

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

/** Clear all radio state on disconnect. */
export function resetRadioState(): void {
  radio.current = null;
  lastRevision = -1;
  lastFreshnessRevision = -1;
  lastObservationSeq = -1;
  lastHealthRevision = -1;
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

/** Receiver identity follows matching capabilities: MAIN may expose A/B slots but never a physical SUB. */
export function matchesCurrentCapabilityTopology(state: ServerState): boolean {
  const capabilities = getCapabilities();
  if (!capabilitiesMatchGeneration(state.providerGeneration) || capabilities === null) return false;
  const hasSubReceiver = Number.isSafeInteger(capabilities.receivers) && capabilities.receivers >= 2;
  if (state.active === 'SUB' && !hasSubReceiver) return false;

  const record = state as unknown as Record<string, unknown>;
  if (!record.txTarget || typeof record.txTarget !== 'object' || Array.isArray(record.txTarget)) return false;
  const txTarget = record.txTarget as Record<string, unknown>;
  if (txTarget.status === 'known' && txTarget.receiver === 'SUB' && !hasSubReceiver) return false;
  if (record.scopeControls !== undefined) {
    if (!record.scopeControls || typeof record.scopeControls !== 'object' || Array.isArray(record.scopeControls)) return false;
    const receiver = (record.scopeControls as Record<string, unknown>).receiver;
    const receiverIndex: number = Number.isSafeInteger(receiver) ? receiver as number : -1;
    if (receiverIndex < 0 || receiverIndex > (hasSubReceiver ? 1 : 0)) return false;
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
}

export function setRadioState(state: ServerState): boolean {
  if (!hasCurrentEpoch(state) || !matchesCurrentCapabilityTopology(state)) return false;

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
    radio.current = nextState;
    notifyRadioStateSubscribers();
    // Sync power status to connection store. MOR-1439: the IC-7300's serial
    // CI-V link never confirms powerstat (structural, like `active` before
    // MOR-1418/1421/1423) — an unobserved raw value is not a fact and must
    // collapse to `null` ("unknown"), never a confident true/false, or the UI
    // renders certainty (including StatusBar's forced powered-off
    // presentation) for a reading the radio never actually gave.
    if (nextState.powerOn !== undefined) {
      setRadioPowerOn(isFieldAvailable(nextState, 'powerOn') ? nextState.powerOn : null);
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
