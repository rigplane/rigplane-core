/**
 * Compatibility tests for the data-channel attach acknowledgement
 * (MOR-4478): the server under review will send one extra text frame
 * `{"type":"attached","v":1,"channel":"scope"}` as the first frame on the
 * scope channel. Today's shipped frontend must ignore it: no state change,
 * no error, no reconnect, and the following `granted` + binary frame are
 * handled exactly as without it.
 *
 * Drives the real controller message path (the controller that owns the
 * scope socket) exactly the way scope-controller-v2.test.ts does.
 */
import { get } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScopeController } from '../scope-controller.svelte';
import { scopeWire } from './scope-wire-fixture';
import type { WsChannel } from '$lib/transport/ws-client';

function channel() {
  const binary = new Set<(buffer: ArrayBuffer) => void>();
  const states = new Set<(state: string) => void>();
  const sessions = new Set<(session: { state: string; epoch: number }) => void>();
  const messages = new Set<(message: unknown) => void>();
  let state = 'disconnected'; let epoch = 0;
  const port = {
    get state() { return state; }, get sessionEpoch() { return epoch; },
    connect: vi.fn((_url: string) => {}), disconnect: vi.fn(() => { state = 'disconnected'; }),
    sendRaw: vi.fn((_message: Record<string, unknown>) => true),
    onBinary: vi.fn((sink: (buffer: ArrayBuffer) => void) => { binary.add(sink); return () => binary.delete(sink); }),
    onStateChange(sink: (state: string) => void) { states.add(sink); return () => states.delete(sink); },
    onSessionTransition(sink: (session: { state: string; epoch: number }) => void) { sessions.add(sink); return () => sessions.delete(sink); },
    onMessage(sink: (message: unknown) => void) { messages.add(sink); return () => messages.delete(sink); },
    change(next: string) {
      state = next; if (state === 'connected') epoch++;
      states.forEach(sink => sink(state)); sessions.forEach(sink => sink({ state, epoch }));
    },
    receive(buffer: ArrayBuffer) { binary.forEach(sink => sink(buffer)); },
    message(message: unknown) { messages.forEach(sink => sink(message)); },
  };
  return { port, change: port.change };
}

const request = { sourceId: 'radio', receiver: 0,
  view: { follow: 'vfo' as const, spanHz: 100_000 }, width: 2800, maxFps: 20 };
const grantedWire = { type: 'granted', sourceId: 'radio', receiver: 0, width: 2049, fps: 20,
  view: { startHz: 14_026_000, endHz: 14_074_000 }, coverage: { startHz: 14_026_000, endHz: 14_074_000 } };
/** What parseScopeGranted must produce from grantedWire: the typed grant. */
const grantedTyped = { type: 'granted', sourceId: 'radio', receiver: 0, width: 2049, fps: 20,
  view: { startHz: 14_026_000, endHz: 14_074_000 }, coverage: { startHz: 14_026_000, endHz: 14_074_000 } };

/** Drive one full scope negotiation and capture the observable outcome. */
async function negotiate(withAttachAck: boolean) {
  const { port, change } = channel();
  const owner = new ScopeController(() => port as unknown as WsChannel);
  const stream = owner.stream('hardware');
  stream.setSubscription(request);
  const sink = vi.fn(); stream.subscribe(sink);
  const geometryWrites: unknown[] = [];
  const unwatch = stream.geometry.subscribe(value => { geometryWrites.push(value); });
  const handle = await owner.hardwareScopeDriver.start();
  change('connected');
  port.sendRaw.mockClear();

  if (withAttachAck) {
    port.message({ type: 'attached', v: 1, channel: 'scope' });
    // The attach ack itself must be a complete no-op.
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
  it('ignores the {"type":"attached"} text frame and then negotiates normally', async () => {
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
