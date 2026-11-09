import { afterEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { flushSync } from 'svelte';
import { CanvasSurface, type CanvasSurfaceFacts } from '../../canvas/canvas-surface.svelte';
import { ScopeStream } from '../scope-stream';
import { ingressScopeFrame, scopeSlotKey } from '../adapters/scope-adapter';
import { scopeWire } from './scope-wire-fixture';
import { scopeSubscription, scopeSubscriptionSurface } from '../scope-view';

function stream() {
  const publish = vi.fn();
  const value = new ScopeStream({ subscribe: () => () => {}, close: () => {} },
    { source: 'radio', sourceId: 'radio', epoch: 1, mode: 3 }, 'borrowed', undefined, publish);
  return { value, publish };
}
const request = () => scopeSubscription({ sourceId: 'radio', receiver: 0,
  view: { follow: 'vfo', spanHz: 100_000 }, width: 2800, visible: true, maxFps: 20 });

const surfaceFacts = (backingW = 100): CanvasSurfaceFacts => ({
  cssW: backingW, cssH: 50, backingW, backingH: 50, pixelScale: 1, visible: true,
});

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
  it.each([true, false])('preserves an already-qualified frame only for a matching first subscription (%s)', matching => {
    const { value, publish } = stream();
    const buffer = scopeWire({ sourceId: 'radio', receiver: 0 });
    const slot = scopeSlotKey('radio', 0);
    value.ingress(ingressScopeFrame(buffer, { source: 'radio', sourceId: 'radio', epoch: 1, mode: 3 }));
    const geometry = get(value.geometry);
    const frame = value.latest(slot);
    expect(geometry).toMatchObject({ sourceId: 'radio', receiver: 0, hasPixels: true, freshness: 'fresh' });
    expect(frame?.pixels?.buffer).toBe(buffer);

    const subscription = { ...request(), receiver: matching ? 0 : 1 };
    value.updateSurfaceSubscription(Symbol('first surface'), subscription);
    expect(publish).toHaveBeenCalledExactlyOnceWith(subscription, null);
    expect(get(value.geometry)).toBe(matching ? geometry : null);
    expect(value.latest(slot)).toBe(matching ? frame : null);
    expect(value.selectedSlot).toBe(matching ? slot : null);
    value.dispose();
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
  let resize = (_entries: { contentRect: { width: number; height: number } }[]) => {};
  let intersect = (_entries: { isIntersecting: boolean }[]) => {};
  vi.stubGlobal('ResizeObserver', class { constructor(callback: typeof resize) { resize = callback; } observe() {} disconnect() {} });
  vi.stubGlobal('IntersectionObserver', class { constructor(callback: typeof intersect) { intersect = callback; } observe() {} disconnect() {} });
  const node = document.createElement('canvas'); document.body.append(node);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ setTransform: vi.fn() } as unknown as RenderingContext);
  vi.spyOn(window, 'devicePixelRatio', 'get').mockReturnValue(2);
  let hidden = false;
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
  let surface!: CanvasSurface;
  const destroyRoot = $effect.root(() => { surface = new CanvasSurface({ stageScale: () => 2 }); });
  const detach = surface.attach(node);
  const params = { stream: value, receiver: 0, view: request().view };
  const action = scopeSubscriptionSurface(node, { ...params, facts: surface.facts });
  const publishFacts = () => action.update({ ...params, facts: surface.facts });
  const stopSize = surface.onSizeChanged(publishFacts);
  const stopVisibility = surface.onVisibilityChanged(publishFacts);
  try {
    flushSync();
    resize([{ contentRect: { width: 100, height: 50 } }]); intersect([{ isIntersecting: true }]);
    expect(publish.mock.lastCall?.[0]).toMatchObject({ width: 400, maxFps: 20 });
    hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    expect(publish.mock.lastCall?.[0]).toMatchObject({ width: 400, maxFps: 0 });
    hidden = false; document.dispatchEvent(new Event('visibilitychange'));
    expect(publish.mock.lastCall?.[0]).toMatchObject({ maxFps: 20 });
    intersect([{ isIntersecting: false }]);
    expect(publish.mock.lastCall?.[0]).toMatchObject({ maxFps: 0 });
  } finally {
    stopSize(); stopVisibility(); action.destroy(); detach(); destroyRoot(); node.remove();
    vi.unstubAllGlobals(); vi.restoreAllMocks();
  }
});

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each(['error-first', 'facts-first'])('retries refused startup once for changed facts in %s order', (order) => {
  const { value, publish } = stream();
  const node = document.createElement('div'); document.body.append(node);
  const params = { stream: value, receiver: 0, view: request().view, facts: surfaceFacts(), revision: 'unknown' };
  const action = scopeSubscriptionSurface(node, params);
  const before = publish.mock.calls.length;
  const error = () => value.handleMessage({ type: 'error', message: 'audio mapping unavailable' });
  if (order === 'error-first') error();
  action.update({ ...params, revision: 'observed' });
  if (order === 'facts-first') error();
  expect(publish).toHaveBeenCalledTimes(before + 1);
  error(); // same observed facts cannot form an unbounded error/retry loop
  expect(publish).toHaveBeenCalledTimes(before + 1);
  action.update({ ...params, revision: 'observed' });
  expect(publish).toHaveBeenCalledTimes(before + 1);
  action.destroy(); node.remove();
});

it('shares the attempted facts revision across surfaces of the same upstream target', () => {
  const { value, publish } = stream();
  const nodes = [document.createElement('div'), document.createElement('div')];
  const params = { stream: value, receiver: 0, view: request().view, facts: surfaceFacts(), revision: 'unknown' };
  const parameters = nodes.map((_, index) => ({ ...params, facts: surfaceFacts(100 + index * 100) }));
  const actions = nodes.map((node, index) => scopeSubscriptionSurface(node, parameters[index]));
  const before = publish.mock.calls.length;
  actions.forEach((action, index) => action.update({ ...parameters[index], revision: 'observed' }));
  value.handleMessage({ type: 'error', message: 'old request rejected' });
  expect(publish).toHaveBeenCalledTimes(before + 1);
  value.handleMessage({ type: 'error', message: 'same facts still rejected' });
  expect(publish).toHaveBeenCalledTimes(before + 1);
  for (const action of actions) action.destroy();
});
