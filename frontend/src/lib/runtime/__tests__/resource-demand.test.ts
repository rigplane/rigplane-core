import { describe, expect, it } from 'vitest';
import { ResourceDemand } from '../resource-demand';
const one = (model: ResourceDemand<string>) => {
  const operations = model.takeOperations();
  expect(operations).toHaveLength(1);
  return operations[0];
};
const cleanupOrderings = ['stop-before-stale', 'stale-before-stop'] as const;
describe('ResourceDemand foundation', () => {
  it('uses exact leases and emits only first start and last stop', () => {
    const model = new ResourceDemand<string>('session-1');
    model.configure('hardware-scope', { available: true, selected: true });
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
    expect(model.snapshot('audio-fft')).toEqual({
      available: false, selected: true, demand: 1, health: 'inactive',
    });
    model.configure('audio-fft', { available: true, selected: true });
    expect(one(model).kind).toBe('start');
  });
  it.each(cleanupOrderings)('dedupes stable-handle cleanup for %s ordering', (ordering) => {
    const model = new ResourceDemand<string>('session-1');
    model.configure('hardware-scope', { available: true, selected: true });
    const first = model.acquire('hardware-scope', 'A');
    const startA = one(model);
    expect(model.completeStart({ ...startA }, { handle: 'fake' })).toBe(false);
    model.release(first);
    const second = model.acquire('hardware-scope', 'B');
    const startB = one(model);
    if (ordering === 'stop-before-stale') {
      expect(model.completeStart(startB, { handle: 'stable' })).toBe(true);
      expect(model.completeStart(startB, { handle: 'stable' })).toBe(false);
      model.release(second);
      expect(model.completeStart(startA, { handle: 'stable' })).toBe(false);
    } else {
      expect(model.completeStart(startA, { handle: 'stable' })).toBe(false);
      expect(model.completeStart(startB, { handle: 'stable' })).toBe(true);
      model.release(second);
    }
    expect(one(model)).toMatchObject({ kind: 'stop', handle: 'stable' });
  });
  it('rejects a stale stop when replacement demand overlaps it', () => {
    const model = new ResourceDemand<string>('session-1');
    model.configure('rx-audio', { available: true, selected: true });
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
