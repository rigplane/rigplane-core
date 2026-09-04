import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { ScopeController } from '../scope-controller.svelte';
import { ScopeFrameHost } from '../scope-frame-host';

function channel() {
  const binary = new Set<(buffer: ArrayBuffer) => void>();
  const states = new Set<(state: string) => void>();
  const sessions = new Set<(value: { state: string; epoch: number }) => void>();
  let state = 'disconnected';
  let epoch = 0;
  return {
    get state() { return state; },
    get sessionEpoch() { return epoch; },
    connect: vi.fn(), disconnect: vi.fn(),
    onBinary(handler: (buffer: ArrayBuffer) => void) {
      binary.add(handler); return () => { binary.delete(handler); };
    },
    onStateChange(handler: (value: string) => void) {
      states.add(handler); return () => { states.delete(handler); };
    },
    onSessionTransition(handler: (value: { state: string; epoch: number }) => void) {
      sessions.add(handler); return () => { sessions.delete(handler); };
    },
    fire(buffer: ArrayBuffer) { for (const handler of [...binary]) handler(buffer); },
    transition(next: string) {
      if (next === 'connected' && state !== 'connected') epoch += 1;
      state = next;
      for (const handler of [...states]) handler(next);
      for (const handler of [...sessions]) handler({ state: next, epoch });
    },
    counts: () => [binary.size, states.size, sessions.size],
  };
}

function frame(receiver: 0 | 1 = 0, pixels = [0, 128, 255], wireSequence = 17): ArrayBuffer {
  const buffer = new ArrayBuffer(16 + pixels.length);
  const view = new DataView(buffer);
  view.setUint8(0, 0x01);
  view.setUint8(1, receiver);
  view.setUint32(3, 14_000_000, true);
  view.setUint32(7, 14_100_000, true);
  view.setUint16(12, wireSequence, true);
  view.setUint16(14, pixels.length, true);
  pixels.forEach((sample, index) => view.setUint8(16 + index, sample));
  return buffer;
}

function clock() {
  let now = 0;
  let id = 0;
  const timers = new Map<number, { at: number; callback: () => void; cancelled: boolean }>();
  return {
    timing: {
      now: () => now,
      setTimeout(callback: () => void, delay: number) {
        const token = ++id;
        timers.set(token, { at: now + delay, callback, cancelled: false });
        return token as unknown as ReturnType<typeof setTimeout>;
      },
      clearTimeout(token: ReturnType<typeof setTimeout>) {
        const timer = timers.get(token as unknown as number);
        if (timer) timer.cancelled = true;
      },
    },
    advance(ms: number) {
      now += ms;
      for (const [token, timer] of [...timers]) {
        if (!timer.cancelled && timer.at <= now) {
          timers.delete(token);
          timer.callback();
        }
      }
    },
    active: () => [...timers.values()].filter((timer) => !timer.cancelled).length,
  };
}

function rig() {
  const time = clock();
  const channels = { scope: channel(), 'audio-scope': channel() };
  const controller = new ScopeController(
    (name) => channels[name as keyof typeof channels] as never,
    time.timing,
  );
  const host = new ScopeFrameHost(controller);
  return { time, channels, controller, host };
}

