import { describe, expect, it } from 'vitest';
import { ResourceDemand } from '../resource-demand';
type Resource = 'hardware-scope' | 'audio-fft' | 'rx-audio'; const ready = (m: ResourceDemand<string>, r: Resource) => m.configure(r, { selected: true, available: true });
const one = (m: ResourceDemand<string>) => {
  const ops = m.takeOperations(); expect(ops).toHaveLength(1);
  return ops[0];
};
describe('ResourceDemand', () => {
  it('uses exact unique leases and emits only first start and last stop', () => {
    const m = new ResourceDemand<string>('s1');
    ready(m, 'hardware-scope');
    const a = m.acquire('hardware-scope', 'old');
    const start = one(m);
    const b = m.acquire('hardware-scope', 'new');
    expect(b.leaseId).not.toBe(a.leaseId);
    expect(m.takeOperations()).toEqual([]);
    expect(m.completeStart(start, { handle: 'scope' })).toBe(true);
    expect(m.release(a)).toBe(true);
    expect(m.takeOperations()).toEqual([]);
    expect(m.release({ ...b } as typeof b)).toBe(false);
    expect(m.release(b)).toBe(true);
    expect(one(m)).toMatchObject({ kind: 'stop', handle: 'scope' });
    expect(m.release(b)).toBe(false); expect(new ResourceDemand<string>('s2').release(b)).toBe(false);
  });
  it('keeps unavailable and failed selection honest until explicit retry', () => {
    const m = new ResourceDemand<string>('s1');
    m.configure('hardware-scope', { selected: true, available: false });
    m.acquire('hardware-scope', 'scope');
    expect(m.takeOperations()).toEqual([]);
    ready(m, 'hardware-scope');
    m.completeStart(one(m), { error: 'offline' });
    expect(m.snapshot('hardware-scope')).toMatchObject(
      { selected: true, available: true, health: 'failed' },
    );
    expect(m.snapshot('audio-fft').demand).toBe(0);
    ready(m, 'hardware-scope');
    expect(m.takeOperations()).toEqual([]);
    m.retry('hardware-scope');
    expect(one(m).kind).toBe('start');
  });
  it('disposes a pending start whose demand vanished', () => {
    const m = new ResourceDemand<string>('s1');
    ready(m, 'audio-fft');
    const lease = m.acquire('audio-fft', 'panel');
    const start = one(m);
    m.release(lease);
    expect(m.completeStart(start, { handle: 'late' })).toBe(false);
    expect(one(m)).toMatchObject({ kind: 'dispose', handle: 'late' });
    expect(m.snapshot('audio-fft').activeHandle).toBeUndefined();
  });
  it('rejects stale stop completion across A→B→A', () => {
    const m = new ResourceDemand<string>('s1');
    ready(m, 'rx-audio');
    const a = m.acquire('rx-audio', 'A');
    m.completeStart(one(m), { handle: 'A' });
    m.release(a);
    const oldStop = one(m);
    m.acquire('rx-audio', 'B');
    m.completeStart(one(m), { handle: 'B' });
    expect(m.completeStop(oldStop)).toBe(false);
    expect(m.snapshot('rx-audio')).toMatchObject({ activeHandle: 'B', health: 'streaming' });
  });
  it('tears down each active handle once and invalidates all old work', () => {
    const m = new ResourceDemand<string>('s1');
    for (const r of ['hardware-scope', 'rx-audio'] as const) {
      ready(m, r); m.acquire(r, r); m.completeStart(one(m), { handle: r });
    }
    ready(m, 'audio-fft');
    const pendingLease = m.acquire('audio-fft', 'pending');
    const pendingStart = one(m);
    expect(m.teardown()).toMatchObject([{ handle: 'hardware-scope' }, { handle: 'rx-audio' }]);
    expect(m.teardown()).toEqual([]);
    expect(m.release(pendingLease)).toBe(false);
    expect(() => m.acquire('rx-audio', 'late')).toThrow('torn down');
    expect(m.completeStart(pendingStart, { handle: 'late' })).toBe(false);
    expect(one(m)).toMatchObject({ kind: 'dispose', handle: 'late' });
  });
});
