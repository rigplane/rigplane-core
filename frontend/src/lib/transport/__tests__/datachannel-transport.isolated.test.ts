/**
 * `DataChannelTransport` unit tests (C5a) over a fake channel: the
 * `MessageTransport` mapping and the per-label attach step, driven by the
 * committed golden documents (`api-channel-golden.json`) — replayed through
 * the real handlers by the Python counterpart
 * `tests/test_api_channel_golden.py::test_golden_documents_replay_through_real_handlers`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { MessageTransport, TransportAttach } from '../message-transport';
import wire from '../api-channel-wire.json';
import golden from '../api-channel-golden.json';
import {
  DataChannelTransport, type DataChannelLike, DATA_CHANNELS, payloadMatchesContract, textCompanionLabel,
} from '../datachannel-transport';

// Fake `RTCDataChannel` (moved from ./support/fake-datachannel.ts, its only
// user): records sends, lets the test act as the peer, and exposes the
// committed golden documents' texts.

/** One committed golden document, as far as these tests read it. */
interface GoldenDoc {
  readonly text?: string;
  readonly decoded?: Record<string, unknown> | null;
  readonly direction?: string;
  readonly variant?: string;
}
const goldenDocs = golden.documents as Record<string, GoldenDoc>;

class FakeDataChannel implements DataChannelLike {
  readyState: RTCDataChannelState = 'connecting';
  binaryType: BinaryType = 'blob';
  readonly label: string;
  bufferedAmount = 0;
  sent: Array<string | ArrayBuffer> = [];
  // This-less callbacks: assignable to the DOM handler types (a function
  // without a this parameter fits any this), and callable on the fake.
  onopen: ((ev: Event) => unknown) | null = null;
  onmessage: ((ev: MessageEvent) => unknown) | null = null;
  onclose: ((ev: Event) => unknown) | null = null;
  onerror: ((ev: Event) => unknown) | null = null;
  constructor(label = 'control') {
    this.label = label;
  }
  send(data: string | ArrayBuffer | ArrayBufferView | Blob): void {
    this.sent.push(data as string | ArrayBuffer);
  }
  close(): void {
    if (this.readyState === 'closed') return;
    this.readyState = 'closed';
    this.onclose?.(new Event('close'));
  }
  simulateOpen(): void {
    this.readyState = 'open';
    this.onopen?.(new Event('open'));
  }
  simulateMessage(data: unknown): void {
    this.onmessage?.({ data } as unknown as MessageEvent);
  }
  simulateError(): void {
    this.onerror?.(new Event('error'));
  }
  textFrames(): string[] {
    return this.sent.filter((f): f is string => typeof f === 'string');
  }
}

/** The golden attach frame's text, `<SESSION-KEY>` substituted live. */
function goldenAttachText(name: string, key: string): string {
  const text = goldenDocs[name]?.text;
  if (text === undefined) throw new Error(`golden document ${name} has no text`);
  return text.replace('<SESSION-KEY>', key);
}

/** The committed control hello, verbatim: only a server->client text
 * document is the producer-built first frame the attach resolves on. */
function goldenHelloText(): string {
  const doc = goldenDocs.control_hello;
  expect(doc.direction).toBe('server->client');
  expect(doc.variant).toBe('text');
  const text = doc.text;
  if (text === undefined) throw new Error('golden document control_hello has no text');
  return text;
}

// Compile-time check (never called): a native RTCDataChannel is exactly the
// channel surface the constructor takes.
export const nativeChannelAccepted: (channel: RTCDataChannel) => DataChannelTransport = (channel) =>
  new DataChannelTransport(channel);

/** A split scope/audio pair: the lossy binary channel and its reliable
 * `*-text` companion (labels from the committed channel set). */
function splitPair(kind: 'scope' | 'audio'): { ch: FakeDataChannel; text: FakeDataChannel; t: DataChannelTransport } {
  const ch = new FakeDataChannel(kind);
  const text = new FakeDataChannel(textCompanionLabel(kind) ?? 'missing');
  return { ch, text, t: new DataChannelTransport(ch, text) };
}
function openPair(pair: { ch: FakeDataChannel; text: FakeDataChannel }): void {
  pair.ch.simulateOpen();
  pair.text.simulateOpen();
}

