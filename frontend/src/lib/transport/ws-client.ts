import type { WsCommand, WsIncoming } from '../types/protocol';
import { makeCommandId } from '../types/protocol';
import { isLiveRadioAvailable, setWsConnected, setHttpConnected, markStateUpdated, setReconnecting, setRadioStatus } from '../stores/connection.svelte';
import { getRadioState, isValidServerState, matchesCurrentCapabilityTopology, patchActiveReceiver, patchRadioState, resetRadioState, setRadioState } from '../stores/radio.svelte';
import { capabilitiesMatchGeneration, clearCapabilities, setCapabilities } from '../stores/capabilities.svelte';
import { fetchCapabilities } from './http-client';

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';
export interface ControlSessionTransition {
  state: ConnectionState;
  epoch: number;
}
export type CommandDeliveryKind = 'transport-sent' | 'ack' | 'response-ok' | 'response-error' | 'error';
export interface CommandDeliveryEvent {
  commandId: string;
  kind: CommandDeliveryKind;
  originalEpoch: number;
  eventEpoch: number;
  error?: string;
}
type MessageHandler = (msg: WsIncoming) => void;
type BinaryHandler = (data: ArrayBuffer) => void;
type StateHandler = (state: ConnectionState) => void;
export type ControlSessionTransitionHandler = (transition: ControlSessionTransition) => void;
type CommandDeliveryHandler = (event: CommandDeliveryEvent) => void;
type PendingPttRelease = { command: WsCommand; originalEpoch: number };
type TrackedPttCommand = PendingPttRelease & {
  seen: Set<CommandDeliveryKind>;
};

const BACKOFF_BASE_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;
const HEARTBEAT_TIMEOUT_MS = 30_000;  // server may be idle on control WS
const KEEPALIVE_INTERVAL_MS = 15_000; // send ping to prevent idle timeout
const MAX_QUEUE_SIZE = 20;
const MAX_TRACKED_PTT_COMMANDS = 100;

// Command types where only the latest value matters (last write wins)
const IDEMPOTENT_TYPES = new Set(['set_freq', 'set_mode', 'set_filter']);

function pttIntent(name: string, params: Record<string, unknown>): 'on' | 'off' | null {
  if (name === 'ptt_on') return 'on';
  if (name === 'ptt_off') return 'off';
  if (name === 'ptt' && params.state === true) return 'on';
  if (name === 'ptt' && params.state === false) return 'off';
  return null;
}

function calcBackoff(attempt: number): number {
  const base = Math.min(BACKOFF_BASE_MS * 2 ** attempt, BACKOFF_MAX_MS);
  return base * (0.8 + Math.random() * 0.4);
}

export class WsChannel {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private keepaliveTimer: ReturnType<typeof setInterval> | null = null;
  private attempt = 0;
  private intentionalClose = false;
  private sendQueue: WsCommand[] = [];
  private pendingPttRelease: PendingPttRelease | null = null;
  private transportEpoch = 0;
  private trackedPttCommands = new Map<string, TrackedPttCommand>();
  private commandDeliveryHandlers = new Set<CommandDeliveryHandler>();
  private messageHandlers = new Set<MessageHandler>();
  private binaryHandlers = new Set<BinaryHandler>();
  private stateHandlers = new Set<StateHandler>();
  private sessionTransitionHandlers = new Set<ControlSessionTransitionHandler>();
  private sessionTransitionQueue: ControlSessionTransition[] = [];
  private dispatchingSessionTransition = false;
  private _state: ConnectionState = 'disconnected';
  private url = '';
  private _subscribeMsg: Record<string, unknown> | null = null;

  /** Register a message to re-send automatically on every (re)connect. */
  setSubscribeMessage(msg: Record<string, unknown>) {
    this._subscribeMsg = msg;
  }

  get state(): ConnectionState {
    return this._state;
  }

  private setState(s: ConnectionState) {
    const changed = this._state !== s;
    this._state = s;
    this.stateHandlers.forEach((h) => h(s));
    if (changed) {
      this._emitSessionTransition({ state: s, epoch: this.transportEpoch });
    }
  }

