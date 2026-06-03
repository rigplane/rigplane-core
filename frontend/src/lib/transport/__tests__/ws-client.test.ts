import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { WsCommand, WsMessage } from '../../types/protocol';
import type { ReceiverState, ServerState } from '../../types/state';

type ServerStateWithObservation = ServerState & {
  observationSeq?: number;
  publicStateSeq?: number;
  fieldStatus?: Record<string, unknown>;
};

// ─── Mock store before importing ws-client ──────────────────────────────────
const radioStoreMock = vi.hoisted(() => ({
  current: null as ServerStateWithObservation | null,
}));

vi.mock('../../stores/connection.svelte', () => ({
  setWsConnected: vi.fn(),
  setHttpConnected: vi.fn(),
  markStateUpdated: vi.fn(),
  setReconnecting: vi.fn(),
  isLiveRadioAvailable: vi.fn(() => true),
}));

vi.mock('../../stores/radio.svelte', () => ({
  getRadioState: vi.fn(() => radioStoreMock.current),
  patchActiveReceiver: vi.fn((patch: Partial<import('../../types/state').ReceiverState>) => {
    const current = radioStoreMock.current;
    if (!current) return;
    const receiver = current.active === 'SUB' ? 'sub' : 'main';
    radioStoreMock.current = {
      ...current,
      [receiver]: {
        ...current[receiver],
        ...patch,
      },
    };
  }),
  patchRadioState: vi.fn((patch: Partial<import('../../types/state').ServerState>) => {
    const current = radioStoreMock.current;
    if (!current) return;
    radioStoreMock.current = {
      ...current,
      ...patch,
    };
  }),
  resetRadioState: vi.fn(() => {
    radioStoreMock.current = null;
  }),
  setRadioState: vi.fn((state: ServerStateWithObservation) => {
    const current = radioStoreMock.current;
    const lastRevision = current ? current.stateRevision ?? current.revision : -1;
    const nextRevision = state.stateRevision ?? state.revision;
    const lastFreshnessRevision = current?.freshnessRevision ?? -1;
    const nextFreshnessRevision = state.freshnessRevision ?? 0;
    const lastHealthRevision = current?.healthRevision ?? -1;
    const nextHealthRevision = state.healthRevision ?? 0;
    const lastObservationSeq = current?.observationSeq ?? -1;
    const nextObservationSeq = state.observationSeq ?? 0;
    const lastPublicStateSeq = current?.publicStateSeq ?? -1;
    const nextPublicStateSeq = state.publicStateSeq ?? 0;
    const isReset = lastRevision > 10 && nextRevision < lastRevision / 2;
    const semanticAdvanced = nextRevision > lastRevision;
    const semanticCurrent = nextRevision === lastRevision;
    const metadataAdvanced = semanticCurrent && (
      nextFreshnessRevision > lastFreshnessRevision
      || nextHealthRevision > lastHealthRevision
      || nextObservationSeq > lastObservationSeq
      || nextPublicStateSeq > lastPublicStateSeq
    );
    if (current === null || semanticAdvanced || metadataAdvanced || isReset) {
      radioStoreMock.current = state;
    }
  }),
}));

import { isLiveRadioAvailable, setWsConnected } from '../../stores/connection.svelte';
import { patchActiveReceiver, patchRadioState, resetRadioState, setRadioState } from '../../stores/radio.svelte';

beforeEach(() => {
  radioStoreMock.current = null;
  vi.mocked(isLiveRadioAvailable).mockReturnValue(true);
  vi.mocked(patchActiveReceiver).mockClear();
  vi.mocked(patchRadioState).mockClear();
  vi.mocked(resetRadioState).mockClear();
  vi.mocked(setRadioState).mockClear();
});

// ─── Minimal WebSocket mock ──────────────────────────────────────────────────
type WsEventName = 'open' | 'message' | 'close' | 'error';

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readyState = MockWebSocket.CONNECTING;
  binaryType = 'blob';
  url: string;
  sent: string[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  // Test helpers
  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  simulateMessage(data: string | ArrayBuffer) {
    this.onmessage?.({ data } as MessageEvent);
  }

  simulateClose() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  simulateError() {
    this.onerror?.();
  }
}