const KEY = 'a'.repeat(64); // a session-key-shaped stand-in
const token = `rigplane-controller-v1.${KEY}`;
describe('DataChannelTransport', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  it('maps a keyed control channel: events, state, error, close', () => {
    const ch = new FakeDataChannel('control');
    const t = new DataChannelTransport(ch);
    const errors: unknown[] = [], closes: unknown[] = [];
    t.onError(() => errors.push(true));
    t.onClose((info) => closes.push(info));
    expect(t.state).toBe('closed');
    t.open('ignored://label-is-the-connection', { protocols: [token], role: 'primary' });
    expect(t.state).toBe('connecting');
    ch.simulateOpen();
    ch.simulateMessage(goldenHelloText());
    expect(t.state).toBe('open');
    expect(ch.binaryType).toBe('arraybuffer');
    ch.simulateError();
    expect(errors).toHaveLength(1);
    ch.close();
    expect(closes).toEqual([{ code: 1006, reason: '', wasClean: false }]);
    expect(t.state).toBe('closed');
  });
  it('reads the K5 channel set from the committed wire contract', () => {
    expect(DATA_CHANNELS.map((c) => [c.label, c.kind, c.ordered, c.max_retransmits, c.carries])).toEqual([
      ['control', 'control', true, null, 'all'],
      ['api', 'api', true, null, 'all'],
      ['scope', 'scope', false, 0, 'binary'],
      ['scope-text', 'scope', true, null, 'text'],
      ['audio', 'audio', false, 0, 'binary'],
      ['audio-text', 'audio', true, null, 'text'],
    ]);
    expect(textCompanionLabel('scope')).toBe('scope-text');
    expect(textCompanionLabel('audio')).toBe('audio-text');
    expect(textCompanionLabel('control')).toBeNull();
  });
  it('scope: sends the golden attach ONCE on scope-text, never resent; opens on the golden ack', () => {
    const pair = splitPair('scope');
    const { ch, text, t } = pair;
    t.open('ignored://', { protocols: [token], role: 'primary' });
    openPair(pair);
    // The shape is the golden document's (JSON spacing is not contractual).
    const frame = goldenAttachText('attach_scope', KEY);
    expect(text.sent.map((f) => JSON.parse(f as string))).toEqual([JSON.parse(frame)]);
    expect(ch.sent).toEqual([]); // nothing on the lossy channel
    vi.advanceTimersByTime(60_000);
    expect(text.textFrames()).toHaveLength(1); // reliable: never resent
    text.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(t.state).toBe('open');
    expect(t.attachedProtocol).toBe(token);
  });
  it('a split pair routes text to the reliable half and binary to the lossy half; both deliver here', () => {
    const pair = splitPair('audio');
    const { ch, text, t } = pair;
    const texts: string[] = [], binaries: ArrayBuffer[] = [];
    t.onText((value) => texts.push(value));
    t.onBinary((value) => binaries.push(value));
    t.open('ignored://', { protocols: [token], role: 'primary' });
    openPair(pair);
    text.simulateMessage(goldenDocs.attach_ack_audio.text ?? '');
    const bytes = new Uint8Array([1, 2]).buffer as ArrayBuffer;
    t.send('{"type":"audio_start","direction":"tx"}');
    t.send(bytes);
    expect(text.textFrames().slice(1)).toEqual(['{"type":"audio_start","direction":"tx"}']);
    expect(ch.sent).toEqual([bytes]);
    expect(ch.binaryType).toBe('arraybuffer');
    expect(text.binaryType).toBe('arraybuffer');
    const rx = new Uint8Array([7]).buffer as ArrayBuffer;
    ch.simulateMessage(rx);
    text.simulateMessage('{"type":"audio_format"}');
    expect(binaries).toEqual([rx]);
    expect(texts).toEqual(['{"type":"audio_format"}']);
  });
  it('a split pair opens only when the ack arrived AND the lossy half is open', () => {
    const { ch, text, t } = splitPair('scope');
    const opens: unknown[] = [];
    t.onOpen(() => opens.push(true));
    t.open('ignored://', { protocols: [token], role: 'primary' });
    text.simulateOpen();
    text.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(t.state).toBe('connecting'); // the binary half is still connecting
    expect(opens).toEqual([]);
    ch.simulateOpen();
    expect(t.state).toBe('open');
    expect(opens).toHaveLength(1);
  });
  it('either half closing closes the whole transport and both channels', () => {
    const pair = splitPair('audio');
    const closes: unknown[] = [];
    pair.t.onClose((info) => closes.push(info));
    pair.t.open('ignored://', { protocols: [token], role: 'primary' });
    openPair(pair);
    pair.text.simulateMessage(goldenDocs.attach_ack_audio.text ?? '');
    pair.ch.close();
    expect(closes).toEqual([{ code: 1006, reason: '', wasClean: false }]);
    expect(pair.text.readyState).toBe('closed');
    expect(pair.t.state).toBe('closed');
  });
  it('refuses a scope/audio channel without its committed text companion, and a companion on control', async () => {
    for (const t of [
      new DataChannelTransport(new FakeDataChannel('scope')),
      new DataChannelTransport(new FakeDataChannel('audio'), new FakeDataChannel('scope-text')),
      new DataChannelTransport(new FakeDataChannel('control'), new FakeDataChannel('scope-text')),
    ]) {
      const closes: unknown[] = [];
      t.onClose((info) => closes.push(info));
      t.open('ignored://', { protocols: [token], role: 'primary' });
      await vi.advanceTimersByTimeAsync(0);
      expect(closes).toEqual([{ code: 1006, reason: 'text channel does not match the channel set', wasClean: false }]);
    }
  });
  it('control opens on hello, audio on its ack; api sends the keyless golden attach', () => {
    const controlCh = new FakeDataChannel('control');
    const control = new DataChannelTransport(controlCh);
    control.open('ignored://', { protocols: [token], role: 'primary' });
    controlCh.simulateOpen();
    expect(controlCh.sent.map((f) => JSON.parse(f as string))).toEqual([
      JSON.parse(goldenAttachText('attach_control', KEY)),
    ]);
    vi.advanceTimersByTime(60_000); // ordered/reliable: no resend
    expect(controlCh.textFrames()).toHaveLength(1);
    expect(control.state).toBe('connecting');
    controlCh.simulateMessage(goldenHelloText());
    expect(control.state).toBe('open');
    expect(control.attachedProtocol).toBe(token);
    const audioPair = splitPair('audio');
    audioPair.t.open('ignored://', { protocols: [token], role: 'primary' });
    openPair(audioPair);
    audioPair.text.simulateMessage(goldenDocs.attach_ack_audio.text ?? '');
    expect(audioPair.t.state).toBe('open');
    expect(audioPair.t.attachedProtocol).toBe(token);
    const apiCh = new FakeDataChannel('api');
    const api = new DataChannelTransport(apiCh);
    api.open('ignored://', { protocols: [], role: 'primary' }); // keyless acquire: no token, the caller's role
    apiCh.simulateOpen();
    expect(apiCh.sent.map((f) => JSON.parse(f as string))).toEqual([goldenDocs.attach_api.decoded]);
    expect(api.state).toBe('open'); // reliable delivery: nothing to wait for
    expect(api.attachedProtocol).toBe('');
    vi.advanceTimersByTime(60_000);
    expect(apiCh.textFrames()).toHaveLength(1); // never resent
  });
  it("api: the keyless acquire carries the caller's role, not a default", () => {
    const ch = new FakeDataChannel('api');
    const t = new DataChannelTransport(ch);
    t.open('ignored://', { protocols: [], role: 'auxiliary' });
    ch.simulateOpen();
    const sent = JSON.parse(ch.textFrames()[0]) as Record<string, unknown>;
    expect(sent['role']).toBe('auxiliary');
    expect(sent['controller_key']).toBeNull();
    expect(payloadMatchesContract(wire.payloads.attach_frame, sent)).toBe(true);
    expect(t.state).toBe('open'); // reliable: nothing to wait for
  });
  it('api: a missing attach is refused instead of a defaulted primary', async () => {
    const ch = new FakeDataChannel('api');
    const t = new DataChannelTransport(ch);
    const errors: unknown[] = [], closes: unknown[] = [];
    t.onError(() => errors.push(true));
    t.onClose((info) => closes.push(info));
    t.open('ignored://');
    await vi.advanceTimersByTimeAsync(0);
    expect(ch.sent).toEqual([]); // nothing on the wire
    expect(errors).toHaveLength(1);
    expect(closes).toEqual([{ code: 1006, reason: 'roleless attach refused', wasClean: false }]);
    expect(t.state).toBe('closed');
  });
  it('serves the WsChannel/AudioManager attach pattern through the MessageTransport interface', () => {
    const pair = splitPair('scope');
    const ws: MessageTransport = pair.t;
    const protocols = [token]; // controllerClient.protocols() with a live grant
    let attached = false;
    ws.open('wss://rigplane.local/api/v1/ws', protocols.length > 0 ? { protocols, role: 'primary' } : undefined);
    ws.onOpen(() => {
      if (ws.attachedProtocol !== protocols[0]) ws.close(); // the ws-client.ts guard
      else attached = true;
    });
    openPair(pair);
    expect(attached).toBe(false); // the attach is still pending
    pair.text.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(attached).toBe(true);
  });
  it('refuses a keyless attach on a keyed label (api is the only keyless one)', async () => {
    const { ch, text, t } = splitPair('scope');
    const errors: unknown[] = [], closes: unknown[] = [];
    t.onError(() => errors.push(true));
    t.onClose((info) => closes.push(info));
    t.open('ignored://', { protocols: [], role: 'primary' }); // a keyless offer on a keyed channel
    await vi.advanceTimersByTimeAsync(0);
    expect([...ch.sent, ...text.sent]).toEqual([]); // the offer itself is invalid: nothing is sent
    expect(errors).toHaveLength(1);
    expect(closes).toEqual([{ code: 1006, reason: 'keyless attach refused', wasClean: false }]);
    expect(t.state).toBe('closed');
  });
  it('refuses an attach-less open on a keyed label: no attach step, error + close', async () => {
    const { ch, t } = splitPair('scope');
    const errors: unknown[] = [], closes: unknown[] = [];
    t.onError(() => errors.push(true));
    t.onClose((info) => closes.push(info));
    t.open('ignored://');
    await vi.advanceTimersByTimeAsync(0);
    expect(ch.sent).toEqual([]); // nothing on the wire
    expect(errors).toHaveLength(1);
    expect(closes).toEqual([{ code: 1006, reason: 'keyless attach refused', wasClean: false }]);
    expect(t.state).toBe('closed');
  });
  it('notifies a listener registered right after open() on an already-open api channel', async () => {
    const ch = new FakeDataChannel('api');
    ch.readyState = 'open';
    const t = new DataChannelTransport(ch);
    const opens: unknown[] = [];
    t.open('ignored://', { protocols: [], role: 'primary' });
    t.onOpen(() => opens.push(true));
    expect(opens).toEqual([]); // deferred: open(); onOpen(...) still sees it
    await vi.advanceTimersByTimeAsync(0);
    expect(opens).toHaveLength(1);
    expect(t.state).toBe('open');
    expect(ch.textFrames().map((f) => JSON.parse(f as string))).toEqual([goldenDocs.attach_api.decoded]);
  });
  it('already-open keyed channel: connecting until the ack, the attach first, onOpen once', async () => {
    const { ch, text, t } = splitPair('scope');
    ch.readyState = 'open';
    text.readyState = 'open';
    const opens: unknown[] = [];
    t.open('ignored://', { protocols: [token], role: 'primary' });
    t.onOpen(() => opens.push(true)); // registered right after open(): still notified
    expect(t.state).toBe('connecting'); // not the native channel's readyState
    expect(() => t.send('early')).toThrow(/not open/);
    await vi.advanceTimersByTimeAsync(0); // the same start the open event runs
    expect(text.textFrames().map((f) => JSON.parse(f))).toEqual([JSON.parse(goldenAttachText('attach_scope', KEY))]);
    expect(t.state).toBe('connecting');
    expect(() => t.send('early')).toThrow(/not open/);
    expect(opens).toEqual([]);
    text.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(t.state).toBe('open');
    expect(opens).toHaveLength(1);
    text.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(opens).toHaveLength(1); // exactly once
  });
  it('not-yet-open keyed channel: connecting until the ack, the attach first, send refused before it', () => {
    const pair = splitPair('scope');
    const { text, t } = pair;
    const opens: unknown[] = [];
    t.open('ignored://', { protocols: [token], role: 'primary' });
    t.onOpen(() => opens.push(true));
    expect(t.state).toBe('connecting');
    openPair(pair);
    expect(text.textFrames().map((f) => JSON.parse(f))).toEqual([JSON.parse(goldenAttachText('attach_scope', KEY))]);
    expect(t.state).toBe('connecting');
    expect(() => t.send('early')).toThrow(/not open/);
    expect(opens).toEqual([]);
    text.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(t.state).toBe('open');
    expect(opens).toHaveLength(1);
  });
  it('carries the caller role: primary matches the golden control attach, auxiliary joins an existing primary', () => {
    const primaryCh = new FakeDataChannel('control');
    const primary = new DataChannelTransport(primaryCh);
    primary.open('ignored://', { protocols: [token], role: 'primary' });
    primaryCh.simulateOpen();
    expect(primaryCh.textFrames().map((f) => JSON.parse(f))).toEqual([
      JSON.parse(goldenAttachText('attach_control', KEY)),
    ]);
    const auxCh = new FakeDataChannel('control');
    const auxiliary = new DataChannelTransport(auxCh);
    auxiliary.open('ignored://', { protocols: [token], role: 'auxiliary' });
    auxCh.simulateOpen();
    const sent = JSON.parse(auxCh.textFrames()[0]) as Record<string, unknown>;
    expect(sent['role']).toBe('auxiliary');
    expect(payloadMatchesContract(wire.payloads.attach_frame, sent)).toBe(true);
  });
  it('refuses a keyed attach that names no role instead of defaulting one', async () => {
    const { ch, t } = splitPair('scope');
    const errors: unknown[] = [], closes: unknown[] = [];
    t.onError(() => errors.push(true));
    t.onClose((info) => closes.push(info));
    // The required `role` makes a roleless attach unrepresentable in TS;
    // the runtime guard still fails closed for untyped callers, so the
    // test builds the unsound attach on purpose.
    t.open('ignored://', { protocols: [token] } as unknown as TransportAttach);
    await vi.advanceTimersByTimeAsync(0);
    expect(ch.sent).toEqual([]); // nothing on the wire
    expect(errors).toHaveLength(1);
    expect(closes).toEqual([{ code: 1006, reason: 'roleless attach refused', wasClean: false }]);
    expect(t.state).toBe('closed');
  });
  it('rejects an attach ack whose version is not the committed attach_ack version', () => {
    const pair = splitPair('scope');
    const { text, t } = pair;
    const opens: unknown[] = [];
    t.onOpen(() => opens.push(true));
    t.open('ignored://', { protocols: [token], role: 'primary' });
    openPair(pair);
    text.simulateMessage('{"type":"attached","v":2,"channel":"scope"}');
    expect(t.state).toBe('connecting');
    expect(opens).toEqual([]);
    text.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(t.state).toBe('open');
    expect(opens).toHaveLength(1);
  });
  it('control: a hello that violates the control_hello contract never completes the attach', () => {
    const hello = JSON.parse(goldenHelloText()) as Record<string, unknown>;
    const missingRadio: Record<string, unknown> = { ...hello };
    delete missingRadio.radio;
    const malformed = [
      JSON.stringify({ type: 'hello' }), // type alone is not the contract
      JSON.stringify(missingRadio), // one required field removed
      JSON.stringify({ ...hello, connected: 'true' }), // a required boolean as a string
    ];
    for (const frame of malformed) {
      const ch = new FakeDataChannel('control');
      const t = new DataChannelTransport(ch);
      const opens: unknown[] = [], texts: string[] = [];
      t.onOpen(() => opens.push(true));
      t.onText((text) => texts.push(text));
      t.open('ignored://', { protocols: [token], role: 'primary' });
      ch.simulateOpen();
      ch.simulateMessage(frame);
      // delivered like any other frame, but it proves no attach
      expect(texts).toEqual([frame]);
      expect(t.state).toBe('connecting');
      vi.advanceTimersByTime(60_000);
      expect(opens).toEqual([]); // the attach never completes
      expect(t.attachedProtocol).toBe('');
      expect(t.state).toBe('connecting'); // control is closed by the peer
      ch.close();
      expect(opens).toEqual([]);
      expect(t.state).toBe('closed');
    }
  });
  it('delivers non-ack frames received before the ack; send waits for open', () => {
    const pair = splitPair('scope');
    const { ch, text, t } = pair;
    const texts: string[] = [];
    const binaries: ArrayBuffer[] = [];
    const opens: unknown[] = [];
    t.onText((text) => texts.push(text));
    t.onBinary((data) => binaries.push(data));
    t.onOpen(() => opens.push(true));
    t.open('ignored://', { protocols: [token], role: 'primary' });
    openPair(pair);
    expect(() => t.send('early')).toThrow(/not open/);
    text.simulateMessage('{"type":"hello"}');
    const bytes = new Uint8Array([9]).buffer as ArrayBuffer;
    ch.simulateMessage(bytes);
    expect(texts).toEqual(['{"type":"hello"}']);
    expect(binaries).toEqual([bytes]);
    text.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(opens).toHaveLength(1);
    text.simulateMessage(goldenDocs.attach_ack_scope.text ?? '');
    expect(texts).toEqual(['{"type":"hello"}', goldenDocs.attach_ack_scope.text ?? '']);
  });
});
