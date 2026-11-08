import { describe, expect, it, vi } from 'vitest';
import { createScopeView, panView, zoomView, viewCenterHz, viewSpanHz, scopeSubscription } from '../scope-view';

describe('scope view', () => {
  it('prioritizes gesture, burst, pending and confirmed and emits only effective moves', () => {
    const model = createScopeView({ startHz: 100, endHz: 200 });
    const listener = vi.fn();
    const unsubscribe = model.subscribe(listener);
    model.set('pending', { startHz: 110, endHz: 210 });
    model.set('tuningBurst', { startHz: 120, endHz: 220 });
    model.set('gesture', { startHz: 130, endHz: 230 });
    model.set('pending', { startHz: 111, endHz: 211 });
    model.set('gesture', { startHz: 130, endHz: 230 });
    expect(listener).toHaveBeenCalledTimes(4); // initial value plus three effective moves
    model.set('gesture', null);
    model.set('tuningBurst', null);
    model.set('pending', null);
    expect(listener.mock.calls.slice(-3).map(([v]) => v.startHz)).toEqual([120, 111, 100]);
    unsubscribe();
  });
  it('emits once per pointer move without frames', () => {
    const model = createScopeView({ startHz: 100, endHz: 200 });
    const listener = vi.fn();
    model.subscribe(listener);
    for (let i = 1; i <= 25; i++) model.set('gesture', { startHz: 100 + i, endHz: 200 + i });
    expect(listener).toHaveBeenCalledTimes(26);
  });
  it('derives center and span and pans and zooms independently of a source', () => {
    const view = { startHz: 100, endHz: 200 };
    expect(viewCenterHz(view)).toBe(150);
    expect(viewSpanHz(view)).toBe(100);
    expect(panView(view, 20)).toEqual({ startHz: 120, endHz: 220 });
    expect(zoomView(view, 0.5)).toEqual({ startHz: 125, endHz: 175 });
  });
  it('computes subscription inputs and pauses hidden or zero width surfaces', () => {
    const view = { startHz: 100, endHz: 200 };
    const input = { sourceId: 'radio', receiver: 0, view, width: 1024, visible: true, maxFps: 15 };
    expect(scopeSubscription(input)).toEqual({ sourceId: 'radio', receiver: 0, view, width: 1024, maxFps: 15 });
    expect(scopeSubscription({ ...input, visible: false }).maxFps).toBe(0);
    expect(scopeSubscription({ ...input, width: 0 }).maxFps).toBe(0);
    expect(scopeSubscription({ ...input, sourceId: 'iq', receiver: null, width: 700, maxFps: 10 })).toEqual({ sourceId: 'iq', receiver: null, view, width: 700, maxFps: 10 });
  });
});