const instances: MockWebSocket[] = [];

function makeReceiver(overrides: Partial<ReceiverState> = {}): ReceiverState {
  return {
    freqHz: 14074000,
    mode: 'USB',
    filter: 1,
    dataMode: 0,
    sMeter: 0,
    att: 0,
    preamp: 0,
    nb: false,
    nr: false,
    afLevel: 128,
    rfGain: 128,
    squelch: 0,
    ...overrides,
  };
}

function makeState(
  overrides: Partial<ServerStateWithObservation> & {
    main?: Partial<ReceiverState>;
    sub?: Partial<ReceiverState>;
    connection?: Partial<ServerState['connection']>;
  } = {},
): ServerStateWithObservation {
  const { main, sub, connection, ...topLevel } = overrides;
  const revision = topLevel.stateRevision ?? topLevel.revision ?? 1;
  return {
    revision,
    stateRevision: revision,
    freshnessRevision: topLevel.freshnessRevision ?? 1,
    healthRevision: topLevel.healthRevision ?? 1,
    observationSeq: topLevel.observationSeq ?? revision,
    publicStateSeq: topLevel.publicStateSeq,
    updatedAt: '2026-06-03T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: makeReceiver(main),
    sub: makeReceiver({ freqHz: 7074000, ...sub }),
    connection: {
      rigConnected: true,
      radioReady: true,
      controlConnected: true,
      ...connection,
    },
    ...topLevel,
  };
}

function fullEnvelope(state: ServerStateWithObservation): Record<string, unknown> {
  return {
    type: 'full',
    data: state,
    revision: state.revision,
    stateRevision: state.stateRevision,
    freshnessRevision: state.freshnessRevision,
    healthRevision: state.healthRevision,
    observationSeq: state.observationSeq,
    publicStateSeq: state.publicStateSeq,
    transportSeq: state.transportSeq,
  };
}

function deltaEnvelope(
  state: ServerStateWithObservation,
  changed: Record<string, unknown>,
  removed: string[] = [],
): Record<string, unknown> {
  return {
    type: 'delta',
    changed,
    removed,
    revision: state.revision,
    stateRevision: state.stateRevision,
    freshnessRevision: state.freshnessRevision,
    healthRevision: state.healthRevision,
    observationSeq: state.observationSeq,
    publicStateSeq: state.publicStateSeq,
    transportSeq: state.transportSeq,
  };
}

