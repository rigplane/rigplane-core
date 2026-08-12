import type { WsCommand, WsIncoming } from '../types/protocol';
import { makeCommandId } from '../types/protocol';
import { isLiveRadioAvailable, setWsConnected, markStateUpdated, setReconnecting, setRadioStatus } from '../stores/connection.svelte';
import { isValidServerState, matchesCurrentCapabilityTopology, resetRadioState, setRadioState } from '../stores/radio.svelte';
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
  cancelled?: boolean;
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
type PendingNonPttCommand = { command: WsCommand; originalEpoch: number };
type TrackedNonPttCommand = TrackedPttCommand & { eventEpoch: number };

const BACKOFF_BASE_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;
const HEARTBEAT_TIMEOUT_MS = 30_000;  // server may be idle on control WS
const KEEPALIVE_INTERVAL_MS = 15_000; // send ping to prevent idle timeout
const MAX_QUEUE_SIZE = 20;
const MAX_TRACKED_PTT_COMMANDS = 100;
const MAX_TRACKED_NON_PTT_COMMANDS = 100;

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

function isTabHidden(): boolean {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden';
}

// ─── Close observability (MOR-1424) ─────────────────────────────────────────
//
// The client previously discarded the WS CloseEvent entirely (onerror just
// called ws.close()), making reconnect-churn incidents impossible to
// attribute. This records the most recent close across all channels
// (control + named channels) for diagnostics.
export interface WsCloseInfo {
  code: number;
  reason: string;
  wasClean: boolean;
  url: string;
  timestamp: number;
}

let _lastCloseInfo: WsCloseInfo | null = null;

export function getLastCloseInfo(): WsCloseInfo | null {
  return _lastCloseInfo;
}

export class WsChannel {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private keepaliveTimer: ReturnType<typeof setInterval> | null = null;
  private attempt = 0;
  private intentionalClose = false;
  private sendQueue: PendingNonPttCommand[] = [];
  private pendingPttRelease: PendingPttRelease | null = null;
  private transportEpoch = 0;
  private trackedPttCommands = new Map<string, TrackedPttCommand>();
  private trackedNonPttCommands = new Map<string, TrackedNonPttCommand>();
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
  // MOR-1424: while the tab is hidden, the browser throttles/never-completes
  // the WS handshake — retrying on a growing backoff just burns attempts
  // against a connection that can't succeed yet. This flag remembers that a
  // reconnect is owed once the tab becomes visible again.
  private reconnectPendingVisibility = false;

