import { describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { ScopeStream } from '../scope-stream';
import { scopeSubscription } from '../scope-view';

function stream() {
  const publish = vi.fn();
  const value = new ScopeStream({ subscribe: () => () => {}, close: () => {} },
    { source: 'radio', sourceId: 'radio', epoch: 1, mode: 3 }, 'borrowed', undefined, publish);
  return { value, publish };
}
const request = () => scopeSubscription({ sourceId: 'radio', receiver: 0,
  view: { follow: 'vfo', spanHz: 100_000 }, width: 2800, visible: true, maxFps: 20 });

describe('ScopeStream subscription negotiation', () => {
  it('publishes changed source, receiver, view, width and visible fps without duplicate requests', () => {
    const { value, publish } = stream();
    value.setSubscription(request()); value.setSubscription(request());
    expect(publish).toHaveBeenCalledTimes(1);
    const updates = [
      { ...request(), width: 1024 }, { ...request(), maxFps: 0 },
      { ...request(), view: { startHz: 14_000_000, endHz: 14_100_000 } },
      { ...request(), sourceId: 'iq_fft', receiver: null },
    ];
    for (const update of updates) value.setSubscription(update);
    expect(publish).toHaveBeenCalledTimes(5);
    expect(publish.mock.lastCall?.[0]).toEqual(updates.at(-1));
  });
  it('publishes actual granted width/fps/view/coverage without claiming pixels', () => {
    const { value } = stream(); value.setSubscription(request());
    const granted = { type: 'granted', sourceId: 'radio', receiver: 0,
      view: { startHz: 14_000_000, endHz: 14_100_000 },
      coverage: { startHz: 14_020_000, endHz: 14_080_000 }, width: 475, fps: 10 };
    value.handleMessage(granted);
    expect(get(value.granted)).toEqual(granted);
    expect(get(value.geometry)).toMatchObject({ mode: 3, width: 475, view: granted.view,
      coverage: granted.coverage, hasPixels: false, freshness: 'stale' });
    value.handleMessage({ type: 'error', message: 'source unavailable' });
    expect(get(value.error)).toBe('source unavailable');
    expect(get(value.geometry)).toBeNull();
    expect(get(value.granted)).toBeNull();
  });
  it('rejects malformed or stale-source grants and clears negotiated state on session retirement', () => {
    const { value } = stream(); value.setSubscription(request());
    const granted = { type: 'granted', sourceId: 'radio', receiver: 0,
      view: { startHz: 14_000_000, endHz: 14_100_000 }, coverage: null, width: 475, fps: 10 };
    value.handleMessage({ ...granted, width: NaN });
    value.handleMessage({ ...granted, sourceId: 'audio_fft' });
    expect(get(value.granted)).toBeNull();
    value.handleMessage(granted);
    expect(get(value.geometry)?.coverage).toEqual({ startHz: 14_000_000, endHz: 14_000_000 });
    value.setSessionEpoch(2);
    expect(get(value.granted)).toBeNull(); expect(get(value.geometry)).toBeNull();
  });
});