function sendStateUpdate(socket: MockWebSocket, data: Record<string, unknown>): void {
  socket.simulateMessage(JSON.stringify({ type: 'state_update', data }));
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('WsChannel', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    instances.length = 0;
    vi.useFakeTimers();
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error mock
    globalThis.WebSocket = MockWebSocket;
    vi.mocked(setWsConnected).mockClear();
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
  });

  it('connects and updates wsConnected store', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    ch.onStateChange((s) => {
      if (s === 'connected') setWsConnected(true);
      if (s === 'disconnected') setWsConnected(false);
    });

    expect(ch.state).toBe('disconnected');
    ch.connect('ws://test/api/v1/ws');
    expect(ch.state).toBe('connecting');

    instances[0].simulateOpen();
    expect(ch.state).toBe('connected');
    expect(ch.isConnected()).toBe(true);
    expect(setWsConnected).toHaveBeenCalledWith(true);
  });

  it('routes JSON messages to onMessage handlers', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    const received: WsMessage[] = [];
    ch.onMessage((m) => received.push(m));

    ch.connect('ws://test');
    instances[0].simulateOpen();
    instances[0].simulateMessage(JSON.stringify({ type: 'ack', id: 'abc' }));

    expect(received).toHaveLength(1);
    expect(received[0].type).toBe('ack');
  });

  it('routes binary messages to onBinary handlers', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    const buffers: ArrayBuffer[] = [];
    ch.onBinary((b) => buffers.push(b));

    ch.connect('ws://test');
    instances[0].simulateOpen();
    const buf = new ArrayBuffer(8);
    instances[0].simulateMessage(buf);

    expect(buffers).toHaveLength(1);
    expect(buffers[0]).toBe(buf);
  });

  it('does not crash on malformed JSON frames', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    const received: WsMessage[] = [];
    ch.onMessage((m) => received.push(m));

    ch.connect('ws://test');
    instances[0].simulateOpen();
    instances[0].simulateMessage('not-json{{');

    expect(received).toHaveLength(0);
  });

  it('buffers commands when disconnected and drains on reconnect', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();

    const cmd: WsCommand = { type: 'cmd', name: 'set_freq', id: '1', params: { freqHz: 14074000 } };
    const queued = ch.send(cmd);
    expect(queued).toBe(false);

    ch.connect('ws://test');
    instances[0].simulateOpen();

    // queue drained on open
    expect(instances[0].sent).toHaveLength(1);
    expect(JSON.parse(instances[0].sent[0])).toMatchObject({ type: 'cmd', name: 'set_freq' });
  });

  it('reconnects with exponential backoff after close', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();

    ch.connect('ws://test');
    instances[0].simulateOpen();
    instances[0].simulateClose();

    expect(ch.state).toBe('disconnected');
    expect(instances).toHaveLength(1);

    // 1st backoff = 1s ± 20% jitter
    vi.advanceTimersByTime(1300);
    expect(instances).toHaveLength(2);
    expect(ch.state).toBe('reconnecting');

    instances[1].simulateOpen();
    expect(ch.state).toBe('connected');
  });

  it('does NOT reconnect after intentional disconnect()', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();

    ch.connect('ws://test');
    instances[0].simulateOpen();
    ch.disconnect();

    vi.advanceTimersByTime(5000);
    expect(instances).toHaveLength(1);
    expect(ch.state).toBe('disconnected');
  });

  it('keeps the connection alive by sending periodic ping frames', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();

    ch.connect('ws://test');
    instances[0].simulateOpen();
    expect(ch.state).toBe('connected');

    // advance past two keepalive intervals without incoming messages
    vi.advanceTimersByTime(30001);

    const pingFrames = instances[0].sent
      .map((data) => JSON.parse(data))
      .filter((msg) => msg.type === 'ping');

    expect(pingFrames.length).toBeGreaterThanOrEqual(2);
    expect(ch.state).toBe('connected');
    expect(instances).toHaveLength(1);
  });

  it('resets heartbeat timer on each incoming message', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();

    ch.connect('ws://test');
    instances[0].simulateOpen();

    // keep feeding messages — should not disconnect
    for (let i = 0; i < 5; i++) {
      vi.advanceTimersByTime(5000);
      instances[0].simulateMessage(JSON.stringify({ type: 'ack', id: `${i}` }));
    }

    expect(ch.state).toBe('connected');
    expect(instances).toHaveLength(1);
  });

  it('removes message handler via returned cleanup fn', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    const received: WsMessage[] = [];
    const unsub = ch.onMessage((m) => received.push(m));

    ch.connect('ws://test');
    instances[0].simulateOpen();
    instances[0].simulateMessage(JSON.stringify({ type: 'ack', id: '1' }));
    expect(received).toHaveLength(1);

    unsub();
    instances[0].simulateMessage(JSON.stringify({ type: 'ack', id: '2' }));
    expect(received).toHaveLength(1); // no new messages
  });
});

