/**
 * Compatibility tests for the data-channel attach acknowledgement
 * (MOR-4478): the server under review will send one extra text frame
 * `{"type": "attached", "v": 1, "channel": "scope"}` as the first frame on the
 * scope channel. Today's shipped frontend must ignore it: no state change,
 * no error, no reconnect, and the following `granted` + binary frame are
 * handled exactly as without it.
 *
 * Drives the real controller message path (the controller that owns the
 * scope socket) exactly the way scope-controller-v2.test.ts does.
 *
 * Producer contracts: the ack frame comes verbatim from the committed
 * api-channel golden documents (built by the server producer and replayed
 * through the real handlers by tests/test_api_channel_golden.py), the
 * granted/subscribe literals satisfy the `ScopeGranted` /
 * `ScopeSubscription` types that `parseScopeGranted` and `ScopeStream`
 * accept, and the channel double implements the `Pick<WsChannel, ...>`
 * surface the controller consumes.
 */
import { get } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScopeController } from '../scope-controller.svelte';
import { scopeWire } from './scope-wire-fixture';
import type { ConnectionState, ControlSessionTransition, WsChannel } from '$lib/transport/ws-client';
import type { WsIncoming } from '../../types/protocol';
import type { ScopeGranted, ScopeSubscription } from '../scope-view';
import apiChannelGolden from '../../transport/api-channel-golden.json';
import apiChannelWire from '../../transport/api-channel-wire.json';

/** The producer-built golden document for this channel's attach ack. */
const attachAckScope = apiChannelGolden.documents.attach_ack_scope;
/** The committed wire contract's own `type` row for the attach ack. */
const wireAttachAckType = JSON.parse(String(
  apiChannelWire.payloads.attach_ack.fields.find(([name]) => name === 'type')?.[1] ?? 'null',
));

/** The members of the producer WsChannel that ScopeController consumes. */
type ScopeChannelSurface = Pick<WsChannel,
  'connect' | 'disconnect' | 'sendRaw' | 'onStateChange' | 'onSessionTransition'
  | 'onBinary' | 'onMessage' | 'state' | 'sessionEpoch'>;

class ScopeChannelFake implements ScopeChannelSurface {
  private readonly binary = new Set<(buffer: ArrayBuffer) => void>();
  private readonly states = new Set<(state: ConnectionState) => void>();
  private readonly sessions = new Set<(session: ControlSessionTransition) => void>();
  private readonly messages = new Set<(message: WsIncoming) => void>();
  private current: ConnectionState = 'disconnected';
  private epoch = 0;

  get state(): ConnectionState { return this.current; }
  get sessionEpoch(): number { return this.epoch; }

  connect = vi.fn((_url: string) => {});
  disconnect = vi.fn(() => { this.current = 'disconnected'; });
  sendRaw = vi.fn((_message: Record<string, unknown>) => true);
  onBinary = vi.fn((sink: (buffer: ArrayBuffer) => void) => {
    this.binary.add(sink);
    return () => { this.binary.delete(sink); };
  });

  onStateChange(sink: (state: ConnectionState) => void): () => void {
    this.states.add(sink);
    return () => { this.states.delete(sink); };
  }
  onSessionTransition(sink: (session: ControlSessionTransition) => void): () => void {
    this.sessions.add(sink);
    return () => { this.sessions.delete(sink); };
  }
  onMessage(sink: (message: WsIncoming) => void): () => void {
    this.messages.add(sink);
    return () => { this.messages.delete(sink); };
  }

  /** Test-side drivers; not part of the faked surface. */
  change(next: ConnectionState): void {
    this.current = next;
    if (this.current === 'connected') this.epoch++;
    this.states.forEach(sink => sink(this.current));
    this.sessions.forEach(sink => sink({ state: this.current, epoch: this.epoch }));
  }
  receive(buffer: ArrayBuffer): void { this.binary.forEach(sink => sink(buffer)); }
  /** Deliver a parsed text frame the way ws-client's onText dispatches it. */
  message(frame: unknown): void {
    const msg = frame as WsIncoming;
    this.messages.forEach(sink => sink(msg));
  }
}

function channel() {
  const port = new ScopeChannelFake();
  return { port, change: (next: ConnectionState) => port.change(next) };
}

const request = { sourceId: 'radio', receiver: 0,
  view: { follow: 'vfo', spanHz: 100_000 }, width: 2800, maxFps: 20 } satisfies ScopeSubscription;
