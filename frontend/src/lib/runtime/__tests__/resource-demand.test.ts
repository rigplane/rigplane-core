import { describe, expect, it } from 'vitest';
import { ResourceDemand, type AppResource } from '../resource-demand';
const ready = (model: ResourceDemand<string>, resource: AppResource) => {
  model.configure(resource, { available: true, selected: true });
};
const one = (model: ResourceDemand<string>) => {
  const operations = model.takeOperations();
  expect(operations).toHaveLength(1);
  return operations[0];
};
describe('ResourceDemand foundation', () => {
  it('uses exact leases and emits only first start and last stop', () => {
    const model = new ResourceDemand<string>('session-1');
    ready(model, 'hardware-scope');
    const first = model.acquire('hardware-scope', 'first');
    const start = one(model);
    const second = model.acquire('hardware-scope', 'second');
    expect(second).not.toBe(first);
    expect(model.takeOperations()).toEqual([]);
    expect(model.completeStart(start, { handle: 'scope' })).toBe(true);
    expect(model.release(first)).toBe(true);
    expect(model.takeOperations()).toEqual([]);
    expect(model.release({ ...second } as typeof second)).toBe(false);
    expect(new ResourceDemand<string>('session-2').release(second)).toBe(false);
    expect(model.release(second)).toBe(true);
    expect(one(model)).toMatchObject({ kind: 'stop', handle: 'scope' });
  });
  it('keeps unavailable resources idle and copies only public configuration', () => {
    const model = new ResourceDemand<string>('session-1');
    const widerConfig = { available: false, selected: true, demand: 99, activeHandle: 'ghost' };
    model.configure('audio-fft', widerConfig);
    model.acquire('audio-fft', 'panel');
    expect(model.takeOperations()).toEqual([]);
    expect(model.snapshot('audio-fft')).toMatchObject({
      available: false, selected: true, demand: 1, health: 'inactive',
    });
    expect(model.snapshot('audio-fft').activeHandle).toBeUndefined();
    ready(model, 'audio-fft');
    expect(one(model).kind).toBe('start');
  });
  it('ignores fake and duplicate starts and protects a stable aliased handle', () => {
    const model = new ResourceDemand<string>('session-1');
    ready(model, 'hardware-scope');
    const first = model.acquire('hardware-scope', 'A');
    const startA = one(model);
    expect(model.completeStart({ ...startA }, { handle: 'fake' })).toBe(false);
    model.release(first);
    const second = model.acquire('hardware-scope', 'B');
    const startB = one(model);
    expect(model.completeStart(startA, { handle: 'stable' })).toBe(false);
    expect(model.takeOperations()).toEqual([]);
    expect(model.completeStart(startB, { handle: 'stable' })).toBe(true);
    expect(model.completeStart(startB, { handle: 'stable' })).toBe(false);
    expect(model.takeOperations()).toEqual([]);
    expect(model.snapshot('hardware-scope')).toMatchObject({ activeHandle: 'stable', health: 'streaming' });
    model.release(second);
    expect(one(model)).toMatchObject({ kind: 'stop', handle: 'stable' });
  });
  it('rejects a stale stop when replacement demand overlaps it', () => {
    const model = new ResourceDemand<string>('session-1');
    ready(model, 'rx-audio');
    const first = model.acquire('rx-audio', 'A');
    model.completeStart(one(model), { handle: 'A' });
    model.release(first);
    const oldStop = one(model);
    model.acquire('rx-audio', 'B');
    model.completeStart(one(model), { handle: 'B' });
    expect(model.completeStop(oldStop)).toBe(false);
    expect(model.snapshot('rx-audio')).toMatchObject({ activeHandle: 'B', health: 'streaming' });
  });
});