describe('ScopeFrameHost MOR-2326 lifecycle', () => {
  it.each([
    ['hardware', 'hardwareScopeDriver', 'scope'],
    ['audio-fft', 'audioFftDriver', 'audio-scope'],
  ] as const)('resolves a nominal %s frame below 500 ms', async (source, driverName, channelName) => {
    const { time, channels, controller, host } = rig();
    host.updateAuthority({ source, receiver: 'MAIN', providerGeneration: 12 });
    const driver = controller[driverName];
    const handle = await driver.start();
    const transport = channels[channelName];
    transport.transition('connected');
    transport.fire(frame());
    time.advance(499);

    expect(host.snapshot()).toEqual({
      state: 'live',
      frame: {
        source, receiver: 'MAIN', freshness: 'fresh',
        startHz: 14_000_000, endHz: 14_100_000,
        normalizedBins: [0, 128 / 255, 1],
      },
    });
    await driver.stop(handle);
    host.dispose();
  });

  it('turns the exact 500 ms silence boundary into stale ghost geometry', async () => {
    const { time, channels, controller, host } = rig();
    const updates = vi.fn();
    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 1 });
    host.subscribe(updates);
    const handle = await controller.hardwareScopeDriver.start();
    channels.scope.transition('connected');
    channels.scope.fire(frame());
    expect(host.snapshot().state).toBe('live');

    time.advance(500);
    expect(host.snapshot()).toEqual({ state: 'ghost', reason: 'stale' });
    expect(updates).toHaveBeenLastCalledWith({ state: 'ghost', reason: 'stale' });
    await controller.hardwareScopeDriver.stop(handle);
    expect(time.active()).toBe(0);
    host.dispose();
  });

  it('invalidates provider, receiver, source, transport epoch, and demand boundaries synchronously', async () => {
    const { channels, controller, host } = rig();
    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 4 });
    let handle = await controller.hardwareScopeDriver.start();
    channels.scope.transition('connected');
    channels.scope.fire(frame());
    expect(host.snapshot().state).toBe('live');

    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 5 });
    expect(host.snapshot().state).toBe('ghost');
    channels.scope.fire(frame());
    expect(host.snapshot().state).toBe('ghost');

    host.updateAuthority({ source: 'hardware', receiver: null, providerGeneration: 5 });
    expect(host.snapshot()).toEqual({ state: 'ghost', reason: 'receiver-unknown' });
    host.updateAuthority({ source: 'audio-fft', receiver: 'MAIN', providerGeneration: 5 });
    expect(host.snapshot().state).toBe('ghost');

    await controller.hardwareScopeDriver.stop(handle);
    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 5 });
    handle = await controller.hardwareScopeDriver.start();
    channels.scope.transition('reconnecting');
    channels.scope.transition('connected');
    channels.scope.fire(frame());
    expect(host.snapshot().state).toBe('live');
    channels.scope.transition('reconnecting');
    expect(host.snapshot().state).toBe('ghost');
    await controller.hardwareScopeDriver.stop(handle);
    expect(host.snapshot().state).toBe('ghost');
    host.dispose();
  });

  it('fails malformed and mismatched frames closed without fallback or zero bins', async () => {
    const { channels, controller, host } = rig();
    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 2 });
    const handle = await controller.hardwareScopeDriver.start();
    channels.scope.transition('connected');
    channels.scope.fire(frame());
    expect(host.snapshot().state).toBe('live');

    channels.scope.fire(frame(1));
    expect(host.snapshot()).toEqual({ state: 'ghost', reason: 'missing' });
    channels.scope.fire(new ArrayBuffer(16));
    const malformed = host.snapshot();
    expect(malformed.state).toBe('ghost');
    expect(malformed).not.toHaveProperty('frame');
    await controller.hardwareScopeDriver.stop(handle);
    host.dispose();
  });

  it('removes host and channel subscriptions and isolates a throwing observer', async () => {
    const { channels, controller, host, time } = rig();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    host.updateAuthority({ source: 'audio-fft', receiver: 'MAIN', providerGeneration: 3 });
    host.subscribe(() => { throw new Error('observer'); });
    const healthy = vi.fn();
    host.subscribe(healthy);
    const handle = await controller.audioFftDriver.start();
    channels['audio-scope'].transition('connected');
    channels['audio-scope'].fire(frame());
    expect(healthy).toHaveBeenCalledWith(expect.objectContaining({ state: 'live' }));
    expect(warn).toHaveBeenCalledWith('Scope frame host subscriber failed', expect.any(Error));

    await controller.audioFftDriver.stop(handle);
    host.dispose();
    expect(channels['audio-scope'].counts()).toEqual([0, 0, 0]);
    expect(time.active()).toBe(0);
  });

  it('keeps timers, transport, wall clock, freshness inputs, and fallback out of the host', () => {
    const source = readFileSync(resolve('src/lib/runtime/scope-frame-host.ts'), 'utf8');
    expect(source).not.toMatch(/setTimeout|setInterval|requestAnimationFrame|Date\.now/);
    expect(source).not.toMatch(/5_000|10_000|freshness\s*:|zero.?fill|fallback/i);
    expect(source).not.toMatch(/getChannel|connect\(|disconnect\(/);
  });
});