const grantedWire = { type: 'granted', sourceId: 'radio', receiver: 0, width: 2049, fps: 20,
  view: { startHz: 14_026_000, endHz: 14_074_000 }, coverage: { startHz: 14_026_000, endHz: 14_074_000 } } satisfies ScopeGranted;
/** What parseScopeGranted must produce from grantedWire: the typed grant. */
const grantedTyped = { type: 'granted', sourceId: 'radio', receiver: 0, width: 2049, fps: 20,
  view: { startHz: 14_026_000, endHz: 14_074_000 }, coverage: { startHz: 14_026_000, endHz: 14_074_000 } } satisfies ScopeGranted;

/** Drive one full scope negotiation and capture the observable outcome. */
async function negotiate(withAttachAck: boolean) {
  const { port, change } = channel();
  // The double implements exactly the WsChannel surface the controller
  // consumes; the one widening below supplies the members the factory's
  // type names but this path never touches.
  const surface: ScopeChannelSurface = port;
  const owner = new ScopeController(() => surface as WsChannel);
  const stream = owner.stream('hardware');
  stream.setSubscription(request);
  const sink = vi.fn(); stream.subscribe(sink);
  const geometryWrites: unknown[] = [];
  const unwatch = stream.geometry.subscribe(value => { geometryWrites.push(value); });
  const handle = await owner.hardwareScopeDriver.start();
  change('connected');
  port.sendRaw.mockClear();

  if (withAttachAck) {
    // The golden document must still describe a server-to-client text
    // frame whose type is the wire contract's attach-ack discriminator,
    // or this spec fails loudly before anything else runs.
    expect(attachAckScope.variant).toBe('text');
    expect(attachAckScope.direction).toBe('server->client');
    const ackFrame = JSON.parse(attachAckScope.text) as Record<string, unknown>;
    expect(ackFrame.type).toBe(wireAttachAckType);
    expect(ackFrame.channel).toBe('scope');
    // The attach ack itself, verbatim from the golden document's text —
    // parsed exactly as WsChannel.onText parses a server text frame
    // before dispatching to onMessage subscribers — must be a no-op.
    port.message(JSON.parse(attachAckScope.text));
    expect(get(stream.error)).toBeNull();
    expect(port.connect).toHaveBeenCalledTimes(1);
    expect(port.disconnect).not.toHaveBeenCalled();
    expect(port.sendRaw).not.toHaveBeenCalled();
    expect(sink).not.toHaveBeenCalled();
    expect(geometryWrites).toEqual([null]);
  }

  port.message(grantedWire);
  // Positive evidence, in each run (with and without the attach ack): the
  // granted text message was dispatched and produced the typed grant BEFORE
  // any binary frame is delivered. Comparing to a typed literal (not to the
  // other run) fails if the controller never registers channel.onMessage.
  expect(get(stream.granted)).toEqual(grantedTyped);
  const buffer = scopeWire({ sourceId: 'radio' });
  port.receive(buffer);
  const outcome = {
    granted: get(stream.granted),
    error: get(stream.error),
    sinkCalls: sink.mock.calls.length,
    lastFrameBuffer: sink.mock.lastCall?.[0]?.pixels?.buffer,
    lastGeometry: geometryWrites.at(-1),
    connectCalls: port.connect.mock.calls.length,
    sentAfter: port.sendRaw.mock.calls.map(call => call[0]),
  };
  unwatch();
  await owner.hardwareScopeDriver.stop(handle);
  return outcome;
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('scope channel attach acknowledgement compatibility (MOR-4478)', () => {
  it('ignores the golden {"type":"attached"} text frame and then negotiates normally', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const baseline = await negotiate(false);
    expect(errorSpy).not.toHaveBeenCalled();

    const withAck = await negotiate(true);
    expect(errorSpy).not.toHaveBeenCalled();

    // Identical outcomes with and without the attach ack.
    expect(withAck.granted).toEqual(baseline.granted);
    expect(withAck.error).toBeNull();
    expect(withAck.sinkCalls).toBe(baseline.sinkCalls);
    expect(withAck.lastFrameBuffer).toBeDefined();
    expect(withAck.lastGeometry).toEqual(baseline.lastGeometry);
    expect(withAck.connectCalls).toBe(1);
    expect(withAck.sentAfter).toEqual([]);
  });
});