  constructor() {
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', this._onVisibilityChange);
    }
  }

  /** Register a message to re-send automatically on every (re)connect. */
  setSubscribeMessage(msg: Record<string, unknown>) {
    this._subscribeMsg = msg;
  }

  private _onVisibilityChange = () => {
    if (isTabHidden()) {
      // Pause the retry loop — cancel any already-scheduled reconnect, but
      // never touch a healthy open (or in-flight connecting) socket.
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
        this.reconnectPendingVisibility = true;
      }
      return;
    }
    if (this.reconnectPendingVisibility && !this.intentionalClose) {
      this.reconnectPendingVisibility = false;
      this.attempt = 0;
      this._open();
    }
  };

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
    // A fresh explicit connect supersedes any stale "reconnect once visible"
    // debt from a previous, unrelated hidden episode.
    this.reconnectPendingVisibility = false;
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
      const queued = this.sendQueue.splice(0);
      for (const pending of queued) this._sendNonPtt(ws, pending, socketEpoch);
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

    ws.onclose = (event: CloseEvent) => {
      _lastCloseInfo = {
        code: event?.code ?? 0,
        reason: event?.reason ?? '',
        wasClean: event?.wasClean ?? false,
        url: this.url,
        timestamp: Date.now(),
      };
      console.info('[ws] closed', _lastCloseInfo);
      this._clearHeartbeat();
      this.trackedNonPttCommands.clear();
      this.ws = null;
      this.setState('disconnected');
      if (!this.intentionalClose) {
        if (isTabHidden()) {
          // Don't burn attempts against a throttled handshake — resume from
          // the visibilitychange listener once the tab is visible again.
          this.reconnectPendingVisibility = true;
        } else {
          const delay = calcBackoff(this.attempt++);
          this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this._open();
          }, delay);
        }
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
    this.trackedNonPttCommands.clear();
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
      return this._sendNonPtt(
        this.ws,
        { command: cmd, originalEpoch: this.transportEpoch },
        this.transportEpoch,
      );
    }
    const intent = pttIntent(cmd.name, cmd.params);
    if (intent === 'on') {
      return false;
    }
    if (intent === 'off') {
      this.pendingPttRelease = { command: cmd, originalEpoch: this.transportEpoch };
      return false;
    }
    if (!IDEMPOTENT_TYPES.has(cmd.name)) {
      this._rejectNonPtt(cmd.id, this.transportEpoch, 'offline non-idempotent command rejected');
      return false;
    }
    // Deduplicate idempotent commands — keep only the latest value
    const superseded = this.sendQueue.find((pending) => pending.command.name === cmd.name);
    if (superseded) {
      this._rejectNonPtt(superseded.command.id, superseded.originalEpoch, 'superseded by newer offline command');
      this.sendQueue = this.sendQueue.filter((pending) => pending.command.name !== cmd.name);
    }
    this.sendQueue.push({ command: cmd, originalEpoch: this.transportEpoch });
    // Drop oldest if over limit
    if (this.sendQueue.length > MAX_QUEUE_SIZE) {
      const dropped = this.sendQueue.shift()!;
      this._rejectNonPtt(dropped.command.id, dropped.originalEpoch, 'offline command queue capacity exceeded');
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

  /**
   * MOR-1422: a client-synthesized notification, delivered through the SAME
   * bus a server `{"type":"notification"}` frame uses (the `errNote`
   * pattern above) — so the one shipped toast surface (`components/shared/
   * Toast.svelte`) renders it with no changes of its own. `code` is resolved
   * to `core.toast.<code>` by `messageFromReasonCode`; `message` is the
   * English fallback for a caller that never supplied one.
   */
  emitLocalNotification(level: 'info' | 'warning' | 'error', message: string, code: string): void {
    const note: WsIncoming = { type: 'notification', level, message, code, category: 'command' };
    this.messageHandlers.forEach((h) => h(note));
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

  get sessionEpoch(): number {
    return this.transportEpoch;
  }

  rejectNonPtt(commandId: string, error: string): void {
    this._rejectNonPtt(commandId, this.transportEpoch, error);
  }

  cancelNonPtt(error: string): void {
    for (const tracked of this.trackedNonPttCommands.values()) {
      this._rejectNonPtt(tracked.command.id, tracked.originalEpoch, error, this.transportEpoch, true);
    }
    this.trackedNonPttCommands.clear();
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

  private _sendNonPtt(ws: WebSocket, pending: PendingNonPttCommand, eventEpoch: number): boolean {
    if (this.trackedNonPttCommands.size >= MAX_TRACKED_NON_PTT_COMMANDS) {
      this._rejectNonPtt(
        pending.command.id, pending.originalEpoch, 'delivery tracking capacity exceeded', eventEpoch,
      );
      return false;
    }
    try {
      ws.send(JSON.stringify(pending.command));
    } catch (error) {
      this._rejectNonPtt(
        pending.command.id, pending.originalEpoch,
        error instanceof Error ? error.message : 'WebSocket send failed', eventEpoch,
      );
      return false;
    }
    const tracked: TrackedNonPttCommand = { ...pending, eventEpoch, seen: new Set() };
    this.trackedNonPttCommands.set(pending.command.id, tracked);
    this._emitTracked(tracked, 'transport-sent', tracked.eventEpoch);
    return true;
  }

  private _emitCommandResult(raw: Record<string, unknown>, eventEpoch: number): void {
    const id = raw.id;
    if (typeof id !== 'string') return;
    const tracked = this.trackedPttCommands.get(id);
    if (tracked && raw.type === 'ack') this._emitTracked(tracked, 'ack', eventEpoch);
    else if (raw.type === 'response') {
      if (tracked) this._emitTracked(tracked, raw.ok === false ? 'response-error' : 'response-ok', eventEpoch);
    } else if (raw.type === 'error' || raw.status === 'error') {
      if (tracked) this._emitTracked(tracked, 'error', eventEpoch, String(raw.message ?? raw.error ?? 'Command failed'));
    }
    const generic = this.trackedNonPttCommands.get(id);
    if (!generic || generic.eventEpoch !== eventEpoch) return;
    if (raw.type === 'ack') this._emitTracked(generic, 'ack', generic.eventEpoch);
    else if (raw.type === 'response') {
      this._emitTracked(
        generic,
        raw.ok === false ? 'response-error' : 'response-ok',
        generic.eventEpoch,
        raw.ok === false ? String(raw.message ?? raw.error ?? 'Command failed') : undefined,
      );
      this.trackedNonPttCommands.delete(id);
    } else if (raw.type === 'error' || raw.status === 'error') {
      this._emitTracked(generic, 'error', generic.eventEpoch, String(raw.message ?? raw.error ?? 'Command failed'));
      this.trackedNonPttCommands.delete(id);
    }
  }

  private _rejectNonPtt(
    commandId: string, originalEpoch: number, error: string,
    eventEpoch = this.transportEpoch, cancelled = false,
  ): void {
    this._emitDelivery({
      commandId, kind: 'error', originalEpoch, eventEpoch, error,
      ...(cancelled ? { cancelled } : {}),
    });
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
    // MOR-1526 (F1 verifier finding): a WS drop that never gets a terminal
    // `connection_status` event (server restart; reconnect_loop hitting
    // asyncio.CancelledError on either exit path in radio_reconnect.py)
    // used to leave `radioStatus` stuck at 'connecting'/'reconnecting'
    // forever — the reconnect overlay then permanently suppressed the
    // honest steady-state facts once the session reconnected to a server
    // that (by the ticket's own premise) never emits `connection_status`
    // on a healthy session. Reset it here so the overlay clears across
    // every disconnect path, not just the ones that happen to emit a
    // terminal event.
    //
    // R2 ruling: rigConnected/radioReady/radioHealth are deliberately NOT
    // reset here (unlike an earlier revision of this fix) — those three
    // also gate `isLiveRadioAvailable()`/`sendCommand()`'s offline
    // queue-and-replay path (below, IDEMPOTENT_TYPES dedup, MAX_QUEUE_SIZE,
    // sendQueue replay), and forcing them false for the whole offline
    // window made that command-safety path unreachable in production — a
    // side effect this display fix must not cause. The chip's up-edge
    // honesty (F2) is instead handled by `factsObservedThisSession` in
    // connection.svelte.ts, which does not touch these fields' values.
    setRadioStatus('disconnected');
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
  _ctrl.cancelNonPtt('provider session replaced');
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

export function getControlSession(): ControlSessionTransition {
  return { state: _ctrl.state, epoch: _ctrl.sessionEpoch };
}

/**
 * MOR-1422: the refusal below fires per COMMAND — a held control (a
 * jog wheel, a repeated keypress) can call `sendCommand` many times a
 * second, and a toast per call would bury the one useful signal ("your
 * commands are not reaching the radio") under a flood. This is a burst
 * debounce, not a rate limit on the refusal itself: `sendCommand` keeps
 * returning `false` for every call, only the notice is throttled.
 */
const REFUSAL_NOTICE_DEBOUNCE_MS = 3_000;
let lastRefusalNoticeAt = -Infinity;

export function sendCommand(
  name: string,
  params: Record<string, unknown> = {},
  id?: string,
): boolean {
  const commandId = id ?? makeCommandId();
  // This health gate only speaks for a LIVE transport. While the socket is
  // open, rigConnected/radioReady/radioHealth are continuously refreshed by
  // state_update, so `!isLiveRadioAvailable()` means the radio link is
  // known-bad — refuse loudly rather than send into a black hole. While the
  // socket is down those same facts were just reset by the 'disconnected'
  // transition above (MOR-1526 F1/F2) and say nothing about the radio; the
  // transport-offline case is governed by WsChannel.send's own offline
  // policy (idempotent keep-latest queue / non-idempotent reject /
  // pendingPttRelease), which predates this gate and stays authoritative.
  if (_ctrl.isConnected() && !isLiveRadioAvailable() && pttIntent(name, params) !== 'off') {
    console.warn('[cmd] blocked while radio health is degraded', name);
    if (pttIntent(name, params) === null) {
      _ctrl.rejectNonPtt(commandId, 'radio health is degraded');
    }
    const now = Date.now();
    if (now - lastRefusalNoticeAt >= REFUSAL_NOTICE_DEBOUNCE_MS) {
      lastRefusalNoticeAt = now;
      _ctrl.emitLocalNotification(
        'warning', 'Command not sent — link to the radio is degraded', 'commandRefusedLinkDegraded',
      );
    }
    return false;
  }
  return _ctrl.send({
    type: 'cmd',
    name,
    id: commandId,
    params,
  });
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
