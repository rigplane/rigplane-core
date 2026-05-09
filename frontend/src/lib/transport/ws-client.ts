import type { WsCommand, WsIncoming } from '../types/protocol';
import { makeCommandId } from '../types/protocol';
import { isLiveRadioAvailable, setWsConnected, setHttpConnected, markStateUpdated, setReconnecting } from '../stores/connection.svelte';
import { getRadioState, patchActiveReceiver, patchRadioState, resetRadioState, setRadioState } from '../stores/radio.svelte';

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';
type MessageHandler = (msg: WsIncoming) => void;
type BinaryHandler = (data: ArrayBuffer) => void;
type StateHandler = (state: ConnectionState) => void;

const BACKOFF_BASE_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;
const HEARTBEAT_TIMEOUT_MS = 30_000;  // server may be idle on control WS
const KEEPALIVE_INTERVAL_MS = 15_000; // send ping to prevent idle timeout
const MAX_QUEUE_SIZE = 20;

// Command types where only the latest value matters (last write wins)
const IDEMPOTENT_TYPES = new Set(['set_freq', 'set_mode', 'set_filter']);

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
  private messageHandlers = new Set<MessageHandler>();
  private binaryHandlers = new Set<BinaryHandler>();
  private stateHandlers = new Set<StateHandler>();
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
    this._state = s;
    this.stateHandlers.forEach((h) => h(s));
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
    ws.binaryType = 'arraybuffer';
    this.ws = ws;

    ws.onopen = () => {
      this.attempt = 0;
      this.setState('connected');
      this._resetHeartbeat();
      this._startKeepalive();
      // drain send queue
      const queued = this.sendQueue.splice(0);
      for (const cmd of queued) ws.send(JSON.stringify(cmd));
      // Re-send subscribe on every (re)connect so server pushes state immediately
      if (this._subscribeMsg) ws.send(JSON.stringify(this._subscribeMsg));
    };

    ws.onmessage = (event: MessageEvent) => {
      this._resetHeartbeat();
      if (event.data instanceof ArrayBuffer) {
        this.binaryHandlers.forEach((h) => h(event.data as ArrayBuffer));
      } else {
        try {
          const raw = JSON.parse(event.data as string) as Record<string, unknown>;
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
      this.ws.send(JSON.stringify(cmd));
      return true;
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
    _fullState = {};
    _hasReceivedFullState = false;
    resetRadioState();
  }
});
// Delta state tracking for incremental updates
let _fullState: Record<string, unknown> = {};
let _hasReceivedFullState = false;

function applyDeltaEnvelope(envelope: Record<string, unknown>): Record<string, unknown> | null {
  const deltaType = envelope.type as string;

  if (deltaType === 'full') {
    // Full state refresh — replace everything
    _fullState = { ...(envelope.data as Record<string, unknown>) };
    // Carry revision at top level for setRadioState
    _fullState.revision = envelope.revision as number;
    _hasReceivedFullState = true;
    return _fullState;
  }

  if (deltaType === 'delta') {
    // Reject delta if we haven't received a full state yet
    if (!_hasReceivedFullState) return null;
    // Incremental update — apply changed fields, remove deleted keys
    const changed = (envelope.changed ?? {}) as Record<string, unknown>;
    const removed = (envelope.removed ?? []) as string[];

    Object.assign(_fullState, changed);
    for (const key of removed) {
      delete _fullState[key];
    }
    // Update revision
    _fullState.revision = envelope.revision as number;
    return _fullState;
  }

  // Legacy format (no delta envelope) — plain state object
  return envelope;
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

export function sendCommand(name: string, params: Record<string, unknown> = {}, id?: string): boolean {
  if (!isLiveRadioAvailable()) {
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
    case 'ptt':
      if (typeof params.state === 'boolean') patchRadioState({ ptt: params.state });
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
        const isSub = ['SUB', 'B'].includes(params.vfo.toUpperCase());
        patchRadioState({ active: isSub ? 'SUB' : 'MAIN' });
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
