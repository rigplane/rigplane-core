import { describe, expect, it } from 'vitest';
import { ResourceDemand, type AppResource } from '../resource-demand';
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
const cleanupCases = [[false, ['rx-audio', 'rx-audio'], ['H', 'H'], ['rx-audio:H']],
  [true, ['rx-audio', 'rx-audio'], ['H', 'H'], ['rx-audio:H']],
  [false, ['rx-audio', 'rx-audio'], ['A', 'B'], ['rx-audio:A', 'rx-audio:B']],
  [false, ['rx-audio', 'hardware-scope'], ['H', 'H'], ['rx-audio:H', 'hardware-scope:H']]] as const;
describe('ResourceDemand foundation', () => {
  it('uses exact leases and remembers adopted handles across cleanup drains', () => {
    const model = new ResourceDemand<string>('session-1');
    const wider = { available: false, selected: true, demand: 99, activeHandle: 'ghost' };
    model.configure('audio-fft', wider);
    model.acquire('audio-fft', 'panel');
    expect(model.snapshot('audio-fft')).toEqual({ available: false, selected: true, demand: 1, health: 'inactive' });
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
    expect(model.takeOperations()).toEqual([]);
    const replacement = model.acquire('rx-audio', 'replacement');
    model.completeStart(one(model), { handle: 'next' });
    model.release(replacement);
    const staleStop = one(model);
    model.acquire('rx-audio', 'overlap');
    model.completeStart(one(model), { handle: 'latest' });
    expect(model.completeStop(staleStop)).toBe(false);
  });
  it.each(cleanupCases)('dedupes orphan cleanup %#', (drain, resources, handles, expected) => {
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
  it('defers singleton cleanup until pending adoption', () => {
    const model = new ResourceDemand<string>('session-1');
    model.configure('rx-audio', { available: true, selected: true });
    const staleStart = supersede(model, 'rx-audio', 'stale');
    const liveLease = model.acquire('rx-audio', 'live'), liveStart = one(model);
    expect(model.completeStart(staleStart, { handle: 'stable' })).toBe(false);
    expect([model.takeOperations(), model.takeOperations()]).toEqual([[], []]);
    expect(model.completeStart(liveStart, { handle: 'stable' })).toBe(true);
    model.release(liveLease); expect(model.takeOperations().map((op) => op.kind)).toEqual(['stop']);
  });
});
