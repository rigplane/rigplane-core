/**
 * `DataChannelTransport` unit tests (C5a) over the shared fake channel:
 * the `MessageTransport` mapping and the per-label attach step — the
 * lossy labels resend the attach until the ack, control resolves on
 * hello, api opens at once.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { FakeDataChannel } from './support/fake-datachannel';
import {
  DataChannelTransport,
  ATTACH_RESEND_MS,
  ATTACH_TIMEOUT_MS,
} from '../datachannel-transport';

const ack = (channel: string) => JSON.stringify({ type: 'attached', v: 1, channel });
const attach = (key: string | null) =>
  JSON.stringify({ type: 'attach', controller_key: key, state_delta: 2, role: 'primary' });

describe('DataChannelTransport', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  it('maps an attach-less channel: events, state, error, close', () => {
    const ch = new FakeDataChannel('scope');
    const t = new DataChannelTransport(ch);
    const opens: unknown[] = [], errors: unknown[] = [], closes: unknown[] = [];
    t.onOpen(() => opens.push(true));
    t.onError(() => errors.push(true));
    t.onClose((info) => closes.push(info));
    expect(t.state).toBe('closed');
    t.open('ignored://label-is-the-connection');
    expect(t.state).toBe('connecting');
    ch.simulateOpen();
    expect(t.state).toBe('open');
    expect(ch.binaryType).toBe('arraybuffer');
    ch.simulateError();
    expect(errors).toHaveLength(1);
    ch.close();
    expect(closes).toEqual([{ code: 1006, reason: '', wasClean: false }]);
    expect(t.state).toBe('closed');
  });
  it('lossy labels: resends the attach until the ack, else errors at 5 s', () => {
    const ch = new FakeDataChannel('scope');
    const t = new DataChannelTransport(ch);
    t.open('ignored://', { protocols: [], controllerKey: 'sess-key' });
    ch.simulateOpen();
    const frame = attach('sess-key');
    expect(ch.sent).toEqual([frame]);
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 2);
    expect(ch.textFrames()).toEqual([frame, frame, frame]);
    ch.simulateMessage(ack('scope'));
    expect(t.state).toBe('open');
    expect(t.attachedProtocol).toBe('scope');
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 4);
    expect(ch.textFrames()).toHaveLength(3); // resends stopped
    const slow = new FakeDataChannel('audio');
    const starved = new DataChannelTransport(slow);
    const errors: unknown[] = [];
    starved.onError(() => errors.push(true));
    starved.open('ignored://', { protocols: [], controllerKey: 'k' });
    slow.simulateOpen();
    vi.advanceTimersByTime(ATTACH_TIMEOUT_MS - 1);
    expect(errors).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(errors).toHaveLength(1);
    expect(starved.state).toBe('closed');
  });
  it('control and api send the attach once; control opens on hello, api at once', () => {
    const controlCh = new FakeDataChannel('control');
    const control = new DataChannelTransport(controlCh);
    const texts: string[] = [];
    control.onText((text) => texts.push(text));
    control.open('ignored://', { protocols: [], controllerKey: 'sess-key' });
    controlCh.simulateOpen();
    expect(controlCh.sent).toEqual([attach('sess-key')]);
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 4); // ordered/reliable: no resend
    expect(control.state).toBe('connecting');
    const hello = JSON.stringify({ type: 'hello', radio: 'IC-7610' });
    controlCh.simulateMessage(hello);
    expect(control.state).toBe('open');
    expect(texts).toEqual([hello]); // hello is delivered, not consumed
    const apiCh = new FakeDataChannel('api');
    const api = new DataChannelTransport(apiCh);
    api.open('ignored://', { protocols: [], controllerKey: null });
    apiCh.simulateOpen();
    expect(apiCh.sent).toEqual([attach(null)]);
    expect(api.state).toBe('open'); // reliable delivery: nothing to wait for
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 4);
    expect(apiCh.textFrames()).toEqual([attach(null)]); // never resent
    api.send(new Uint8Array([1]).buffer as ArrayBuffer);
    expect(apiCh.sent).toHaveLength(2);
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
