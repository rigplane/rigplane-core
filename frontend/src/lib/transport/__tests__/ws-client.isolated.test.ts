import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { WsCommand, WsMessage } from '../../types/protocol';
import type { ReceiverState, ServerState } from '../../types/state';
import type { CommandDeliveryEvent, ControlSessionTransition } from '../ws-client';
import { MockWebSocket, instances } from './support/fake-ws-backend';

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
  setRadioStatus: vi.fn(),
  isLiveRadioAvailable: vi.fn(() => true),
  // Under the fast pool's ``isolate: false`` this hoisted mock is shared
  // module-wide; scope-controller.svelte (loaded by a sibling fast-pool
  // test) imports the real ``markScopeFrame``, so it must be stubbed here
  // or that sibling throws "No markScopeFrame export". See issue #771.
  markScopeFrame: vi.fn(),
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
  isValidServerState: vi.fn(() => true),
  matchesCurrentCapabilityTopology: vi.fn(() => true),
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
    const semanticAdvanced = nextRevision > lastRevision;
    const semanticCurrent = nextRevision === lastRevision;
    const metadataAdvanced = semanticCurrent && (
      nextFreshnessRevision > lastFreshnessRevision
      || nextHealthRevision > lastHealthRevision
      || nextObservationSeq > lastObservationSeq
      || nextPublicStateSeq > lastPublicStateSeq
    );
    if (current === null || semanticAdvanced || metadataAdvanced) {
      radioStoreMock.current = state;
    }
  }),
}));

vi.mock('../../stores/capabilities.svelte', () => ({
  capabilitiesMatchGeneration: vi.fn(() => true),
  clearCapabilities: vi.fn(),
  setCapabilities: vi.fn(() => true),
}));

vi.mock('../http-client', () => ({
  fetchCapabilities: vi.fn(() => new Promise(() => {})),
}));

