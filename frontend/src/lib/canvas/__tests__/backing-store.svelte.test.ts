import { describe, expect, it, vi } from 'vitest';
import { canvasBackingSize, watchDevicePixelRatio } from '../backing-store.svelte';

describe('canvasBackingSize', () => {
  it.each([
    [1, 1, 320, 160],
    [1, 1.5, 480, 240],
    [1, 2, 640, 320],
    [1, 3, 960, 480],
    [2, 1, 640, 320],
    [2, 2, 1280, 640],
    [1.5, 1.5, 720, 360],
  ])('css 320x160 at dpr %s and stage scale %s is %sx%s device pixels', (dpr, stageScale, width, height) => {
    expect(canvasBackingSize(320, 160, dpr, stageScale)).toEqual({ width, height, pixelScale: dpr * stageScale });
  });

  it('floors a missing scale at 1 and rounds a fractional product', () => {
    expect(canvasBackingSize(10, 8, 0, Number.NaN)).toEqual({ width: 10, height: 8, pixelScale: 1 });
    expect(canvasBackingSize(100, 50, 1.25, 1.5)).toEqual({ width: 188, height: 94, pixelScale: 1.875 });
  });
});

describe('watchDevicePixelRatio', () => {
  it('fires on a resolution change and drops the listener on stop', () => {
    const listeners = new Map<string, () => void>();
    vi.stubGlobal('matchMedia', (query: string) => ({
      addEventListener: (_type: string, listener: () => void) => { listeners.set(query, listener); },
      removeEventListener: (_type: string, listener: () => void) => {
        if (listeners.get(query) === listener) listeners.delete(query);
      },
    }));
    const onChange = vi.fn();
    const stop = watchDevicePixelRatio(onChange);
    listeners.get('(resolution: 1dppx)')?.();
    expect(onChange).toHaveBeenCalledTimes(1);
    stop();
    expect(listeners.size).toBe(0);
    vi.unstubAllGlobals();
  });
});