  private _emitSessionTransition(transition: ControlSessionTransition): void {
    this.sessionTransitionQueue.push(transition);
    if (this.dispatchingSessionTransition) return;
    this.dispatchingSessionTransition = true;
    try {
      while (this.sessionTransitionQueue.length > 0) {
        const next = this.sessionTransitionQueue.shift()!;
        for (const handler of [...this.sessionTransitionHandlers]) handler(next);
      }
    } finally {
      this.dispatchingSessionTransition = false;
    }
  }

  connect(url: string) {
    const rs = this.ws?.readyState;
    if (rs === WebSocket.OPEN || rs === WebSocket.CONNECTING) return;
    this.url = url;
    this.intentionalClose = false;
    this._open();
  }

  private _open() {
    this.setState(this.attempt === 0 ? 'connecting' : 'reconnecting');
    const ws = new WebSocket(this.url);
    let socketEpoch = 0;
    ws.binaryType = 'arraybuffer';
    this.ws = ws;

    ws.onopen = () => {
      socketEpoch = ++this.transportEpoch;
      this.attempt = 0;
      this.setState('connected');
      if (
        this.ws !== ws
        || ws.readyState !== WebSocket.OPEN
        || this._state !== 'connected'
        || socketEpoch !== this.transportEpoch
      ) return;
      this._resetHeartbeat();
      this._startKeepalive();
      // drain send queue
      const release = this.pendingPttRelease;
      this.pendingPttRelease = null;
      if (release) this._sendPtt(ws, release, socketEpoch);
      const queued = this.sendQueue.splice(0).filter(
        (cmd) => pttIntent(cmd.name, cmd.params) !== 'on',
      );
      for (const cmd of queued) ws.send(JSON.stringify(cmd));
      // Re-send subscribe on every (re)connect so server pushes state immediately
      if (this._subscribeMsg) ws.send(JSON.stringify(this._subscribeMsg));
    };

    ws.onmessage = (event: MessageEvent) => {
      if (this.ws === ws) this._resetHeartbeat();
      if (event.data instanceof ArrayBuffer) {
        this.binaryHandlers.forEach((h) => h(event.data as ArrayBuffer));
      } else {
        try {
          const raw = JSON.parse(event.data as string) as Record<string, unknown>;
          this._emitCommandResult(raw, socketEpoch);
          // Handle status-based error responses ({"status":"error", ...})
          if (raw['status'] === 'error') {
            const errorMsg = (raw['message'] as string) || (raw['error'] as string) || 'Command failed';
            console.error(`[ws] error response:`, raw);
            const errNote = { type: 'notification', level: 'error', message: errorMsg, category: 'command' } as any;
            this.messageHandlers.forEach((h) => h(errNote));
            return;
          }
          const msg = raw as unknown as WsIncoming;
          this.messageHandlers.forEach((h) => h(msg));
          if (msg.type === 'error') {
            console.error(`[ws] error from server (id=${msg.id}): ${msg.message}`);
          } else if (msg.type === 'response') {
            if (msg.ok === false) {
              const errorMsg = msg.message || msg.error || 'Command failed';
              console.error(`[ws] command ${msg.id} failed: ${errorMsg}`);
              for (const h of this.messageHandlers) {
                h({ type: 'notification', level: 'error', message: errorMsg, category: 'command' } as any);
              }
            } else {
              console.debug(`[ws] command ${msg.id} ok`);
            }
          }
        } catch {
          // ignore malformed frames
        }
      }
    };

    ws.onclose = () => {
      this._clearHeartbeat();
      this.ws = null;
      this.setState('disconnected');
      if (!this.intentionalClose) {
        const delay = calcBackoff(this.attempt++);
        this.reconnectTimer = setTimeout(() => this._open(), delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }

  /** Reconnect using the last-known URL (no-op if never connected or already open). */
  reconnect() {
    if (!this.url) return;
    this.connect(this.url);
  }

  disconnect() {
    this.intentionalClose = true;
    this._clearTimers();
    const { ws } = this;
    this.ws = null;
    if (ws) {
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CLOSING) {
        ws.close();
      } else if (ws.readyState === WebSocket.CONNECTING) {
        // WS not yet open — close silently after open to avoid console error
        ws.onopen = () => ws.close();
        ws.onerror = () => {}; // suppress error log
      }
    }
    this.setState('disconnected');
    this.attempt = 0;
  }

  send(cmd: WsCommand): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      const intent = pttIntent(cmd.name, cmd.params);
      if (intent) {
        return this._sendPtt(
          this.ws,
          { command: cmd, originalEpoch: this.transportEpoch },
          this.transportEpoch,
        );
      }
      try {
        this.ws.send(JSON.stringify(cmd));
        return true;
      } catch {
        return false;
      }
    }
    const intent = pttIntent(cmd.name, cmd.params);
    if (intent === 'on') {
      return false;
    }
    if (intent === 'off') {
      this.sendQueue = this.sendQueue.filter(
        (queued) => pttIntent(queued.name, queued.params) !== 'on',
      );
      this.pendingPttRelease = { command: cmd, originalEpoch: this.transportEpoch };
      return false;
    }
    // Deduplicate idempotent commands — keep only the latest value
    if (IDEMPOTENT_TYPES.has(cmd.name)) {
      this.sendQueue = this.sendQueue.filter((c) => c.name !== cmd.name);
    }
    this.sendQueue.push(cmd);
    // Drop oldest if over limit
    if (this.sendQueue.length > MAX_QUEUE_SIZE) {
      this.sendQueue.shift();
    }
    return false;
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  onMessage(handler: MessageHandler): () => void {
    this.messageHandlers.add(handler);
    return () => this.messageHandlers.delete(handler);
  }

  onBinary(handler: BinaryHandler): () => void {
    this.binaryHandlers.add(handler);
    return () => this.binaryHandlers.delete(handler);
  }

  onStateChange(handler: StateHandler): () => void {
    this.stateHandlers.add(handler);
    return () => this.stateHandlers.delete(handler);
  }

  onSessionTransition(handler: ControlSessionTransitionHandler): () => void {
    this.sessionTransitionHandlers.add(handler);
    return () => this.sessionTransitionHandlers.delete(handler);
  }

  onCommandDelivery(handler: CommandDeliveryHandler): () => void {
    this.commandDeliveryHandlers.add(handler);
    return () => this.commandDeliveryHandlers.delete(handler);
  }

  private _sendPtt(ws: WebSocket, pending: PendingPttRelease, eventEpoch: number): boolean {
    try {
      ws.send(JSON.stringify(pending.command));
    } catch (error) {
      this._emitDelivery({
        commandId: pending.command.id,
        kind: 'error',
        originalEpoch: pending.originalEpoch,
        eventEpoch,
        error: error instanceof Error ? error.message : 'WebSocket send failed',
      });
      return false;
    }
    const tracked: TrackedPttCommand = { ...pending, seen: new Set() };
    if (this.trackedPttCommands.size >= MAX_TRACKED_PTT_COMMANDS) {
      this.trackedPttCommands.delete(this.trackedPttCommands.keys().next().value!);
    }
    this.trackedPttCommands.set(pending.command.id, tracked);
    this._emitTracked(tracked, 'transport-sent', eventEpoch);
    return true;
  }

  private _emitCommandResult(raw: Record<string, unknown>, eventEpoch: number): void {
    const id = raw.id;
    if (typeof id !== 'string') return;
    const tracked = this.trackedPttCommands.get(id);
    if (!tracked) return;
    if (raw.type === 'ack') this._emitTracked(tracked, 'ack', eventEpoch);
    else if (raw.type === 'response') {
      this._emitTracked(tracked, raw.ok === false ? 'response-error' : 'response-ok', eventEpoch);
    } else if (raw.type === 'error' || raw.status === 'error') {
      this._emitTracked(tracked, 'error', eventEpoch, String(raw.message ?? raw.error ?? 'Command failed'));
    }
  }

  private _emitTracked(
    tracked: TrackedPttCommand,
    kind: CommandDeliveryKind,
    eventEpoch: number,
    error?: string,
  ): void {
    if (tracked.seen.has(kind)) return;
    tracked.seen.add(kind);
    this._emitDelivery({
      commandId: tracked.command.id,
      kind,
      originalEpoch: tracked.originalEpoch,
      eventEpoch,
      ...(error ? { error } : {}),
    });
  }

  private _emitDelivery(event: CommandDeliveryEvent): void {
    this.commandDeliveryHandlers.forEach((handler) => handler(event));
  }

  private _resetHeartbeat() {
    this._clearHeartbeat();
    this.heartbeatTimer = setTimeout(() => {
      console.warn('[ws] heartbeat timeout — closing');
      this.ws?.close();
    }, HEARTBEAT_TIMEOUT_MS);
  }

  private _clearHeartbeat() {
    if (this.heartbeatTimer) {
      clearTimeout(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private _startKeepalive() {
    this._stopKeepalive();
    this.keepaliveTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }));
        // Reset heartbeat — we know the connection is alive if send succeeds
        this._resetHeartbeat();
      }
    }, KEEPALIVE_INTERVAL_MS);
  }

  private _stopKeepalive() {
    if (this.keepaliveTimer) {
      clearInterval(this.keepaliveTimer);
      this.keepaliveTimer = null;
    }
  }

  private _clearTimers() {
    this._clearHeartbeat();
    this._stopKeepalive();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}