import { isLiveRadioAvailable, setRadioStatus, setWsConnected } from '../../stores/connection.svelte';
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
  const { main, sub, connection, txTarget, ...topLevel } = overrides;
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
    stateContractVersion: 1,
    providerGeneration: 0,
    ...topLevel,
    txTarget: txTarget ?? { status: 'unknown', reason: 'not-observed' },
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
    stateContractVersion: state.stateContractVersion,
    providerGeneration: state.providerGeneration,
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
    stateContractVersion: state.stateContractVersion,
    providerGeneration: state.providerGeneration,
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

  it('emits correlated PTT delivery events without fabricating RF state', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    const events: CommandDeliveryEvent[] = [];
    ch.onCommandDelivery((event) => events.push(event));
    ch.connect('ws://test');
    instances[0].simulateOpen();

    expect(ch.send({ type: 'cmd', name: 'ptt_on', id: 'on', params: {} })).toBe(true);
    instances[0].simulateMessage(JSON.stringify({ type: 'ack', id: 'on' }));
    instances[0].simulateMessage(JSON.stringify({ type: 'ack', id: 'on' }));
    instances[0].simulateMessage(JSON.stringify({ type: 'response', id: 'on', ok: true }));
    expect(ch.send({ type: 'cmd', name: 'ptt_off', id: 'off', params: {} })).toBe(true);
    instances[0].simulateMessage(JSON.stringify({ type: 'response', id: 'off', ok: false }));
    instances[0].simulateMessage(JSON.stringify({ type: 'error', id: 'off', message: 'rejected' }));

    expect(events).toMatchObject([
      { commandId: 'on', kind: 'transport-sent', originalEpoch: 1, eventEpoch: 1 },
      { commandId: 'on', kind: 'ack', originalEpoch: 1, eventEpoch: 1 },
      { commandId: 'on', kind: 'response-ok', originalEpoch: 1, eventEpoch: 1 },
      { commandId: 'off', kind: 'transport-sent', originalEpoch: 1, eventEpoch: 1 },
      { commandId: 'off', kind: 'response-error', originalEpoch: 1, eventEpoch: 1 },
      { commandId: 'off', kind: 'error', originalEpoch: 1, eventEpoch: 1, error: 'rejected' },
    ]);
  });

  it('preserves queued OFF identity and distinguishes stale reconnect events', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    const events: CommandDeliveryEvent[] = [];
    ch.onCommandDelivery((event) => events.push(event));

    ch.send({ type: 'cmd', name: 'ptt_off', id: 'off-1', params: {} });
    ch.send({ type: 'cmd', name: 'ptt', id: 'off-2', params: { state: false } });
    expect(events).toEqual([]);
    ch.connect('ws://test');
    instances[0].simulateOpen();
    expect(events[0]).toMatchObject({
      commandId: 'off-2', kind: 'transport-sent', originalEpoch: 0, eventEpoch: 1,
    });

    instances[0].simulateClose();
    ch.send({ type: 'cmd', name: 'ptt_off', id: 'off-3', params: {} });
    vi.advanceTimersByTime(1300);
    instances[1].simulateOpen();
    instances[0].simulateMessage(JSON.stringify({ type: 'ack', id: 'off-2' }));
    instances[0].simulateMessage(JSON.stringify({ type: 'ack', id: 'off-2' }));
    instances[0].simulateMessage(JSON.stringify({ type: 'response', id: 'off-2', ok: true }));
    instances[0].simulateMessage(JSON.stringify({ type: 'response', id: 'off-2', ok: true }));
    instances[0].simulateMessage(JSON.stringify({ type: 'error', id: 'off-2', message: 'stale' }));
    instances[0].simulateMessage(JSON.stringify({ type: 'error', id: 'off-2', message: 'stale' }));

    expect(events.slice(1)).toMatchObject([
      { commandId: 'off-3', kind: 'transport-sent', originalEpoch: 1, eventEpoch: 2 },
      { commandId: 'off-2', kind: 'ack', originalEpoch: 0, eventEpoch: 1 },
      { commandId: 'off-2', kind: 'response-ok', originalEpoch: 0, eventEpoch: 1 },
      { commandId: 'off-2', kind: 'error', originalEpoch: 0, eventEpoch: 1 },
    ]);
  });

  it('reports a synchronous PTT send failure without a sent event', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    const events: CommandDeliveryEvent[] = [];
    ch.onCommandDelivery((event) => events.push(event));
    ch.connect('ws://test');
    instances[0].simulateOpen();
    instances[0].send = () => { throw new Error('socket failed'); };

    expect(ch.send({ type: 'cmd', name: 'ptt_on', id: 'on', params: {} })).toBe(false);
    expect(events).toEqual([{
      commandId: 'on', kind: 'error', originalEpoch: 1, eventEpoch: 1, error: 'socket failed',
    }]);
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

  it('snapshots control session subscribers for each transition', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    const order: string[] = [];
    let added = false;
    ch.onSessionTransition((transition) => {
      order.push(`first:${transition.state}`);
      if (!added) {
        added = true;
        ch.onSessionTransition((next) => order.push(`late:${next.state}`));
      }
    });
    ch.onSessionTransition((transition) => order.push(`second:${transition.state}`));

    ch.connect('ws://test');
    expect(order).toEqual(['first:connecting', 'second:connecting']);

    instances[0].simulateOpen();
    expect(order).toEqual([
      'first:connecting',
      'second:connecting',
      'first:connected',
      'second:connected',
      'late:connected',
    ]);
  });

  it('serializes reentrant transitions and aborts open work after callback disconnect', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    const order: string[] = [];
    const deliveries: CommandDeliveryEvent[] = [];
    ch.onSessionTransition((transition) => {
      order.push(`first:${transition.state}`);
      if (transition.state === 'connected') ch.disconnect();
    });
    ch.onSessionTransition((transition) => order.push(`second:${transition.state}`));
    ch.onCommandDelivery((event) => deliveries.push(event));

    expect(ch.send({ type: 'cmd', name: 'ptt_off', id: 'off', params: {} })).toBe(false);
    ch.connect('ws://test');
    instances[0].simulateOpen();
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(30_001);

    expect(order).toEqual([
      'first:connecting',
      'second:connecting',
      'first:connected',
      'second:connected',
      'first:disconnected',
      'second:disconnected',
    ]);
    expect(ch.state).toBe('disconnected');
    expect(instances).toHaveLength(1);
    expect(instances[0].sent).toEqual([]);
    expect(deliveries).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
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

  it('publishes each control session epoch before draining a pinned OFF', async () => {
    const {
      connect,
      disconnect,
      onCommandDelivery,
      onControlSessionTransition,
      sendCommand,
    } = await import('../ws-client');
    const transitions: ControlSessionTransition[] = [];
    const order: string[] = [];
    onControlSessionTransition((transition) => {
      transitions.push(transition);
      order.push(`session:${transition.state}:${transition.epoch}`);
    });
    onCommandDelivery((event) => {
      order.push(`delivery:${event.commandId}:${event.eventEpoch}`);
    });

    expect(sendCommand('ptt_off', {}, 'off-1')).toBe(false);
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    instances[0].simulateClose();
    expect(sendCommand('ptt_off', {}, 'off-2')).toBe(false);
    vi.advanceTimersByTime(1300);
    instances[1].simulateOpen();
    disconnect();

    expect(transitions).toEqual([
      { state: 'connecting', epoch: 0 },
      { state: 'connected', epoch: 1 },
      { state: 'disconnected', epoch: 1 },
      { state: 'reconnecting', epoch: 1 },
      { state: 'connected', epoch: 2 },
      { state: 'disconnected', epoch: 2 },
    ]);
    expect(order).toEqual([
      'session:connecting:0',
      'session:connected:1',
      'delivery:off-1:1',
      'session:disconnected:1',
      'session:reconnecting:1',
      'session:connected:2',
      'delivery:off-2:2',
      'session:disconnected:2',
    ]);
  });

  it('unsubscribes the control session transition handler idempotently', async () => {
    const { connect, disconnect, onControlSessionTransition } = await import('../ws-client');
    const transitions: ControlSessionTransition[] = [];
    const unsubscribe = onControlSessionTransition((transition) => transitions.push(transition));

    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    unsubscribe();
    unsubscribe();
    disconnect();
    connect('ws://test/api/v1/ws');
    instances[1].simulateOpen();

    expect(transitions).toEqual([
      { state: 'connecting', epoch: 0 },
      { state: 'connected', epoch: 1 },
    ]);
  });

  it('applies optimistic data mode updates before sending', async () => {
    const { sendCommand } = await import('../ws-client');

    sendCommand('set_data_mode', { mode: 2, receiver: 0 });

    expect(patchActiveReceiver).toHaveBeenCalledWith({ dataMode: 2 });
  });

  it('treats bare VFO B as a slot without switching MAIN to SUB', async () => {
    radioStoreMock.current = makeState({
      active: 'MAIN',
      main: makeReceiver({ activeSlot: 'A' }),
    });
    const { sendCommand } = await import('../ws-client');

    sendCommand('set_vfo', { vfo: 'B' });

    expect(patchActiveReceiver).toHaveBeenCalledWith({ activeSlot: 'B' });
    expect(patchRadioState).not.toHaveBeenCalled();
    expect(radioStoreMock.current?.active).toBe('MAIN');
    expect(radioStoreMock.current?.main.activeSlot).toBe('B');
  });

  it('treats explicit SUB as a receiver selection', async () => {
    radioStoreMock.current = makeState({
      active: 'MAIN',
      main: makeReceiver({ activeSlot: 'A' }),
    });
    const { sendCommand } = await import('../ws-client');

    sendCommand('set_vfo', { vfo: 'SUB' });

    expect(patchRadioState).toHaveBeenCalledWith({ active: 'SUB' });
    expect(patchActiveReceiver).not.toHaveBeenCalled();
    expect(radioStoreMock.current?.active).toBe('SUB');
    expect(radioStoreMock.current?.main.activeSlot).toBe('A');
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

  it('allows open-socket PTT release aliases while radio health is degraded', async () => {
    vi.mocked(isLiveRadioAvailable).mockReturnValue(false);
    radioStoreMock.current = makeState({ ptt: true });
    const { connect, sendCommand } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    expect(sendCommand('ptt_on')).toBe(false);
    expect(sendCommand('ptt', { state: true })).toBe(false);
    expect(sendCommand('ptt_off')).toBe(true);
    expect(radioStoreMock.current?.ptt).toBe(true);
    expect(sendCommand('ptt', { state: false })).toBe(true);
    expect(radioStoreMock.current?.ptt).toBe(true);

    expect(instances[0].sent.map((frame) => JSON.parse(frame))).toMatchObject([
      { name: 'ptt_off' },
      { name: 'ptt', params: { state: false } },
    ]);
  });

  it('coalesces offline PTT release aliases while radio health is degraded', async () => {
    vi.mocked(isLiveRadioAvailable).mockReturnValue(false);
    radioStoreMock.current = makeState({ ptt: true });
    const { connect, sendCommand } = await import('../ws-client');

    expect(sendCommand('ptt_on', {}, 'on-1')).toBe(false);
    expect(sendCommand('ptt', { state: true }, 'on-2')).toBe(false);
    expect(sendCommand('ptt_off', {}, 'off-1')).toBe(false);
    expect(radioStoreMock.current?.ptt).toBe(true);
    expect(sendCommand('ptt', { state: false }, 'off-2')).toBe(false);
    expect(radioStoreMock.current?.ptt).toBe(true);

    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    expect(radioStoreMock.current?.ptt).toBe(true);
    expect(instances[0].sent.map((frame) => JSON.parse(frame))).toEqual([
      { type: 'cmd', name: 'ptt', id: 'off-2', params: { state: false } },
    ]);
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

  it('does not optimistically mutate PTT before authoritative state arrives', async () => {
    const { connect, sendCommand } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 5, ptt: false, split: false })));
    vi.mocked(patchRadioState).mockClear();
    sendCommand('ptt_on');
    sendCommand('ptt', { state: true });
    sendCommand('ptt_off');
    sendCommand('ptt', { state: false });
    expect(patchRadioState).not.toHaveBeenCalled();
    expect(radioStoreMock.current?.ptt).toBe(false);
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

  it('feeds connection_status events into the radio status store (MOR-620)', async () => {
    vi.mocked(setRadioStatus).mockClear();
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    instances[0].simulateMessage(JSON.stringify({
      type: 'event',
      name: 'connection_status',
      data: { state: 'reconnecting', attempt: 2, next_retry_seconds: 5 },
    }));
    expect(setRadioStatus).toHaveBeenCalledWith('reconnecting');

    instances[0].simulateMessage(JSON.stringify({
      type: 'event',
      name: 'connection_status',
      data: { state: 'connected' },
    }));
    expect(setRadioStatus).toHaveBeenLastCalledWith('connected');

    // Malformed payloads must be ignored, not crash the handler.
    vi.mocked(setRadioStatus).mockClear();
    instances[0].simulateMessage(JSON.stringify({ type: 'event', name: 'connection_status' }));
    instances[0].simulateMessage(JSON.stringify({ type: 'event', name: 'connection_status', data: { state: 7 } }));
    expect(setRadioStatus).not.toHaveBeenCalled();
  });

  it('rejects a same-session full snapshot that rolls revision truth backward', async () => {
    const { connect } = await import('../ws-client');
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 20, ptt: true, split: true })));
    vi.mocked(setRadioState).mockClear();

    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 1, ptt: false, split: false })));

    expect(setRadioState).not.toHaveBeenCalled();
    expect(radioStoreMock.current?.revision).toBe(20);
    expect(radioStoreMock.current?.ptt).toBe(true);
    expect(radioStoreMock.current?.split).toBe(true);
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

  it('never replays offline PTT-on aliases and coalesces release aliases', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();

    expect(ch.send({ type: 'cmd', name: 'ptt_on', id: 'on-1', params: {} })).toBe(false);
    expect(ch.send({ type: 'cmd', name: 'ptt', id: 'on-2', params: { state: true } })).toBe(false);
    expect(ch.send({ type: 'cmd', name: 'ptt_off', id: 'off-1', params: {} })).toBe(false);
    expect(ch.send({ type: 'cmd', name: 'ptt', id: 'off-2', params: { state: false } })).toBe(false);

    ch.connect('ws://test');
    instances[0].simulateOpen();

    expect(instances[0].sent.map((frame) => JSON.parse(frame))).toEqual([
      { type: 'cmd', name: 'ptt', id: 'off-2', params: { state: false } },
    ]);
  });

  it('sends healthy PTT-on and release aliases immediately on an open socket', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();
    ch.connect('ws://test');
    instances[0].simulateOpen();

    expect(ch.send({ type: 'cmd', name: 'ptt_on', id: 'on-1', params: {} })).toBe(true);
    expect(ch.send({ type: 'cmd', name: 'ptt', id: 'on-2', params: { state: true } })).toBe(true);
    expect(ch.send({ type: 'cmd', name: 'ptt_off', id: 'off-1', params: {} })).toBe(true);
    expect(ch.send({ type: 'cmd', name: 'ptt', id: 'off-2', params: { state: false } })).toBe(true);

    expect(instances[0].sent.map((frame) => JSON.parse(frame).id)).toEqual([
      'on-1',
      'on-2',
      'off-1',
      'off-2',
    ]);
  });

  it('pins an offline release beyond ordinary queue capacity', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();

    ch.send({ type: 'cmd', name: 'ptt_off', id: 'release', params: {} });
    for (let i = 0; i < 45; i++) {
      ch.send({ type: 'cmd', name: 'set_af_level', id: `ordinary-${i}`, params: { level: i } });
    }

    ch.connect('ws://test');
    instances[0].simulateOpen();

    const drained = instances[0].sent.map((frame) => JSON.parse(frame));
    expect(drained).toHaveLength(21);
    expect(drained[0]).toMatchObject({ name: 'ptt_off', id: 'release' });
    expect(drained.slice(1).map((cmd) => cmd.id)).toEqual(
      Array.from({ length: 20 }, (_, i) => `ordinary-${i + 25}`),
    );
  });

  it('allows one new queued release in a later offline episode', async () => {
    const { WsChannel } = await import('../ws-client');
    const ch = new WsChannel();

    ch.send({ type: 'cmd', name: 'ptt_off', id: 'release-1', params: {} });
    ch.connect('ws://test');
    instances[0].simulateOpen();
    expect(instances[0].sent.map((frame) => JSON.parse(frame).id)).toEqual(['release-1']);

    ch.disconnect();
    ch.send({ type: 'cmd', name: 'ptt_off', id: 'release-2', params: {} });
    ch.connect('ws://test');
    instances[1].simulateOpen();
    expect(instances[1].sent.map((frame) => JSON.parse(frame).id)).toEqual(['release-2']);
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
