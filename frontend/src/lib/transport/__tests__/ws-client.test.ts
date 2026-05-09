import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { WsCommand, WsMessage } from '../../types/protocol';

// ─── Mock store before importing ws-client ──────────────────────────────────
vi.mock('../../stores/connection.svelte', () => ({
  setWsConnected: vi.fn(),
  setHttpConnected: vi.fn(),
  markStateUpdated: vi.fn(),
  setReconnecting: vi.fn(),
  isLiveRadioAvailable: vi.fn(() => true),
}));

vi.mock('../../stores/radio.svelte', () => ({
  getRadioState: vi.fn().mockReturnValue(null),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  resetRadioState: vi.fn(),
  setRadioState: vi.fn(),
}));

import { isLiveRadioAvailable, setWsConnected } from '../../stores/connection.svelte';
import { patchActiveReceiver } from '../../stores/radio.svelte';

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
