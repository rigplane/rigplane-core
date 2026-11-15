/**
 * `DataChannelTransport` unit tests (C5a).
 *
 * A fake `RTCDataChannel` (text frames as strings, binary as
 * `ArrayBuffer`) pins the `MessageTransport` mapping and the attach
 * step: the attach frame is sent on channel open, resent every 250 ms
 * until the server's `attached` frame arrives, and given up as an
 * error/close after 5 s. Frames that arrive before `attached` and are
 * not the ack are delivered normally.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  DataChannelTransport,
  ATTACH_RESEND_MS,
  ATTACH_TIMEOUT_MS,
} from '../datachannel-transport';

class FakeDataChannel {
  readyState: 'connecting' | 'open' | 'closing' | 'closed' = 'connecting';
  binaryType = 'blob';
  readonly label: string;
  bufferedAmount = 0;
  sent: Array<string | ArrayBuffer> = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(label = 'control') {
    this.label = label;
  }

  send(data: string | ArrayBuffer): void {
    this.sent.push(data);
  }

  close(): void {
    if (this.readyState === 'closed') return;
    this.readyState = 'closed';
    this.onclose?.();
  }

  simulateOpen(): void {
    this.readyState = 'open';
    this.onopen?.();
  }

  simulateMessage(data: unknown): void {
    this.onmessage?.({ data });
  }

  simulateClose(): void {
    this.readyState = 'closed';
    this.onclose?.();
  }

  simulateError(): void {
    this.onerror?.();
  }

  textFrames(): string[] {
    return this.sent.filter((f): f is string => typeof f === 'string');
  }
}

const ack = (channel: string) =>
  JSON.stringify({ type: 'attached', v: 1, channel });

describe('DataChannelTransport', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('maps an attach-less channel: open/text/binary/close events and sends', () => {
    const ch = new FakeDataChannel('scope');
    const t = new DataChannelTransport(ch);
    const opens: unknown[] = [];
    const texts: string[] = [];
    const binaries: ArrayBuffer[] = [];
    const closes: unknown[] = [];
    t.onOpen(() => opens.push(true));
    t.onText((text) => texts.push(text));
    t.onBinary((data) => binaries.push(data));
    t.onClose((info) => closes.push(info));
    expect(t.state).toBe('connecting');
    t.open('ignored://label-is-the-connection');
    ch.simulateOpen();
    expect(t.state).toBe('open');
    expect(opens).toHaveLength(1);
    expect(ch.binaryType).toBe('arraybuffer');
    t.send('{"type":"ping"}');
    const bytes = new Uint8Array([1, 2]).buffer as ArrayBuffer;
    t.send(bytes);
    expect(ch.sent).toEqual(['{"type":"ping"}', bytes]);
    ch.simulateMessage('{"type":"pong"}');
    ch.simulateMessage(bytes);
    expect(texts).toEqual(['{"type":"pong"}']);
    expect(binaries).toEqual([bytes]);
    ch.simulateClose();
    expect(closes).toEqual([{ code: 1006, reason: '', wasClean: false }]);
    expect(t.state).toBe('closed');
  });

  it('close() closes the channel and reports a clean local close', () => {
    const ch = new FakeDataChannel();
    const t = new DataChannelTransport(ch);
    const closes: unknown[] = [];
    t.onClose((info) => closes.push(info));
    t.open('ignored://');
    ch.simulateOpen();
    t.close();
    expect(ch.readyState).toBe('closed');
    expect(closes).toEqual([{ code: 1000, reason: '', wasClean: true }]);
    expect(t.state).toBe('closed');
  });

  it('sends the attach frame on open, resends it every 250 ms until the ack', () => {
    const ch = new FakeDataChannel('control');
    const t = new DataChannelTransport(ch);
    const opens: unknown[] = [];
    t.onOpen(() => opens.push(true));
    t.open('ignored://', { protocols: [], controllerKey: 'sess-key' });
    ch.simulateOpen();
    const attach = JSON.stringify({
      type: 'attach',
      controller_key: 'sess-key',
      state_delta: 2,
      role: 'primary',
    });
    expect(ch.sent).toEqual([attach]);
    // Two resends: the first attach frame may be lost on a lossy channel.
    vi.advanceTimersByTime(ATTACH_RESEND_MS);
    vi.advanceTimersByTime(ATTACH_RESEND_MS);
    expect(ch.textFrames()).toEqual([attach, attach, attach]);
    // The ack stops the resend loop and opens the transport.
    ch.simulateMessage(ack('control'));
    expect(opens).toHaveLength(1);
    expect(t.state).toBe('open');
    expect(t.attachedProtocol).toBe('control');
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 4);
    expect(ch.textFrames()).toHaveLength(3);
  });

  it('attaches keyless (null controller_key) when asked', () => {
    const ch = new FakeDataChannel('api');
    const t = new DataChannelTransport(ch);
    t.open('ignored://', { protocols: [], controllerKey: null });
    ch.simulateOpen();
    expect(JSON.parse(ch.textFrames()[0])).toEqual({
      type: 'attach',
      controller_key: null,
      state_delta: 2,
      role: 'primary',
    });
    ch.simulateMessage(ack('api'));
    expect(t.state).toBe('open');
  });

  it('gives the attach step up as error+close after 5 s without an ack', () => {
    const ch = new FakeDataChannel('audio');
    const t = new DataChannelTransport(ch);
    const errors: unknown[] = [];
    const closes: unknown[] = [];
    t.onError(() => errors.push(true));
    t.onClose((info) => closes.push(info));
    t.open('ignored://', { protocols: [], controllerKey: 'k' });
    ch.simulateOpen();
    vi.advanceTimersByTime(ATTACH_TIMEOUT_MS - 1);
    expect(errors).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(errors).toHaveLength(1);
    expect(closes).toEqual([{ code: 1006, reason: 'attach timeout', wasClean: false }]);
    expect(ch.readyState).toBe('closed');
    expect(t.state).toBe('closed');
  });

  it('delivers non-ack frames received before the ack', () => {
    const ch = new FakeDataChannel('scope');
    const t = new DataChannelTransport(ch);
    const texts: string[] = [];
    const binaries: ArrayBuffer[] = [];
    const opens: unknown[] = [];
    t.onText((text) => texts.push(text));
    t.onBinary((data) => binaries.push(data));
    t.onOpen(() => opens.push(true));
    t.open('ignored://', { protocols: [], controllerKey: 'k' });
    ch.simulateOpen();
    // Unordered channels may overtake the ack with real data: deliver it.
    ch.simulateMessage('{"type":"hello"}');
    const bytes = new Uint8Array([9]).buffer as ArrayBuffer;
    ch.simulateMessage(bytes);
    expect(texts).toEqual(['{"type":"hello"}']);
    expect(binaries).toEqual([bytes]);
    expect(opens).toHaveLength(0);
    ch.simulateMessage(ack('scope'));
    expect(opens).toHaveLength(1);
    // An ack-shaped frame after the ack is just an ordinary text frame.
    ch.simulateMessage(ack('scope'));
    expect(texts).toEqual(['{"type":"hello"}', ack('scope')]);
  });

  it('a channel error surfaces through onError; a send before open throws', () => {
    const ch = new FakeDataChannel();
    const t = new DataChannelTransport(ch);
    const errors: unknown[] = [];
    t.onError(() => errors.push(true));
    t.open('ignored://', { protocols: [], controllerKey: 'k' });
    ch.simulateOpen();
    expect(() => t.send('early')).toThrow(/closed transport/);
    ch.simulateMessage(ack('control'));
    t.send('late');
    expect(ch.sent[ch.sent.length - 1]).toBe('late');
    ch.simulateError();
    expect(errors).toHaveLength(1);
  });

  it('unsubscribed handlers stop firing', () => {
    const ch = new FakeDataChannel();
    const t = new DataChannelTransport(ch);
    const texts: string[] = [];
    const off = t.onText((text) => texts.push(text));
    t.open('ignored://');
    ch.simulateOpen();
    off();
    ch.simulateMessage('x');
    expect(texts).toEqual([]);
  });
});
