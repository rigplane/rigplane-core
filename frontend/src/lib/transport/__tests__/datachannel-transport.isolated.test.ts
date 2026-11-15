/**
 * `DataChannelTransport` unit tests (C5a) over the shared fake channel:
 * the `MessageTransport` mapping and the per-label attach step — the
 * lossy labels (scope/audio) resend the attach frame every 250 ms until
 * the ack and error+close after 5 s, control resolves on hello, api is
 * open immediately; pre-ack frames that do not resolve the attach are
 * delivered normally.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { FakeDataChannel } from './support/fake-datachannel';
import {
  DataChannelTransport,
  ATTACH_RESEND_MS,
  ATTACH_TIMEOUT_MS,
} from '../datachannel-transport';

const ack = (channel: string) => JSON.stringify({ type: 'attached', v: 1, channel });

describe('DataChannelTransport', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  it('maps an attach-less channel: events, sends, state, close', () => {
    const ch = new FakeDataChannel('scope');
    const t = new DataChannelTransport(ch);
    const opens: unknown[] = [];
    const texts: string[] = [];
    const binaries: ArrayBuffer[] = [];
    const closes: unknown[] = [];
    const errors: unknown[] = [];
    t.onOpen(() => opens.push(true));
    t.onText((text) => texts.push(text));
    t.onBinary((data) => binaries.push(data));
    t.onClose((info) => closes.push(info));
    t.onError(() => errors.push(true));
    expect(t.state).toBe('closed');
    t.open('ignored://label-is-the-connection');
    expect(t.state).toBe('connecting');
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
    ch.simulateError();
    expect(errors).toHaveLength(1);
    ch.close();
    expect(closes).toEqual([{ code: 1006, reason: '', wasClean: false }]);
    expect(t.state).toBe('closed');
  });
  it('resends the attach frame until the ack on the lossy labels', () => {
    const ch = new FakeDataChannel('scope');
    const t = new DataChannelTransport(ch);
    const closes: unknown[] = [];
    t.onClose((info) => closes.push(info));
    t.open('ignored://', { protocols: [], controllerKey: 'sess-key' });
    ch.simulateOpen();
    const attach = JSON.stringify({
      type: 'attach',
      controller_key: 'sess-key',
      state_delta: 2,
      role: 'primary',
    });
    expect(ch.sent).toEqual([attach]);
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 2);
    expect(ch.textFrames()).toEqual([attach, attach, attach]);
    ch.simulateMessage(ack('scope'));
    expect(t.state).toBe('open');
    expect(t.attachedProtocol).toBe('scope');
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 4);
    expect(ch.textFrames()).toHaveLength(3);
    t.close();
    expect(closes).toEqual([{ code: 1000, reason: '', wasClean: true }]);
  });
  it('control: sends the attach once and opens on hello (the ack it always had)', () => {
    const ch = new FakeDataChannel('control');
    const t = new DataChannelTransport(ch);
    const opens: unknown[] = [];
    const texts: string[] = [];
    t.onOpen(() => opens.push(true));
    t.onText((text) => texts.push(text));
    t.open('ignored://', { protocols: [], controllerKey: 'sess-key' });
    ch.simulateOpen();
    const attach = JSON.stringify({
      type: 'attach',
      controller_key: 'sess-key',
      state_delta: 2,
      role: 'primary',
    });
    expect(ch.sent).toEqual([attach]); // ordered/reliable: no resend
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 4);
    expect(ch.textFrames()).toEqual([attach]);
    expect(t.state).toBe('connecting');
    const hello = JSON.stringify({ type: 'hello', radio: 'IC-7610' });
    ch.simulateMessage(hello);
    expect(opens).toHaveLength(1);
    expect(t.state).toBe('open');
    expect(t.attachedProtocol).toBe('control');
    expect(texts).toEqual([hello]); // hello is delivered, not consumed
  });
  it('api: sends the attach once and is open immediately', () => {
    const ch = new FakeDataChannel('api');
    const t = new DataChannelTransport(ch);
    const opens: unknown[] = [];
    t.onOpen(() => opens.push(true));
    t.open('ignored://', { protocols: [], controllerKey: null });
    ch.simulateOpen();
    const attach = JSON.stringify({
      type: 'attach',
      controller_key: null,
      state_delta: 2,
      role: 'primary',
    });
    expect(ch.sent).toEqual([attach]);
    expect(opens).toHaveLength(1);
    expect(t.state).toBe('open'); // reliable delivery: nothing to wait for
    expect(t.attachedProtocol).toBe('api');
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 4);
    expect(ch.textFrames()).toEqual([attach]); // never resent
    t.send(new Uint8Array([1]).buffer as ArrayBuffer);
    expect(ch.sent).toHaveLength(2);
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
  it('delivers non-ack frames received before the ack; send waits for open', () => {
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
    expect(() => t.send('early')).toThrow(/closed transport/);
    ch.simulateMessage('{"type":"hello"}');
    const bytes = new Uint8Array([9]).buffer as ArrayBuffer;
    ch.simulateMessage(bytes);
    expect(texts).toEqual(['{"type":"hello"}']);
    expect(binaries).toEqual([bytes]);
    ch.simulateMessage(ack('scope'));
    expect(opens).toHaveLength(1);
    ch.simulateMessage(ack('scope'));
    expect(texts).toEqual(['{"type":"hello"}', ack('scope')]);
  });
});
