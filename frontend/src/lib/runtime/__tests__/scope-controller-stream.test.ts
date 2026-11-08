import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import { scopeWire } from './scope-wire-fixture';
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
      if (state === 'connected') return;
      state = 'connected'; epoch += 1;
      for (const update of states) update(state);
      for (const update of sessions) update({ state, epoch });
    },
    disconnect() { state = 'disconnected'; for (const update of states) update(state); },
    sendRaw: vi.fn(() => true),
    onMessage: () => () => {},
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

const radioAt = (seq: number, source: 'hardware' | 'audio_fft' = 'hardware') => Array.from(new Uint8Array(scopeWire({ source: source === 'audio_fft' ? 'audio_fft' : 'radio', sourceId: source === 'audio_fft' ? 'audio_fft' : 'scope', seq, pixels: new Uint8Array([seq & 255, 64, 128, 255]) })));

afterEach(() => { vi.restoreAllMocks(); });

describe('ScopeController shared streams (MOR-4270)', () => {
  it('exposes one stream per source over the same channel the driver opens', () => {
    const channels = new Map<string, ReturnType<typeof fakeChannel>>();
    const controller = new ScopeController(vi.fn((name: string) => {
      let channel = channels.get(name);
      if (!channel) { channel = fakeChannel(); channels.set(name, channel); }
      return channel as unknown as WsChannel;
    }));
    expect(controller.stream('hardware')).toBe(controller.stream('hardware'));
    expect(controller.stream('audio_fft')).not.toBe(controller.stream('hardware'));
    const sink = vi.fn();
    controller.stream('hardware').subscribe(sink);
    controller.hardwareScopeDriver.start();
    const channel = channels.get('scope')!;
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
    const controller = new ScopeController(vi.fn((name: string) => name === 'scope' ? channel as unknown as WsChannel : fakeChannel() as unknown as WsChannel));
    const receipts: unknown[] = [];
    controller.subscribeTraceReceipt((receipt) => receipts.push(receipt));
    controller.hardwareScopeDriver.start();
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
    channel.receive(radioAt(0));
    expect(stream.latest('scope:0')).not.toBeNull();
    channel.disconnect();
    channel.connect();
    expect(stream.latest('scope:0')).toBeNull();
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

it.each(['hardware', 'audio_fft'] as const)('expires a phased %s receipt at its exact 500 ms deadline', async source => {
  vi.useFakeTimers(); let now = 0;
  const timing = {
    now: () => now,
    setTimeout: vi.fn((callback: () => void, delay: number) => setTimeout(callback, delay)),
    clearTimeout: vi.fn((handle: ReturnType<typeof setTimeout>) => clearTimeout(handle)),
    setInterval: vi.fn((callback: () => void, delay: number) => setInterval(callback, delay)),
    clearInterval: vi.fn((handle: ReturnType<typeof setInterval>) => clearInterval(handle)),
  };
  const channel = fakeChannel(); const controller = new ScopeController(() => channel as unknown as WsChannel, timing);
  const stream = controller.stream(source); const geometry = vi.fn(); stream.geometry.subscribe(geometry);
  const driver = source === 'hardware' ? controller.hardwareScopeDriver : controller.audioFftDriver;
  const handle = await driver.start();
  try {
    now = 50; vi.advanceTimersByTime(50); channel.receive(radioAt(0, source));
    now = 549; vi.advanceTimersByTime(499);
    expect(geometry.mock.lastCall?.[0]).toMatchObject({ freshness: 'fresh' });
    expect(controller.snapshotHealth(source).frameSeen).toBe(true);
    now = 550; vi.advanceTimersByTime(1);
    expect(geometry.mock.lastCall?.[0]).toMatchObject({ freshness: 'stale' });
    expect(controller.snapshotHealth(source).frameSeen).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  } finally { await driver.stop(handle); vi.useRealTimers(); }
});

it('keeps one pending deadline over a 100-frame burst and reschedules only when it wakes', async () => {
  vi.useFakeTimers(); let now = 0;
  const timing = {
    now: () => now,
    setTimeout: vi.fn((callback: () => void, delay: number) => setTimeout(callback, delay)),
    clearTimeout: vi.fn((handle: ReturnType<typeof setTimeout>) => clearTimeout(handle)),
    setInterval: vi.fn((callback: () => void, delay: number) => setInterval(callback, delay)),
    clearInterval: vi.fn((handle: ReturnType<typeof setInterval>) => clearInterval(handle)),
  };
  const channel = fakeChannel(); const controller = new ScopeController(() => channel as unknown as WsChannel, timing);
  const handle = await controller.hardwareScopeDriver.start();
  try {
    expect(timing.setTimeout).not.toHaveBeenCalled();
    now = 50; vi.advanceTimersByTime(50); channel.receive(radioAt(0));
    for (let frame = 1; frame <= 100; frame++) {
      now += 1; vi.advanceTimersByTime(1); channel.receive(radioAt(frame));
    }
    expect(timing.setTimeout).toHaveBeenCalledTimes(1);
    expect(timing.clearTimeout).not.toHaveBeenCalled();
    expect(timing.setInterval).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(1);
    now = 550; vi.advanceTimersByTime(400);
    expect(controller.snapshotHealth('hardware').frameSeen).toBe(true);
    expect(timing.setTimeout).toHaveBeenCalledTimes(2);
    expect(timing.setTimeout.mock.lastCall?.[1]).toBe(100);
    expect(timing.clearTimeout).not.toHaveBeenCalled();
    await controller.hardwareScopeDriver.stop(handle);
    expect(timing.clearTimeout).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
    now = 2_000; vi.advanceTimersByTime(1_450);
    expect(timing.setTimeout).toHaveBeenCalledTimes(2);
  } finally { await controller.hardwareScopeDriver.stop(handle); vi.useRealTimers(); }
});
