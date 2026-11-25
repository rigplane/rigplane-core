/**
 * `WebSocketTransport` unit tests (C4, no behaviour change).
 *
 * Pins that the transport maps the raw socket 1:1 the way `WsChannel` and
 * `AudioManager` use it today: URL + subprotocol offer, `binaryType`,
 * text/binary split, protocol echo, close info, error-closes-socket, and
 * connect-while-connecting silence. Uses the repo's shared fake
 * (`support/fake-ws-backend.ts`).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MockWebSocket, instances } from './support/fake-ws-backend';
import { WebSocketTransport } from '../websocket-transport';
import { createMessageTransport } from '../transport-factory';

describe('WebSocketTransport', () => {
  beforeEach(() => {
    instances.length = 0;
    vi.stubGlobal('WebSocket', MockWebSocket);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('opens the URL sessionless with binaryType arraybuffer and tracks state', () => {
    const t = new WebSocketTransport();
    expect(t.state).toBe('closed');
    t.open('wss://station.test/api/v1/ws?state_delta=2');
    expect(t.state).toBe('connecting');
    expect(instances).toHaveLength(1);
    expect(instances[0].url).toBe('wss://station.test/api/v1/ws?state_delta=2');
    expect(instances[0].binaryType).toBe('arraybuffer');
    expect(t.attachedProtocol).toBe('');
    instances[0].simulateOpen();
    expect(t.state).toBe('open');
  });

  it('offers the attach protocols and echoes the accepted one', () => {
    const t = new WebSocketTransport();
    const onOpen = vi.fn();
    t.onOpen(onOpen);
    t.open('wss://station.test/api/v1/ws', { protocols: ['rigplane-controller-v1.abc'], role: 'primary' });
    expect(instances[0].protocol).toBe('rigplane-controller-v1.abc');
    instances[0].simulateOpen();
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(t.attachedProtocol).toBe('rigplane-controller-v1.abc');
  });

  it('sends text and binary frames exactly like the raw socket', () => {
    const t = new WebSocketTransport();
    t.open('wss://station.test/api/v1/ws');
    instances[0].simulateOpen();
    t.send(JSON.stringify({ type: 'ping' }));
    const bytes = new Uint8Array([1, 2, 3]).buffer as ArrayBuffer;
    t.send(bytes);
    expect(instances[0].sent[0]).toBe(JSON.stringify({ type: 'ping' }));
    // The binary frame reaches the socket untouched (same reference).
    expect(instances[0].sent[1]).toBe(bytes);
  });

  it('splits incoming frames into text and binary events', () => {
    const t = new WebSocketTransport();
    const texts: string[] = [];
    const binaries: ArrayBuffer[] = [];
    t.onText((text) => texts.push(text));
    t.onBinary((data) => binaries.push(data));
    t.open('wss://station.test/api/v1/ws');
    instances[0].simulateOpen();
    t.send('x');
    instances[0].simulateMessage('{"type":"pong"}');
    const bytes = new Uint8Array([9]).buffer as ArrayBuffer;
    instances[0].simulateMessage(bytes);
    expect(texts).toEqual(['{"type":"pong"}']);
    expect(binaries).toEqual([bytes]);
  });

  it('reports close code/reason/wasClean and moves to closed', () => {
    const t = new WebSocketTransport();
    const closes: Array<{ code: number; reason: string; wasClean: boolean }> = [];
    t.onClose((info) => closes.push(info));
    t.open('wss://station.test/api/v1/ws');
    instances[0].simulateOpen();
    instances[0].simulateClose(1006, 'abnormal closure', false);
    expect(closes).toEqual([{ code: 1006, reason: 'abnormal closure', wasClean: false }]);
    expect(t.state).toBe('closed');
  });

  it('an error closes the socket so the close path (and reconnect above) runs', () => {
    const t = new WebSocketTransport();
    const closes: unknown[] = [];
    const errors: unknown[] = [];
    t.onClose((info) => closes.push(info));
    t.onError(() => errors.push(true));
    t.open('wss://station.test/api/v1/ws');
    instances[0].simulateError();
    // Like the raw `ws.onerror = () => ws.close()` wiring: the socket is
    // closed first, then error subscribers run.
    expect(closes).toHaveLength(1);
    expect(errors).toHaveLength(1);
    expect(t.state).toBe('closed');
  });

  it('close() on an open socket closes it and still reports the close', () => {
    const t = new WebSocketTransport();
    const closes: unknown[] = [];
    t.onClose((info) => closes.push(info));
    t.open('wss://station.test/api/v1/ws');
    instances[0].simulateOpen();
    t.close();
    expect(instances[0].readyState).toBe(MockWebSocket.CLOSED);
    expect(closes).toHaveLength(1);
    expect(t.state).toBe('closed');
  });

  it('close() while connecting cancels the handshake at once (MOR-4469)', () => {
    const t = new WebSocketTransport();
    const closes: unknown[] = [];
    const errors: unknown[] = [];
    t.onClose((info) => closes.push(info));
    t.onError(() => errors.push(true));
    t.open('wss://station.test/api/v1/ws');
    t.close();
    // An audio stop must not wait out the handshake: the connection attempt
    // is aborted immediately, the close still reports, and the swallowed
    // onerror keeps a deliberate cancel from surfacing as a transport error.
    expect(instances[0].readyState).toBe(MockWebSocket.CLOSED);
    expect(closes).toHaveLength(1);
    expect(t.state).toBe('closed');
    instances[0].simulateError();
    expect(errors).toHaveLength(0);
  });

  it('unsubscribed handlers stop firing', () => {
    const t = new WebSocketTransport();
    const texts: string[] = [];
    const off = t.onText((text) => texts.push(text));
    t.open('wss://station.test/api/v1/ws');
    instances[0].simulateOpen();
    off();
    instances[0].simulateMessage('{"type":"pong"}');
    expect(texts).toEqual([]);
  });
});

describe('transport factory', () => {
  beforeEach(() => {
    instances.length = 0;
    vi.stubGlobal('WebSocket', MockWebSocket);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns a WebSocket transport by default', () => {
    const t = createMessageTransport();
    expect(t).toBeInstanceOf(WebSocketTransport);
    t.open('wss://station.test/api/v1/ws');
    expect(instances).toHaveLength(1);
    expect(instances[0].url).toBe('wss://station.test/api/v1/ws');
    t.close();
  });
});
