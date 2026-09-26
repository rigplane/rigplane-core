import { describe, expect, it, vi } from 'vitest';
import { canvasBackingSize, readAncestorScale, watchDevicePixelRatio } from '../backing-store';

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

function box(element: HTMLElement, layout: number, painted: number): void {
  Object.defineProperty(element, 'offsetWidth', { configurable: true, value: layout });
  Object.defineProperty(element, 'offsetHeight', { configurable: true, value: layout });
  element.getBoundingClientRect = () => ({ width: painted, height: painted, left: 0, top: 0, right: painted, bottom: painted, x: 0, y: 0, toJSON() { return this; } }) as DOMRect;
}

describe('readAncestorScale', () => {
  it.each([
    [200, 200, 1],
    [200, 400, 2],
    [200, 300, 1.5],
    [0, 0, 1],
  ])('layout %s painted %s reads scale %s', (layout, painted, scale) => {
    const canvas = document.createElement('canvas');
    box(canvas, layout, painted);
    expect(readAncestorScale(canvas)).toBe(scale);
  });

  it('ignores a non-uniform stretch', () => {
    const canvas = document.createElement('canvas');
    Object.defineProperty(canvas, 'offsetWidth', { configurable: true, value: 200 });
    Object.defineProperty(canvas, 'offsetHeight', { configurable: true, value: 200 });
    canvas.getBoundingClientRect = () => ({ width: 400, height: 600, left: 0, top: 0, right: 400, bottom: 600, x: 0, y: 0, toJSON() { return this; } }) as DOMRect;
    expect(readAncestorScale(canvas)).toBe(1);
  });
});

describe('watchDevicePixelRatio', () => {
  it('fires on a resolution change and resubscribes at the new ratio', () => {
    const listeners = new Map<string, () => void>();
    vi.stubGlobal('matchMedia', (query: string) => ({
      addEventListener: (_type: string, listener: () => void) => { listeners.set(query, listener); },
      removeEventListener: (_type: string, listener: () => void) => {
        if (listeners.get(query) === listener) listeners.delete(query);
      },
    }));
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 1, writable: true });
    const onChange = vi.fn();
    const stop = watchDevicePixelRatio(onChange);
    listeners.get('(resolution: 1dppx)')?.();
    expect(onChange).toHaveBeenCalledTimes(1);
    window.devicePixelRatio = 2;
    listeners.get('(resolution: 2dppx)')?.();
    expect(onChange).toHaveBeenCalledTimes(2);
    stop();
    expect(listeners.size).toBe(0);
    vi.unstubAllGlobals();
  });
});
