import { describe, expect, it, vi } from 'vitest';
import { ResourceDemand, type AppResource } from '../resource-demand';
import { PresentationResourceHost } from '../resource-host';
const one = (model: ResourceDemand<string>) => {
  const operations = model.takeOperations();
  expect(operations).toHaveLength(1);
  return operations[0];
};
const supersede = (model: ResourceDemand<string>, resource: AppResource, consumer: string) => {
  const lease = model.acquire(resource, consumer), start = one(model);
  model.release(lease);
  return start;
};
const ready = (model: ResourceDemand<string>, resource: AppResource) => {
  model.configure(resource, { available: true, selected: true });
};
const activate = (model: ResourceDemand<string>, resource: AppResource, handle: string) => {
  ready(model, resource);
  const lease = model.acquire(resource, handle);
  expect(model.completeStart(one(model), { handle })).toBe(true);
  return lease;
};
const overlap = (model: ResourceDemand<string>, resource: AppResource) => {
  ready(model, resource);
  const stale = supersede(model, resource, 'old');
  const activeLease = model.acquire(resource, 'new');
  return { stale, current: one(model), activeLease };
};
const cleanupCases = [
  [false, ['rx-audio', 'rx-audio'], ['H', 'H'], ['rx-audio:H', 'rx-audio:H']],
  [true, ['rx-audio', 'rx-audio'], ['H', 'H'], ['rx-audio:H', 'rx-audio:H']],
  [false, ['rx-audio', 'rx-audio'], ['A', 'B'], ['rx-audio:A', 'rx-audio:B']],
  [false, ['rx-audio', 'hardware-scope'], ['H', 'H'], ['rx-audio:H', 'hardware-scope:H']],
] as const;
describe('ResourceDemand foundation', () => {
  it('uses exact leases and tracks cleanup per issued start', () => {
    const model = new ResourceDemand<string>('session-1');
    const wider = {
      available: false,
      selected: true,
      demand: 99,
      activeHandle: 'ghost',
      generation: 99,
      health: 'streaming',
      pending: { kind: 'start' },
    };
    model.configure('audio-fft', wider);
    model.acquire('audio-fft', 'panel');
    expect(model.snapshot('audio-fft')).toEqual({
      available: false, selected: true, demand: 1, generation: 0, health: 'inactive',
    });
    model.configure('rx-audio', { available: true, selected: true });
    const staleA = supersede(model, 'rx-audio', 'A');
    const late = ['B', 'C'].map((consumer) => supersede(model, 'rx-audio', consumer));
    const first = model.acquire('rx-audio', 'first'), start = one(model);
    const shared = model.acquire('rx-audio', 'shared');
    expect(model.takeOperations()).toEqual([]);
    expect(model.release({ ...shared } as typeof shared)).toBe(false);
    expect(new ResourceDemand<string>('session-1').release(shared)).toBe(false);
    expect(model.completeStart({ ...start }, { handle: 'fake' })).toBe(false);
    expect(model.completeStart(staleA, { handle: 'stable' })).toBe(false);
    expect(model.completeStart(start, { handle: 'stable' })).toBe(true);
    expect(model.completeStart(start, { handle: 'stable' })).toBe(false);
    expect(model.release(first)).toBe(true);
    expect(model.release(shared)).toBe(true);
    const stop = one(model);
    expect(model.completeStop(stop)).toBe(true);
    for (const operation of late) expect(model.completeStart(operation, { handle: 'stable' })).toBe(false);
    expect(model.takeOperations()).toMatchObject(
      [staleA, ...late].map((operation) => ({
        kind: 'dispose',
        generation: operation.generation,
        handle: 'stable',
      })),
    );
    const replacement = model.acquire('rx-audio', 'replacement');
    model.completeStart(one(model), { handle: 'next' });
    model.release(replacement);
    const staleStop = one(model);
    model.acquire('rx-audio', 'overlap');
    model.completeStart(one(model), { handle: 'latest' });
    expect(model.completeStop(staleStop)).toBe(false);
  });
  it.each(cleanupCases)('emits orphan cleanup per start issuance %#', (drain, resources, handles, expected) => {
    const model = new ResourceDemand<string>('session-1');
    resources.forEach((resource) => model.configure(resource, { available: true, selected: true }));
    const starts = resources.map((resource, index) => supersede(model, resource, String(index)));
    const emitted = starts.flatMap((start, index) => {
      model.completeStart(start, { handle: handles[index] });
      return drain ? model.takeOperations() : [];
    });
    emitted.push(...model.takeOperations());
    expect(emitted.map((op) => 'handle' in op && `${op.resource}:${op.handle}`)).toEqual(expected);
  });
  it('disposes a reused singleton once per abandoned start issuance', () => {
    const model = new ResourceDemand<string>('session-1');
    ready(model, 'rx-audio');
    const first = supersede(model, 'rx-audio', 'first');
    const second = supersede(model, 'rx-audio', 'second');
    expect(model.completeStart(first, { handle: 'singleton' })).toBe(false);
    expect(model.completeStart(second, { handle: 'singleton' })).toBe(false);
    expect(model.takeOperations()).toMatchObject([
      { kind: 'dispose', generation: first.generation, handle: 'singleton' },
      { kind: 'dispose', generation: second.generation, handle: 'singleton' },
    ]);
    expect(model.completeStart(second, { handle: 'singleton' })).toBe(false);
    expect(model.takeOperations()).toEqual([]);
  });
  it('balances repeated same-microtask cycles from a singleton driver', async () => {
    const handle = {};
    const dispose = vi.fn();
    const stop = vi.fn();
    const host = new PresentationResourceHost<object>('session-1');
    host.configure('rx-audio', {
      available: true,
      selected: true,
      driver: { start: () => handle, stop, dispose },
    });
    const first = host.acquire('rx-audio', 'first');
    expect(host.release(first)).toBe(true);
    const second = host.acquire('rx-audio', 'second');
    expect(host.release(second)).toBe(true);
    await host.teardown();
    expect(dispose.mock.calls).toEqual([[handle], [handle]]);
    expect(stop).not.toHaveBeenCalled();
    expect(host.snapshot('rx-audio')).toMatchObject({
      demand: 0,
      health: 'inactive',
    });
  });
  it('defers singleton cleanup until pending adoption', () => {
    const model = new ResourceDemand<string>('session-1');
    ready(model, 'rx-audio');
    const staleStart = supersede(model, 'rx-audio', 'stale');
    const liveLease = model.acquire('rx-audio', 'live');
    const liveStart = one(model);
    expect(model.completeStart(staleStart, { handle: 'stable' })).toBe(false);
    expect([model.takeOperations(), model.takeOperations()]).toEqual([[], []]);
    expect(model.completeStart(liveStart, { handle: 'stable' })).toBe(true);
    model.release(liveLease);
    const stop = one(model);
    expect(stop.kind).toBe('stop');
    expect(model.takeOperations()).toEqual([]);
    expect(model.completeStop(stop)).toBe(true);
    expect(one(model)).toMatchObject({
      kind: 'dispose',
      generation: staleStart.generation,
      handle: 'stable',
    });
  });
  it('requires explicit retry and disposes a completed abandoned start once', () => {
    const model = new ResourceDemand<string>('session-1');
    ready(model, 'audio-fft');
    const lease = model.acquire('audio-fft', 'panel');
    expect(model.completeStart(one(model), { error: 'offline' })).toBe(true);
    model.configure('audio-fft', { available: true, selected: false });
    ready(model, 'audio-fft');
    expect(model.takeOperations()).toEqual([]);
    model.retry('audio-fft');
    const retry = one(model);
    model.release(lease);
    expect(model.completeStart(retry, { handle: 'late' })).toBe(false);
    expect(one(model)).toMatchObject({ kind: 'dispose', handle: 'late' });
    expect(model.completeStart(retry, { handle: 'late' })).toBe(false);
    expect(model.takeOperations()).toEqual([]);
  });
  it('tears down active work once and invalidates the old session', () => {
    const model = new ResourceDemand<string>('session-1');
    const active = activate(model, 'hardware-scope', 'scope');
    ready(model, 'audio-fft');
    const pending = model.acquire('audio-fft', 'pending');
    const pendingStart = one(model);
    expect(model.teardown()).toMatchObject([{ kind: 'stop', handle: 'scope' }]);
    expect(model.teardown()).toEqual([]);
    expect(model.takeOperations()).toEqual([]);
    expect(model.release(active)).toBe(false);
    expect(model.release(pending)).toBe(false);
    expect(() => model.acquire('rx-audio', 'late')).toThrow('torn down');
    expect(model.completeStart(pendingStart, { handle: 'late' })).toBe(false);
    expect(one(model)).toMatchObject({ kind: 'dispose', handle: 'late' });
  });
  it.each([false, true])(
    'harvests an undrained stop once (replacement demand: %s)',
    (replacement) => {
      const model = new ResourceDemand<string>('session-1');
      const oldLease = activate(model, 'rx-audio', 'old');
      model.release(oldLease);
      const replacementLease = replacement ? model.acquire('rx-audio', 'new') : undefined;
      expect(model.teardown()).toMatchObject([{ kind: 'stop', handle: 'old' }]);
      if (replacementLease) expect(model.release(replacementLease)).toBe(false);
      expect(model.takeOperations()).toEqual([]);
      expect(model.teardown()).toEqual([]);
    },
  );
  it.each([false, true])(
    'prefers one stop when dispose aliases active or queued stop work (%s)',
    (releaseBeforeTeardown) => {
      const model = new ResourceDemand<string>('session-1');
      ready(model, 'hardware-scope');
      const stale = [
        supersede(model, 'hardware-scope', 'stale-a'),
        supersede(model, 'hardware-scope', 'stale-b'),
      ];
      const activeLease = model.acquire('hardware-scope', 'current');
      const current = one(model);
      for (const operation of stale) model.completeStart(operation, { handle: 'stable' });
      model.completeStart(current, { handle: 'stable' });
      if (releaseBeforeTeardown) model.release(activeLease);
      expect(model.teardown()).toMatchObject([{ kind: 'stop', handle: 'stable' }]);
      expect(model.takeOperations()).toEqual([]);
    },
  );
  it('preserves distinct stale disposal and active stop intents', () => {
    const model = new ResourceDemand<string>('session-1');
    const { stale, current } = overlap(model, 'hardware-scope');
    model.completeStart(stale, { handle: 'old' });
    model.completeStart(current, { handle: 'new' });
    expect(model.teardown()).toMatchObject([
      { kind: 'dispose', handle: 'old' },
      { kind: 'stop', handle: 'new' },
    ]);
    expect(model.takeOperations()).toEqual([]);
  });
});