describe('control channel singleton', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    instances.length = 0;
    vi.useFakeTimers();
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error mock
    globalThis.WebSocket = MockWebSocket;
    vi.mocked(setWsConnected).mockClear();
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
    vi.resetModules();
  });

  it('applies optimistic data mode updates before sending', async () => {
    const { sendCommand } = await import('../ws-client');

    sendCommand('set_data_mode', { mode: 2, receiver: 0 });

    expect(patchActiveReceiver).toHaveBeenCalledWith({ dataMode: 2 });
  });

  it('sendCommand returns false and queues when not connected', async () => {
    const { sendCommand, isConnected } = await import('../ws-client');
    expect(isConnected()).toBe(false);
    const result = sendCommand('ptt', { state: true });
    expect(result).toBe(false);
  });

  it('sendCommand blocks live-radio commands while radio health is degraded', async () => {
    vi.mocked(isLiveRadioAvailable).mockReturnValue(false);
    vi.mocked(patchActiveReceiver).mockClear();
    const { sendCommand } = await import('../ws-client');

    const result = sendCommand('set_freq', { freq: 14074000, receiver: 0 });

    expect(result).toBe(false);
    expect(patchActiveReceiver).not.toHaveBeenCalled();
  });

  it('getChannel returns the same instance for the same name', async () => {
    const { getChannel } = await import('../ws-client');
    const a = getChannel('scope');
    const b = getChannel('scope');
    expect(a).toBe(b);
  });

  it('rejects a stale delta without contaminating the accumulated full state', async () => {
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 5, ptt: false, split: false })));
    vi.mocked(setRadioState).mockClear();

    sendStateUpdate(instances[0], deltaEnvelope(makeState({ revision: 4 }), { ptt: true }));

    expect(setRadioState).not.toHaveBeenCalled();
    expect(radioStoreMock.current?.ptt).toBe(false);

    sendStateUpdate(instances[0], deltaEnvelope(makeState({ revision: 6 }), { split: true }));

    expect(setRadioState).toHaveBeenCalledTimes(1);
    expect(radioStoreMock.current?.revision).toBe(6);
    expect(radioStoreMock.current?.ptt).toBe(false);
    expect(radioStoreMock.current?.split).toBe(true);
  });

  it('rejects an out-of-order equal-revision delta without contaminating the accumulated full state', async () => {
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 5, ptt: false, split: false, dualWatch: false })));
    sendStateUpdate(instances[0], deltaEnvelope(makeState({ revision: 6 }), { split: true }));
    vi.mocked(setRadioState).mockClear();

    sendStateUpdate(instances[0], deltaEnvelope(makeState({ revision: 6 }), { ptt: true }));

    expect(setRadioState).not.toHaveBeenCalled();
    expect(radioStoreMock.current?.ptt).toBe(false);

    sendStateUpdate(instances[0], deltaEnvelope(makeState({ revision: 7 }), { dualWatch: true }));

    expect(setRadioState).toHaveBeenCalledTimes(1);
    expect(radioStoreMock.current?.revision).toBe(7);
    expect(radioStoreMock.current?.ptt).toBe(false);
    expect(radioStoreMock.current?.split).toBe(true);
    expect(radioStoreMock.current?.dualWatch).toBe(true);
  });

  it('applies a valid delta to the accumulated full state and store exactly once', async () => {
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 5, ptt: false })));
    vi.mocked(setRadioState).mockClear();

    sendStateUpdate(instances[0], deltaEnvelope(makeState({ revision: 6 }), { ptt: true }));

    expect(setRadioState).toHaveBeenCalledTimes(1);
    expect(radioStoreMock.current?.revision).toBe(6);
    expect(radioStoreMock.current?.ptt).toBe(true);
  });

  it('merges a valid delta from the accepted server accumulator, not optimistic store state', async () => {
    const { connect, sendCommand } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 5, ptt: false, split: false })));
    sendCommand('ptt', { state: true });
    expect(radioStoreMock.current?.ptt).toBe(true);
    vi.mocked(setRadioState).mockClear();

    sendStateUpdate(instances[0], deltaEnvelope(makeState({ revision: 6 }), { split: true }));

    expect(setRadioState).toHaveBeenCalledTimes(1);
    expect(vi.mocked(setRadioState).mock.calls[0][0].ptt).toBe(false);
    expect(radioStoreMock.current?.revision).toBe(6);
    expect(radioStoreMock.current?.ptt).toBe(false);
    expect(radioStoreMock.current?.split).toBe(true);
  });

  it('accepts an equal semantic revision when only healthRevision advances', async () => {
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(
      instances[0],
      fullEnvelope(makeState({ revision: 5, stateRevision: 5, healthRevision: 1 })),
    );
    vi.mocked(setRadioState).mockClear();

    sendStateUpdate(
      instances[0],
      deltaEnvelope(makeState({ revision: 5, stateRevision: 5, healthRevision: 2 }), {}),
    );

    expect(setRadioState).toHaveBeenCalledTimes(1);
    expect(radioStoreMock.current?.stateRevision).toBe(5);
    expect(radioStoreMock.current?.healthRevision).toBe(2);
  });

  it('accepts same-value fieldStatus metadata when only observationSeq advances', async () => {
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(
      instances[0],
      fullEnvelope(makeState({
        revision: 5,
        stateRevision: 5,
        freshnessRevision: 1,
        healthRevision: 1,
        observationSeq: 1,
        fieldStatus: {
          'main.freqHz': {
            storePath: 'receiver.main.active.freq_mode.freq_hz',
            observed: true,
            freshness: 'fresh',
            availability: 'available',
            lastObservedMonotonic: 1,
            source: { provider: 'first' },
          },
        },
      })),
    );
    vi.mocked(setRadioState).mockClear();

    const nextFieldStatus = {
      'main.freqHz': {
        storePath: 'receiver.main.active.freq_mode.freq_hz',
        observed: true,
        freshness: 'fresh',
        availability: 'available',
        lastObservedMonotonic: 2,
        source: { provider: 'second' },
      },
    } as const;
    sendStateUpdate(
      instances[0],
      deltaEnvelope(
        makeState({
          revision: 5,
          stateRevision: 5,
          freshnessRevision: 1,
          healthRevision: 1,
          observationSeq: 2,
        }),
        { fieldStatus: nextFieldStatus },
      ),
    );

    expect(setRadioState).toHaveBeenCalledTimes(1);
    expect(radioStoreMock.current?.stateRevision).toBe(5);
    expect(radioStoreMock.current?.freshnessRevision).toBe(1);
    expect(radioStoreMock.current?.observationSeq).toBe(2);
    expect(radioStoreMock.current?.fieldStatus?.['main.freqHz']).toEqual(
      nextFieldStatus['main.freqHz'],
    );
  });

  it('accepts wsClients-only deltas when delivery metadata advances', async () => {
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(
      instances[0],
      fullEnvelope(makeState({
        revision: 5,
        stateRevision: 5,
        freshnessRevision: 1,
        healthRevision: 1,
        observationSeq: 1,
        publicStateSeq: 1,
        transportSeq: 1,
        wsClients: { scope: 0, control: 1, audio: 0 },
      })),
    );
    vi.mocked(setRadioState).mockClear();

    sendStateUpdate(
      instances[0],
      deltaEnvelope(
        makeState({
          revision: 5,
          stateRevision: 5,
          freshnessRevision: 1,
          healthRevision: 1,
          observationSeq: 1,
          publicStateSeq: 2,
          transportSeq: 2,
        }),
        {
          publicStateSeq: 2,
          updatedAt: '2026-06-03T00:00:01Z',
          wsClients: { scope: 0, control: 2, audio: 0 },
        },
      ),
    );

    expect(setRadioState).toHaveBeenCalledTimes(1);
    expect(radioStoreMock.current?.stateRevision).toBe(5);
    expect(radioStoreMock.current?.freshnessRevision).toBe(1);
    expect(radioStoreMock.current?.observationSeq).toBe(1);
    expect(radioStoreMock.current?.publicStateSeq).toBe(2);
    expect(radioStoreMock.current?.transportSeq).toBe(2);
    expect(radioStoreMock.current?.wsClients?.control).toBe(2);
  });

  it('rejects equal-revision semantic deltas even when delivery metadata advances', async () => {
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(
      instances[0],
      fullEnvelope(makeState({
        revision: 5,
        stateRevision: 5,
        publicStateSeq: 1,
        transportSeq: 1,
        ptt: true,
        wsClients: { scope: 0, control: 1, audio: 0 },
      })),
    );
    vi.mocked(setRadioState).mockClear();

    sendStateUpdate(
      instances[0],
      deltaEnvelope(
        makeState({
          revision: 5,
          stateRevision: 5,
          publicStateSeq: 2,
          transportSeq: 2,
          ptt: false,
        }),
        {
          publicStateSeq: 2,
          ptt: false,
          wsClients: { scope: 0, control: 2, audio: 0 },
        },
      ),
    );

    expect(setRadioState).not.toHaveBeenCalled();
    expect(radioStoreMock.current?.publicStateSeq).toBe(1);
    expect(radioStoreMock.current?.wsClients?.control).toBe(1);
    expect(radioStoreMock.current?.ptt).toBe(true);
  });

  it('rejects stale semantic deltas even when observationSeq advances', async () => {
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(
      instances[0],
      fullEnvelope(makeState({ revision: 6, stateRevision: 6, observationSeq: 6, ptt: true })),
    );
    vi.mocked(setRadioState).mockClear();

    sendStateUpdate(
      instances[0],
      deltaEnvelope(
        makeState({ revision: 5, stateRevision: 5, observationSeq: 7, ptt: false }),
        { ptt: false },
      ),
    );

    expect(setRadioState).not.toHaveBeenCalled();
    expect(radioStoreMock.current?.stateRevision).toBe(6);
    expect(radioStoreMock.current?.observationSeq).toBe(6);
    expect(radioStoreMock.current?.ptt).toBe(true);
  });

  it('replaces accumulated state when a full snapshot follows a revision reset', async () => {
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 20, ptt: true, split: true })));
    vi.mocked(setRadioState).mockClear();

    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 1, ptt: false, split: false })));

    expect(setRadioState).toHaveBeenCalledTimes(1);
    expect(radioStoreMock.current?.revision).toBe(1);
    expect(radioStoreMock.current?.ptt).toBe(false);
    expect(radioStoreMock.current?.split).toBe(false);
  });

});

