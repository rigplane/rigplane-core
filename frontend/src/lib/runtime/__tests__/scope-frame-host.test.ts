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
  function transition(next: string) {
    if (next === 'connected' && state !== 'connected') epoch += 1;
    state = next;
    for (const handler of [...states]) handler(next);
    for (const handler of [...sessions]) handler({ state: next, epoch });
  }
  return {
    get state() { return state; },
    get sessionEpoch() { return epoch; },
    connect: vi.fn(() => transition('connecting')), disconnect: vi.fn(() => transition('disconnected')),
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
    transition,
    retainBinary: () => [...binary][0],
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
  it('publishes resolution and receipt from one read, sharing the qualified envelope', async () => {
    const { channels, controller, host } = rig();
    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 12 });
    const handle = await controller.hardwareScopeDriver.start();
    channels.scope.transition('connected');
    const updates = vi.fn();
    const legacy = vi.fn();
    host.subscribePresentation(updates);
    host.subscribe(legacy);
    const read = vi.spyOn(controller, 'snapshotFrameEvidence');
    const input = frame();
    channels.scope.fire(input);

    expect(read).toHaveBeenCalledTimes(1);
    const projected = updates.mock.lastCall![0];
    const evidence = read.mock.results[0].value;
    expect(projected.envelope).toBe(evidence.envelope);
    expect(projected.authority).toBe(evidence.authority);
    expect(projected.resolution).toBe(legacy.mock.lastCall![0]);
    expect(projected.resolution.state).toBe('live');
    expect(projected.envelope.acceptedSequence).toBeGreaterThan(0);
    expect(Object.isFrozen(projected)).toBe(true);
    expect(Object.isFrozen(projected.authority)).toBe(true);
    expect(Object.isFrozen(projected.envelope)).toBe(true);
    expect(Object.isFrozen(projected.envelope.frame)).toBe(true);
    expect(Object.isFrozen(projected.resolution)).toBe(true);
    expect(Object.isFrozen(projected.resolution.frame.normalizedBins)).toBe(true);
    new Uint8Array(input).fill(0);
    expect([...projected.envelope.frame.pixels]).toEqual([0, 128, 255]);
    expect(projected.resolution.frame.normalizedBins).toEqual([0, 128 / 255, 1]);
    expect(host.snapshotPresentation()).toEqual(projected);
    await controller.hardwareScopeDriver.stop(handle);
    host.dispose();
  });

  it('publishes exact expiry and selected-receiver null despite unrelated raw liveness', async () => {
    const { time, channels, controller, host } = rig();
    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 1 });
    const handle = await controller.hardwareScopeDriver.start();
    channels.scope.transition('connected');
    const updates = vi.fn();
    host.subscribePresentation(updates);
    channels.scope.fire(frame());
    const live = updates.mock.lastCall![0];
    time.advance(499);
    expect(host.snapshotPresentation().resolution.state).toBe('live');
    time.advance(1);
    expect(updates.mock.lastCall![0]).toMatchObject({
      envelope: live.envelope, resolution: { state: 'ghost', reason: 'stale' },
    });
    expect(live.resolution.state).toBe('live');
    channels.scope.fire(frame(1));
    expect(controller.hardwareScopeFrameLive).toBe(true);
    expect(updates.mock.lastCall![0]).toMatchObject({
      envelope: null, resolution: { state: 'ghost', reason: 'missing' },
    });
    channels.scope.fire(frame());
    channels.scope.transition('reconnecting');
    expect(updates.mock.lastCall![0].resolution.state).toBe('ghost');
    await controller.hardwareScopeDriver.stop(handle);
    expect(updates.mock.lastCall![0].envelope).toBeNull();
    expect(host.snapshotPresentation().authority.demanded).toBe(false);
    host.updateAuthority(null);
    expect(updates.mock.lastCall![0].envelope).toBeNull();
    host.dispose();
  });

  it('retires provider receipts, rejects old callbacks, and recovers under the original handle', async () => {
    const { channels, controller, host } = rig();
    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 1 });
    const handle = await controller.hardwareScopeDriver.start();
    channels.scope.transition('connected');
    const updates = vi.fn();
    host.subscribePresentation(updates);
    channels.scope.fire(frame());
    const original = updates.mock.lastCall![0];
    const retired = channels.scope.retainBinary();
    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 2 });
    expect(updates.mock.lastCall![0]).toMatchObject({
      envelope: null, authority: { providerGeneration: 2 }, resolution: { state: 'ghost' },
    });
    channels.scope.fire(frame());
    expect(host.snapshotPresentation().envelope).toBeNull();
    retired(frame());
    expect(host.snapshotPresentation().envelope).toBeNull();
    expect(channels.scope.connect).toHaveBeenCalledTimes(2);
    expect(channels.scope.disconnect).toHaveBeenCalledTimes(1);
    channels.scope.transition('connected');
    channels.scope.fire(frame());
    const renewed = updates.mock.lastCall![0];
    expect(renewed.resolution.state).toBe('live');
    expect(renewed.envelope.providerGeneration).toBe(2);
    expect(renewed.envelope.acceptedSequence).toBeGreaterThan(original.envelope.acceptedSequence);
    expect(renewed.envelope.transportEpoch).toBeGreaterThan(original.envelope.transportEpoch);
    retired(frame());
    expect(host.snapshotPresentation()).toEqual(renewed);
    channels.scope.fire(new ArrayBuffer(16));
    expect(updates.mock.lastCall![0].resolution.state).toBe('ghost');
    expect(original.resolution.state).toBe('live');
    await controller.hardwareScopeDriver.stop(handle);
    host.dispose();
  });

  it.each([-1, NaN, Infinity])('rejects invalid clock age %s in the presentation', async (age) => {
    const { time, channels, controller, host } = rig();
    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 1 });
    const handle = await controller.hardwareScopeDriver.start();
    channels.scope.transition('connected');
    channels.scope.fire(frame());
    time.advance(age);
    expect(host.snapshotPresentation().resolution.state).toBe('ghost');
    await controller.hardwareScopeDriver.stop(handle);
    host.dispose();
  });

  it('shares one evidence subscription and isolates/removes presentation subscribers', () => {
    const { controller, host } = rig();
    host.dispose();
    const unsubscribe = vi.fn();
    const subscribe = vi.spyOn(controller, 'subscribeFrameEvidence').mockReturnValue(unsubscribe);
    const owner = new ScopeFrameHost(controller);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const healthy = vi.fn();
    const legacy = vi.fn();
    owner.subscribePresentation(() => { throw new Error('observer'); });
    const remove = owner.subscribePresentation(healthy);
    owner.subscribe(legacy);
    const notify = subscribe.mock.calls[0][0];
    notify();
    expect(healthy).toHaveBeenCalledTimes(1);
    expect(legacy).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith('Scope frame host subscriber failed', expect.any(Error));
    remove();
    notify();
    expect(healthy).toHaveBeenCalledTimes(1);
    expect(subscribe).toHaveBeenCalledTimes(1);
    owner.dispose();
    owner.dispose();
    owner.subscribePresentation(healthy);
    notify();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    expect(healthy).toHaveBeenCalledTimes(1);
    expect(legacy).toHaveBeenCalledTimes(2);
  });

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
