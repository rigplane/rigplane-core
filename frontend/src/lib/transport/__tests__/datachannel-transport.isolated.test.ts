/**
 * `DataChannelTransport` unit tests (C5a) over the shared fake channel:
 * the `MessageTransport` mapping and the per-label attach step, driven by
 * the committed attach documents (`api-channel-golden.json`) — replayed
 * through the real handlers by the Python counterpart
 * `tests/test_api_channel_golden.py::test_golden_documents_replay_through_real_handlers`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { MessageTransport } from '../message-transport';
import { FakeDataChannel, goldenDocs, goldenAttachText } from './support/fake-datachannel';
import {
  DataChannelTransport,
  ATTACH_RESEND_MS,
  ATTACH_TIMEOUT_MS,
} from '../datachannel-transport';

// Compile-time check (never called): a native RTCDataChannel is exactly
// the channel surface the constructor takes.
const nativeChannelAccepted: (channel: RTCDataChannel) => DataChannelTransport = (channel) =>
  new DataChannelTransport(channel);
void nativeChannelAccepted;

const KEY = 'a'.repeat(64); // a session-key-shaped stand-in
const token = `rigplane-controller-v1.${KEY}`;

describe('DataChannelTransport', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  it('maps an attach-less channel: events, state, error, close', () => {
    const ch = new FakeDataChannel('scope');
    const t = new DataChannelTransport(ch);
    const errors: unknown[] = [], closes: unknown[] = [];
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
  it('scope: resends the golden attach until the golden ack, then exposes the token', () => {
    const ch = new FakeDataChannel('scope');
    const t = new DataChannelTransport(ch);
    t.open('ignored://', { protocols: [token] });
    ch.simulateOpen();
    // The attach frame's shape is the golden document's (JSON spacing is
    // not part of the contract); a resend repeats it byte for byte.
    const frame = goldenAttachText('attach_scope', KEY);
    expect(ch.sent.map((f) => JSON.parse(f as string))).toEqual([JSON.parse(frame)]);
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 2);
    expect(ch.textFrames()).toEqual([ch.sent[0], ch.sent[0], ch.sent[0]]);
    ch.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(t.state).toBe('open');
    expect(t.attachedProtocol).toBe(token);
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 4);
    expect(ch.textFrames()).toHaveLength(3); // resends stopped
  });
  it('audio: the attach times out into error + close without an ack', () => {
    const ch = new FakeDataChannel('audio');
    const t = new DataChannelTransport(ch);
    const errors: unknown[] = [];
    t.onError(() => errors.push(true));
    t.open('ignored://', { protocols: [token] });
    ch.simulateOpen();
    vi.advanceTimersByTime(ATTACH_TIMEOUT_MS - 1);
    expect(errors).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(errors).toHaveLength(1);
    expect(t.state).toBe('closed');
  });
  it('control opens on hello, audio on its ack; api sends the keyless golden attach', () => {
    const controlCh = new FakeDataChannel('control');
    const control = new DataChannelTransport(controlCh);
    control.open('ignored://', { protocols: [token] });
    controlCh.simulateOpen();
    expect(controlCh.sent.map((f) => JSON.parse(f as string))).toEqual([
      JSON.parse(goldenAttachText('attach_control', KEY)),
    ]);
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 4); // ordered/reliable: no resend
    expect(control.state).toBe('connecting');
    controlCh.simulateMessage(JSON.stringify({ type: 'hello', radio: 'IC-7610' }));
    expect(control.state).toBe('open');
    expect(control.attachedProtocol).toBe(token);
    const audioCh = new FakeDataChannel('audio');
    const audio = new DataChannelTransport(audioCh);
    audio.open('ignored://', { protocols: [token] });
    audioCh.simulateOpen();
    audioCh.simulateMessage(goldenDocs.attach_ack_audio.text ?? '');
    expect(audio.state).toBe('open');
    expect(audio.attachedProtocol).toBe(token);
    const apiCh = new FakeDataChannel('api');
    const api = new DataChannelTransport(apiCh);
    api.open('ignored://'); // keyless: the api acquire path offers no token
    apiCh.simulateOpen();
    expect(apiCh.sent.map((f) => JSON.parse(f))).toEqual([goldenDocs.attach_api.decoded]);
    expect(api.state).toBe('open'); // reliable delivery: nothing to wait for
    expect(api.attachedProtocol).toBe('');
    vi.advanceTimersByTime(ATTACH_RESEND_MS * 4);
    expect(apiCh.textFrames()).toHaveLength(1); // never resent
  });
  it('serves the WsChannel/AudioManager attach pattern through the MessageTransport interface', () => {
    const ch = new FakeDataChannel('scope');
    const ws: MessageTransport = new DataChannelTransport(ch);
    const protocols = [token]; // controllerClient.protocols() with a live grant
    let attached = false;
    ws.open('wss://rigplane.local/api/v1/ws', protocols.length > 0 ? { protocols } : undefined);
    ws.onOpen(() => {
      // exactly the ws-client.ts / audio-manager.ts guard
      if (ws.attachedProtocol !== protocols[0]) ws.close();
      else attached = true;
    });
    ch.simulateOpen();
    expect(attached).toBe(false); // the attach is still pending
    ch.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(attached).toBe(true);
  });
  it('refuses a keyless attach on a keyed label (api is the only keyless one)', async () => {
    const ch = new FakeDataChannel('scope');
    const t = new DataChannelTransport(ch);
    const errors: unknown[] = [], closes: unknown[] = [];
    t.onError(() => errors.push(true));
    t.onClose((info) => closes.push(info));
    t.open('ignored://', { protocols: [] }); // a sessionless offer on a keyed channel
    await vi.advanceTimersByTimeAsync(0);
    expect(ch.sent).toEqual([]); // the offer itself is invalid: nothing is sent
    expect(errors).toHaveLength(1);
    expect(closes).toEqual([{ code: 1006, reason: 'keyless attach refused', wasClean: false }]);
    expect(t.state).toBe('closed');
  });
  it('notifies a listener registered right after open() on an already-open api channel', async () => {
    const ch = new FakeDataChannel('api');
    ch.readyState = 'open';
    const t = new DataChannelTransport(ch);
    const opens: unknown[] = [];
    t.open('ignored://');
    t.onOpen(() => opens.push(true));
    expect(opens).toEqual([]); // deferred: open(); onOpen(...) still sees the event
    await vi.advanceTimersByTimeAsync(0);
    expect(opens).toHaveLength(1);
    expect(t.state).toBe('open');
    expect(ch.textFrames().map((f) => JSON.parse(f))).toEqual([goldenDocs.attach_api.decoded]);
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
    t.open('ignored://', { protocols: [token] });
    ch.simulateOpen();
    expect(() => t.send('early')).toThrow(/closed transport/);
    ch.simulateMessage('{"type":"hello"}');
    const bytes = new Uint8Array([9]).buffer as ArrayBuffer;
    ch.simulateMessage(bytes);
    expect(texts).toEqual(['{"type":"hello"}']);
    expect(binaries).toEqual([bytes]);
    ch.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(opens).toHaveLength(1);
    ch.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(texts).toEqual(['{"type":"hello"}', goldenDocs.attach_ack_scope.text ?? '']);
  });
});