describe('WsChannel send queue', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    instances.length = 0;
    vi.useFakeTimers();
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error mock
    globalThis.WebSocket = MockWebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
    vi.resetModules();
  });

  it('deduplicates idempotent commands (set_freq) — keeps only latest', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();

    // Queue multiple set_freq while disconnected
    ch.send({ type: 'cmd', name: 'set_freq', id: '1', params: { freq: 14000000 } });
    ch.send({ type: 'cmd', name: 'set_freq', id: '2', params: { freq: 14074000 } });
    ch.send({ type: 'cmd', name: 'set_freq', id: '3', params: { freq: 14100000 } });

    ch.connect('ws://test');
    instances[0].simulateOpen();

    // Only the last set_freq should be sent
    expect(instances[0].sent).toHaveLength(1);
    expect(JSON.parse(instances[0].sent[0]).params.freq).toBe(14100000);
  });

  it('drops oldest commands when queue exceeds MAX_QUEUE_SIZE (20)', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();

    for (let i = 0; i < 25; i++) {
      ch.send({ type: 'cmd', name: 'ptt', id: `cmd-${i}`, params: { i } });
    }

    ch.connect('ws://test');
    instances[0].simulateOpen();

    expect(instances[0].sent).toHaveLength(20);
    expect(JSON.parse(instances[0].sent[0]).id).toBe('cmd-5');
  });

  it('handles error response with status field', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    const received: any[] = [];
    ch.onMessage((m) => received.push(m));

    ch.connect('ws://test');
    instances[0].simulateOpen();
    instances[0].simulateMessage(JSON.stringify({ status: 'error', message: 'Command failed' }));

    expect(received).toHaveLength(1);
    expect(received[0].level).toBe('error');
  });
});