// ─── Control channel singleton (backward-compat API) ───────────────────────

const _ctrl = new WsChannel();
_ctrl.onStateChange((s) => {
  setWsConnected(s === 'connected');
  setReconnecting(s === 'connecting' || s === 'reconnecting');
  if (s === 'disconnected') {
    _fullState = null;
    _hasReceivedFullState = false;
    _acceptedProviderGeneration = null;
    _expectedProviderGeneration = null;
    _capabilityRefreshGeneration = null;
    resetRadioState();
    clearCapabilities();
  }
});
// Delta state tracking for incremental updates
let _fullState: Record<string, unknown> | null = null;
let _hasReceivedFullState = false;
let _acceptedProviderGeneration: number | null = null;
let _expectedProviderGeneration: number | null = null;
let _capabilityRefreshGeneration: number | null = null;

function isProviderGeneration(value: unknown): value is number {
  return typeof value === 'number'
    && Number.isSafeInteger(value)
    && value >= 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function epochOf(envelope: Record<string, unknown>): number | null {
  if (envelope.stateContractVersion !== 1 || !isProviderGeneration(envelope.providerGeneration)) {
    return null;
  }
  return envelope.providerGeneration;
}

function highestSeenGeneration(): number | null {
  if (_expectedProviderGeneration !== null) return _expectedProviderGeneration;
  return _acceptedProviderGeneration;
}

function resetForProviderGeneration(generation: number): void {
  _fullState = null;
  _hasReceivedFullState = false;
  _acceptedProviderGeneration = null;
  _expectedProviderGeneration = generation;
  resetRadioState();
  clearCapabilities();
}

function commitCurrentState(): boolean {
  if (
    !_hasReceivedFullState
    || _fullState === null
    || _acceptedProviderGeneration === null
    || !capabilitiesMatchGeneration(_acceptedProviderGeneration)
  ) return false;
  return setRadioState(_fullState as any);
}

function refreshCapabilities(generation: number): void {
  if (_capabilityRefreshGeneration === generation) return;
  _capabilityRefreshGeneration = generation;
  void fetchCapabilities().then((caps) => {
    if (
      _acceptedProviderGeneration !== generation
      || !_hasReceivedFullState
      || _fullState === null
    ) return;
    const record = caps as unknown as Record<string, unknown>;
    if (record.stateContractVersion !== 1 || record.providerGeneration !== generation) return;
    if (setCapabilities(caps)) commitCurrentState();
  }).catch(() => {
    // Capability retrieval is metadata only. Remain fail-closed until a later
    // provider generation or reconnect supplies a new authoritative full.
  });
}

function syncEnvelopeRevisions(
  state: Record<string, unknown>,
  envelope: Record<string, unknown>,
): Record<string, unknown> {
  if (typeof envelope.revision === 'number') {
    state.revision = envelope.revision;
  }
  if (typeof envelope.stateRevision === 'number') {
    state.stateRevision = envelope.stateRevision;
  }
  if (typeof envelope.freshnessRevision === 'number') {
    state.freshnessRevision = envelope.freshnessRevision;
  }
  if (typeof envelope.observationSeq === 'number') {
    state.observationSeq = envelope.observationSeq;
  }
  if (typeof envelope.publicStateSeq === 'number') {
    state.publicStateSeq = envelope.publicStateSeq;
  }
  if (typeof envelope.transportSeq === 'number') {
    state.transportSeq = envelope.transportSeq;
  }
  if (typeof envelope.healthRevision === 'number') {
    state.healthRevision = envelope.healthRevision;
  }
  return state;
}

function stateRevisionOf(state: Record<string, unknown> | null): number {
  if (!state) return -1;
  if (typeof state.stateRevision === 'number') return state.stateRevision;
  if (typeof state.revision === 'number') return state.revision;
  return -1;
}

function freshnessRevisionOf(state: Record<string, unknown> | null): number {
  return typeof state?.freshnessRevision === 'number' ? state.freshnessRevision : 0;
}

function healthRevisionOf(state: Record<string, unknown> | null): number {
  return typeof state?.healthRevision === 'number' ? state.healthRevision : 0;
}

function observationSeqOf(state: Record<string, unknown> | null): number {
  return typeof state?.observationSeq === 'number' ? state.observationSeq : 0;
}

function deliverySeqOf(state: Record<string, unknown> | null): number {
  if (!state) return -1;
  const publicStateSeq = typeof state.publicStateSeq === 'number' ? state.publicStateSeq : 0;
  const transportSeq = typeof state.transportSeq === 'number' ? state.transportSeq : 0;
  return Math.max(publicStateSeq, transportSeq);
}

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

function hasOnlyLiveMetadataKeys(keys: string[]): boolean {
  return keys.length > 0 && keys.every((key) => LIVE_METADATA_KEYS.has(key));
}

function isRevisionAcceptable(
  currentState: Record<string, unknown> | null,
  nextState: Record<string, unknown>,
  changedKeys: string[] = [],
): boolean {
  if (!currentState) return true;

  const lastRevision = stateRevisionOf(currentState);
  const nextRevision = stateRevisionOf(nextState);
  const semanticAdvanced = nextRevision > lastRevision;
  const semanticCurrent = nextRevision === lastRevision;
  const metadataAdvanced = semanticCurrent && (
    freshnessRevisionOf(nextState) > freshnessRevisionOf(currentState)
    || healthRevisionOf(nextState) > healthRevisionOf(currentState)
    || observationSeqOf(nextState) > observationSeqOf(currentState)
    || (
      deliverySeqOf(nextState) > deliverySeqOf(currentState)
      && hasOnlyLiveMetadataKeys(changedKeys)
    )
  );

  return semanticAdvanced || metadataAdvanced;
}

function applyDeltaEnvelope(envelope: Record<string, unknown>): Record<string, unknown> | null {
  const deltaType = envelope.type;
  const generation = epochOf(envelope);
  if (generation === null || (deltaType !== 'full' && deltaType !== 'delta')) return null;
  const highestSeen = highestSeenGeneration();
  if (highestSeen !== null && generation < highestSeen) return null;

  if (deltaType === 'full') {
    if (!isRecord(envelope.data)) return null;
    const data = envelope.data;
    if (data.stateContractVersion !== 1 || data.providerGeneration !== generation) return null;
    const nextState = syncEnvelopeRevisions({ ...data }, envelope);
    if (!isValidServerState(nextState)) return null;
    if (capabilitiesMatchGeneration(generation) && !matchesCurrentCapabilityTopology(nextState as any)) return null;
    if (
      _acceptedProviderGeneration === generation
      && _fullState !== null
      && (
        stateRevisionOf(nextState) < stateRevisionOf(_fullState)
        || deliverySeqOf(nextState) < deliverySeqOf(_fullState)
        || !isRevisionAcceptable(_fullState, nextState, Object.keys(nextState))
      )
    ) return null;
    if (_acceptedProviderGeneration !== generation || !_hasReceivedFullState) {
      // Bootstrap may already have fetched matching capabilities. Keep that
      // proven epoch; every provider transition (or an unbased higher delta)
      // still clears all retired browser truth before accepting this full.
      if (
        _expectedProviderGeneration !== generation
        && (_acceptedProviderGeneration !== null || !capabilitiesMatchGeneration(generation))
      ) resetForProviderGeneration(generation);
    }
    _fullState = nextState;
    _hasReceivedFullState = true;
    _acceptedProviderGeneration = generation;
    _expectedProviderGeneration = null;
    refreshCapabilities(generation);
    return commitCurrentState() ? _fullState : null;
  }

  if (!isRecord(envelope.changed)) return null;
  if (envelope.removed !== undefined && (!Array.isArray(envelope.removed) || !envelope.removed.every((key) => typeof key === 'string'))) return null;
  if (
    (envelope.changed.stateContractVersion !== undefined && envelope.changed.stateContractVersion !== 1)
    || (envelope.changed.providerGeneration !== undefined && envelope.changed.providerGeneration !== generation)
    || (envelope.removed ?? []).includes('stateContractVersion')
    || (envelope.removed ?? []).includes('providerGeneration')
  ) return null;
  const changed = envelope.changed;
  const removed = (envelope.removed ?? []) as string[];
  const currentState = _fullState;
  const candidate = currentState ? { ...currentState, ...changed } : null;
  if (candidate) {
    for (const key of removed) delete candidate[key];
    syncEnvelopeRevisions(candidate, envelope);
    if (!isValidServerState(candidate)) return null;
    if (capabilitiesMatchGeneration(generation) && !matchesCurrentCapabilityTopology(candidate as any)) return null;
  }
  if (_acceptedProviderGeneration !== generation || !_hasReceivedFullState || _fullState === null) {
    if (highestSeen === null || generation > highestSeen) resetForProviderGeneration(generation);
    return null;
  }
  const nextState = candidate!;
  if (!isRevisionAcceptable(currentState, nextState, Object.keys(changed))) return null;
  _fullState = nextState;
  return commitCurrentState() ? _fullState : null;
}

_ctrl.onMessage((msg) => {
  if (msg.type === 'state_update' && msg.data) {
    const state = applyDeltaEnvelope(msg.data as Record<string, unknown>);
    if (state) {
      setRadioState(state as any);
      setHttpConnected(true);
      markStateUpdated();
    }
  }
  // Radio reconnect/degraded status (MOR-594 backend event, consumed for
  // the StatusBar radio indicator — MOR-620). Shape:
  // { type: 'event', name: 'connection_status',
  //   data: { state, attempt, next_retry_seconds } }
  if (msg.type === 'event') {
    const ev = msg as { name?: string; data?: Record<string, unknown> };
    if (ev.name === 'connection_status') {
      const state = ev.data?.state;
      if (typeof state === 'string') setRadioStatus(state);
    }
  }
  // Companion-injected state (RC-28 tuning step, etc.)
  if (msg.type === 'companion_state') {
    const raw = msg as unknown as Record<string, unknown>;
    const stepHz = raw['tuning_step_hz'];
    if (typeof stepHz === 'number' && stepHz > 0) {
      // Lazy import to avoid circular dependency.
      import('../stores/tuning.svelte').then((m) => m.setTuningStepFromCompanion(stepHz));
    }
  }
});

export function connect(url: string = '/api/v1/ws') {
  const token = typeof globalThis.localStorage?.getItem === 'function'
    ? globalThis.localStorage.getItem('rigplane-auth-token')
    : null;
  const wsUrl = token ? `${url}?token=${encodeURIComponent(token)}` : url;
  _ctrl.connect(wsUrl);
}

/** Send a raw JSON message (e.g. subscribe) and register it for re-send on reconnect. */
export function sendRaw(msg: Record<string, unknown>): boolean {
  if (msg.type === 'subscribe') {
    _ctrl.setSubscribeMessage(msg);
  }
  return _ctrl.send(msg as any);
}

export function disconnect() {
  _ctrl.disconnect();
}

export function onCommandDelivery(handler: CommandDeliveryHandler): () => void {
  return _ctrl.onCommandDelivery(handler);
}

export function onControlSessionTransition(handler: ControlSessionTransitionHandler): () => void {
  return _ctrl.onSessionTransition(handler);
}

export function sendCommand(name: string, params: Record<string, unknown> = {}, id?: string): boolean {
  if (!isLiveRadioAvailable() && pttIntent(name, params) !== 'off') {
    console.warn('[cmd] blocked while radio health is degraded', name);
    return false;
  }
  // Auto-optimistic: apply UI patch immediately before sending
  try { _applyOptimistic(name, params); } catch (e) { console.warn('[optimistic]', e); }
  return _ctrl.send({
    type: 'cmd',
    name,
    id: id ?? makeCommandId(),
    params,
  });
}

/** Auto-optimistic update mapping: command → state patch */
function _applyOptimistic(name: string, params: Record<string, unknown>): void {
  switch (name) {
    case 'set_freq':
      if (typeof params.freq === 'number') patchActiveReceiver({ freqHz: params.freq });
      break;
    case 'set_mode':
      if (typeof params.mode === 'string') patchActiveReceiver({ mode: params.mode });
      break;
    case 'set_data_mode':
      if (typeof params.mode === 'number') patchActiveReceiver({ dataMode: params.mode });
      break;
    case 'set_filter':
      if (typeof params.filter === 'string') {
        const n = parseInt((params.filter as string).replace('FIL', ''), 10);
        if (n >= 1 && n <= 3) patchActiveReceiver({ filter: n });
      }
      break;
    case 'set_nb':
      if (typeof params.on === 'boolean') {
        const patch: Record<string, unknown> = { nb: params.on };
        if (!params.on) patch.nbLevel = 0;
        patchActiveReceiver(patch);
      }
      break;
    case 'set_nr':
      if (typeof params.on === 'boolean') {
        const patch: Record<string, unknown> = { nr: params.on };
        if (!params.on) patch.nrLevel = 0;
        patchActiveReceiver(patch);
      }
      break;
    case 'set_nb_level':
      if (typeof params.level === 'number') {
        patchActiveReceiver({ nbLevel: params.level, nb: params.level > 0 });
      }
      break;
    case 'set_nr_level':
      if (typeof params.level === 'number') {
        patchActiveReceiver({ nrLevel: params.level, nr: params.level > 0 });
      }
      break;
    case 'set_af_level':
      if (typeof params.level === 'number') patchActiveReceiver({ afLevel: params.level });
      break;
    case 'set_rf_gain':
      if (typeof params.level === 'number') patchActiveReceiver({ rfGain: params.level });
      break;
    case 'set_squelch':
      if (typeof params.level === 'number') patchActiveReceiver({ squelch: params.level });
      break;
    case 'set_att':
      if (typeof params.level === 'number') patchActiveReceiver({ att: params.level });
      break;
    case 'set_attenuator':
      if (typeof params.db === 'number') patchActiveReceiver({ att: params.db });
      else if (typeof params.level === 'number') patchActiveReceiver({ att: params.level });
      break;
    case 'set_preamp':
      if (typeof params.level === 'number') patchActiveReceiver({ preamp: params.level });
      break;
    case 'set_filter_width':
      if (typeof params.width === 'number') patchActiveReceiver({ filterWidth: params.width });
      break;
    case 'set_digisel':
      if (typeof params.on === 'boolean') patchActiveReceiver({ digisel: params.on });
      break;
    case 'set_ip_plus':
    case 'set_ipplus':  // backward-compat alias
      if (typeof params.on === 'boolean') patchActiveReceiver({ ipplus: params.on });
      break;
    case 'set_dual_watch':
      if (typeof params.on === 'boolean') patchRadioState({ dualWatch: params.on });
      break;
    case 'set_split':
      if (typeof params.on === 'boolean') patchRadioState({ split: params.on });
      break;
    case 'set_rit_status':
      if (typeof params.on === 'boolean') patchRadioState({ ritOn: params.on });
      break;
    case 'set_rit_tx_status':
      if (typeof params.on === 'boolean') patchRadioState({ ritTx: params.on });
      break;
    case 'set_rit_frequency':
      if (typeof params.freq === 'number') patchRadioState({ ritFreq: params.freq });
      break;
    case 'set_tuner_status':
      if (typeof params.value === 'number') patchRadioState({ tunerStatus: params.value });
      break;
    case 'set_mic_gain':
      if (typeof params.level === 'number') patchRadioState({ micGain: params.level });
      break;
    case 'set_cw_pitch':
      if (typeof params.value === 'number') patchRadioState({ cwPitch: params.value });
      break;
    case 'set_key_speed':
      if (typeof params.speed === 'number') patchRadioState({ keySpeed: params.speed });
      break;
    case 'set_break_in':
      if (typeof params.mode === 'number') patchRadioState({ breakIn: params.mode });
      break;
    case 'set_vox':
      if (typeof params.on === 'boolean') patchRadioState({ voxOn: params.on });
      break;
    case 'set_compressor':
    case 'set_comp':
      if (typeof params.on === 'boolean') patchRadioState({ compressorOn: params.on });
      break;
    case 'set_compressor_level':
      if (typeof params.level === 'number') patchRadioState({ compressorLevel: params.level });
      break;
    case 'set_monitor':
      if (typeof params.on === 'boolean') patchRadioState({ monitorOn: params.on });
      break;
    case 'set_monitor_gain':
      if (typeof params.level === 'number') patchRadioState({ monitorGain: params.level });
      break;
    case 'set_vfo':
    case 'select_vfo':  // backward-compat alias
      if (typeof params.vfo === 'string') {
        const vfo = params.vfo.toUpperCase();
        if (vfo === 'A' || vfo === 'B') {
          patchActiveReceiver({ activeSlot: vfo });
        } else if (vfo === 'MAIN' || vfo === 'SUB') {
          patchRadioState({ active: vfo });
        } else if (vfo === 'VFOA' || vfo === 'VFOB') {
          // Legacy dual-receiver aliases retain their historical meaning.
          patchRadioState({ active: vfo === 'VFOB' ? 'SUB' : 'MAIN' });
        }
      }
      break;

    case 'set_scope_mode': {
      const sm = getRadioState();
      if (sm?.scopeControls && typeof params.mode === 'number') {
        patchRadioState({ scopeControls: { ...sm.scopeControls, mode: params.mode } });
      }
      break;
    }
    case 'set_scope_span': {
      const ss = getRadioState();
      if (ss?.scopeControls && typeof params.span === 'number') {
        patchRadioState({ scopeControls: { ...ss.scopeControls, span: params.span } });
      }
      break;
    }
    case 'set_scope_hold': {
      const sh = getRadioState();
      if (sh?.scopeControls && typeof params.on === 'boolean') {
        patchRadioState({ scopeControls: { ...sh.scopeControls, hold: params.on } });
      }
      break;
    }
    case 'set_scope_ref': {
      const sr = getRadioState();
      if (sr?.scopeControls && typeof params.ref === 'number') {
        patchRadioState({ scopeControls: { ...sr.scopeControls, refDb: params.ref } });
      }
      break;
    }

    case 'set_antenna_1':
      // IC-7610: 0x12 0x00 selects ANT1 and the data byte encodes RX-ANT.
      patchRadioState({ txAntenna: 1, rxAntenna1: !!params.on });
      break;
    case 'set_antenna_2':
      patchRadioState({ txAntenna: 2, rxAntenna2: !!params.on });
      break;
    case 'set_rx_antenna_ant1':
      if (typeof params.on === 'boolean') patchRadioState({ txAntenna: 1, rxAntenna1: params.on });
      break;
    case 'set_rx_antenna_ant2':
      if (typeof params.on === 'boolean') patchRadioState({ txAntenna: 2, rxAntenna2: params.on });
      break;
  }
}

export function onMessage(handler: MessageHandler): () => void {
  return _ctrl.onMessage(handler);
}

/** @deprecated Use onMessage */
export const addMessageHandler = onMessage;

export function isConnected(): boolean {
  return _ctrl.isConnected();
}

// ─── Named channel registry (scope / audio) ────────────────────────────────

const _channels = new Map<string, WsChannel>();

export function getChannel(name: string): WsChannel {
  let ch = _channels.get(name);
  if (!ch) {
    ch = new WsChannel();
    _channels.set(name, ch);
  }
  return ch;
}

/** Disconnect the control channel and all named channels (scope, etc.). */
export function disconnectAll(): void {
  _ctrl.disconnect();
  for (const ch of _channels.values()) {
    ch.disconnect();
  }
}

/** Reconnect the control channel and all previously-connected named channels. */
export function reconnectAll(): void {
  _ctrl.reconnect();
  for (const ch of _channels.values()) {
    ch.reconnect();
  }
}
