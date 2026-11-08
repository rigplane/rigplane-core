import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import type { WsChannel } from '$lib/transport/ws-client';

function fakeChannel() {
  const binary = new Set<(buffer: ArrayBuffer) => void>();
  const states = new Set<(state: string) => void>();
  const sessions = new Set<(transition: { state: string; epoch: number }) => void>();
  let epoch = 0;
  let state = 'disconnected';
  return {
    get sessionEpoch() { return epoch; },
    get state() { return state; },
    connect() {
      state = 'connected'; epoch += 1;
      for (const update of states) update(state);
      for (const update of sessions) update({ state, epoch });
    },
    disconnect() { state = 'disconnected'; for (const update of states) update(state); },
    onBinary(handler: (buffer: ArrayBuffer) => void) { binary.add(handler); return () => binary.delete(handler); },
    onStateChange(handler: (state: string) => void) { states.add(handler); return () => states.delete(handler); },
    onSessionTransition(handler: (transition: { state: string; epoch: number }) => void) {
      sessions.add(handler); return () => sessions.delete(handler);
    },
    receive(bytes: number[]) {
      const buffer = Uint8Array.from(bytes).buffer;
      for (const handler of binary) handler(buffer);
      return buffer;
    },
  };
}

// Real Python-encoder golden bytes (see fixtures/generate-scope-wire.py).
const RADIO_FRAME = [1, 0, 0, 128, 159, 213, 0, 32, 38, 215, 0, 255, 255, 0, 4, 0, 0, 64, 128, 255];
const radioAt = (seq: number) => { const bytes = [...RADIO_FRAME]; bytes[11] = seq; bytes[12] = 0; bytes[16] = seq; return bytes; };

afterEach(() => { vi.restoreAllMocks(); });

describe('ScopeController shared streams (MOR-4270)', () => {
  it('exposes one stream per source over the same channel the driver opens', () => {
    const channel = fakeChannel();
    const controller = new ScopeController(vi.fn((name: string) => {
      expect(['scope', 'audio-scope']).toContain(name);
      return channel as unknown as WsChannel;
    }));
    expect(controller.stream('hardware')).toBe(controller.stream('hardware'));
    expect(controller.stream('audio_fft')).not.toBe(controller.stream('hardware'));
    const sink = vi.fn();
    controller.stream('hardware').subscribe(sink);
    controller.hardwareScopeDriver.start();
    channel.connect();
    const buffer = channel.receive(radioAt(2));
    const [frame, slotKey] = sink.mock.lastCall!;
    expect(frame.pixels.buffer).toBe(buffer); // zero copies to the renderer sink
    expect(frame.epoch).toBe(1);
    expect(slotKey).toBe('scope:0');
    expect(controller.snapshotHealth('hardware')).toMatchObject({ demanded: true, transport: 'connected', frameSeen: true });
  });

  it('counts accepted frames monotonically and publishes trace receipts with channel identity', () => {
    vi.spyOn(performance, 'now').mockReturnValue(1_000);
    const channel = fakeChannel();
    const controller = new ScopeController(vi.fn(() => channel as unknown as WsChannel));
    const receipts: unknown[] = [];
    controller.subscribeTraceReceipt((receipt) => receipts.push(receipt));
    controller.hardwareScopeDriver.start();
    channel.connect();
    channel.receive(radioAt(0));
    channel.receive(radioAt(1));
    const accepted = receipts.filter((receipt) => (receipt as { kind: string }).kind === 'accepted');
    expect(accepted.map((receipt) => (receipt as { token: { acceptedSequence: number } }).token.acceptedSequence))
      .toEqual([1, 2]);
    expect(accepted.map((receipt) => (receipt as { token: { transportEpoch: number } }).token.transportEpoch))
      .toEqual([1, 1]);
    channel.receive([1]); // malformed → rejected receipt, health drops frameSeen
    expect(receipts.filter((receipt) => (receipt as { kind: string }).kind === 'rejected'))
      .toHaveLength(1);
  });

  it('a session transition resets the stream so stale-session pixels cannot survive', () => {
    const channel = fakeChannel();
    const controller = new ScopeController(vi.fn(() => channel as unknown as WsChannel));
    const stream = controller.stream('hardware');
    controller.hardwareScopeDriver.start();
    channel.connect();
    channel.receive(radioAt(0));
    expect(stream.latest('scope:0')).not.toBeNull();
    channel.disconnect();
    channel.connect();
    expect(stream.latest('scope:0')).toBeNull();
    const geometry = stream.geometry.subscribe(() => {});
    void geometry;
  });

  it('marks stale through the liveness clock instead of per-frame timers', () => {
    vi.useFakeTimers();
    vi.spyOn(performance, 'now').mockImplementation(() => Date.now());
    try {
      const channel = fakeChannel();
      const controller = new ScopeController(vi.fn(() => channel as unknown as WsChannel));
      const stream = controller.stream('hardware');
      const geometry: unknown[] = [];
      stream.geometry.subscribe((value) => geometry.push(value));
      controller.hardwareScopeDriver.start();
      channel.connect();
      channel.receive(radioAt(0));
      expect((geometry.at(-1) as { freshness: string } | null)?.freshness).toBe('fresh');
      vi.advanceTimersByTime(900);
      expect((geometry.at(-1) as { freshness: string } | null)?.freshness).toBe('stale');
      expect(controller.snapshotHealth('hardware')).toMatchObject({ frameSeen: false });
    } finally {
      vi.useRealTimers();
    }
  });
});
