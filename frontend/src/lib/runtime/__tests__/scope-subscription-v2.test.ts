import { describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { ScopeStream } from '../scope-stream';
import { scopeSubscription, scopeSubscriptionSurface } from '../scope-view';

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
    expect(publish).toHaveBeenCalledTimes(6);
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

it('retains selected pixels while changing width/view, rejects hidden races, and retires only removed receiver keys', () => {
  const { value, publish } = stream();
  const main = Symbol('main'), sub = Symbol('sub'), duplicate = Symbol('duplicate');
  value.updateSurfaceSubscription(main, request());
  value.updateSurfaceSubscription(duplicate, { ...request(), width: 1024, maxFps: 10 });
  value.updateSurfaceSubscription(sub, { ...request(), receiver: 1, width: 689 });
  expect(value.subscriptions).toHaveLength(2);
  expect(value.subscriptions.find(request => request.receiver === 0)).toMatchObject({ width: 2800, maxFps: 20 });
  value.removeSurfaceSubscription(main);
  expect(value.subscriptions.find(request => request.receiver === 0)).toMatchObject({ width: 1024, maxFps: 10 });
  expect(value.subscriptions.find(request => request.receiver === 1)).toMatchObject({ width: 689, maxFps: 20 });
  value.removeSurfaceSubscription(duplicate);
  expect(publish).toHaveBeenCalledWith(null, expect.objectContaining({ sourceId: 'radio', receiver: 0 }));
  expect(value.subscriptions).toEqual([expect.objectContaining({ receiver: 1 })]);
  value.removeSurfaceSubscription(sub);
  const geometry = get(value.geometry);
  value.handleMessage({ type: 'granted', sourceId: 'radio', receiver: 1,
    view: { startHz: 1, endHz: 2 }, coverage: null, width: 689, fps: 20 });
  expect(get(value.geometry)).toBe(geometry);
});

it('retries a refused startup follow subscription after observed facts change', () => {
  const { value, publish } = stream();
  const token = Symbol('surface');
  value.updateSurfaceSubscription(token, request());
  value.handleMessage({ type: 'error', sourceId: 'radio', receiver: 0, message: 'frequency unavailable' });
  expect(get(value.error)).toBe('frequency unavailable');
  value.updateSurfaceSubscription(token, request());
  expect(publish).toHaveBeenCalledTimes(2);
  expect(publish.mock.lastCall?.[0].view).toEqual({ follow: 'vfo', spanHz: 100000 });
});


it('observes native backing width, intersection and document visibility for demand only', () => {
  const { value, publish } = stream();
  let resize = (_entries: { contentRect: { width: number } }[]) => {};
  let intersect = (_entries: { isIntersecting: boolean }[]) => {};
  vi.stubGlobal('ResizeObserver', class { constructor(callback: typeof resize) { resize = callback; } observe() {} disconnect() {} });
  vi.stubGlobal('IntersectionObserver', class { constructor(callback: typeof intersect) { intersect = callback; } observe() {} disconnect() {} });
  const node = document.createElement('div'); document.body.append(node);
  Object.defineProperty(node, 'clientWidth', { value: 100 });
  Object.defineProperty(node, 'offsetWidth', { value: 100 });
  vi.spyOn(node, 'getBoundingClientRect').mockReturnValue({ width: 200 } as DOMRect);
  vi.spyOn(window, 'devicePixelRatio', 'get').mockReturnValue(2);
  let hidden = false;
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
  const action = scopeSubscriptionSurface(node, { stream: value, receiver: 0, view: request().view });
  try {
    resize([{ contentRect: { width: 100 } }]); intersect([{ isIntersecting: true }]);
    expect(publish.mock.lastCall?.[0]).toMatchObject({ width: 400, maxFps: 20 });
    hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    expect(publish.mock.lastCall?.[0]).toMatchObject({ width: 400, maxFps: 0 });
    hidden = false; document.dispatchEvent(new Event('visibilitychange'));
    expect(publish.mock.lastCall?.[0]).toMatchObject({ maxFps: 20 });
    intersect([{ isIntersecting: false }]);
    expect(publish.mock.lastCall?.[0]).toMatchObject({ maxFps: 0 });
  } finally {
    action.destroy(); node.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks();
  }
});
