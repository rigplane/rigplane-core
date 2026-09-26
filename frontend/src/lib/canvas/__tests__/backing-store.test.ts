import { describe, expect, it } from 'vitest';
import { canvasBackingSize, readAncestorScale } from '../backing-store';

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

  it('floors a missing or non-positive scale at 1 so the store is never empty', () => {
    expect(canvasBackingSize(10, 8, 0, 0)).toEqual({ width: 10, height: 8, pixelScale: 1 });
    expect(canvasBackingSize(10, 8, Number.NaN, Number.NaN).width).toBe(10);
  });

  it('rounds a fractional product instead of truncating it', () => {
    expect(canvasBackingSize(100, 50, 1.25, 1.5)).toEqual({ width: 188, height: 94, pixelScale: 1.875 });
  });
});

describe('readAncestorScale', () => {
  it('returns 1 when nothing between the canvas and the root is scaled', () => {
    const canvas = document.createElement('canvas');
    document.body.appendChild(canvas);
    expect(readAncestorScale(canvas)).toBe(1);
    canvas.remove();
  });

  it('reads a uniform ancestor scale of 2 from the transform matrix', () => {
    const stage = document.createElement('div');
    stage.style.transform = 'scale(2)';
    const canvas = document.createElement('canvas');
    stage.appendChild(canvas);
    document.body.appendChild(stage);
    expect(readAncestorScale(canvas)).toBe(2);
    stage.remove();
  });

  it('reads a translate-then-scale(1.5) the way ScaledStage writes it', () => {
    const stage = document.createElement('div');
    stage.style.transform = 'translate(12px, 4px) scale(1.5)';
    const canvas = document.createElement('canvas');
    stage.appendChild(canvas);
    document.body.appendChild(stage);
    expect(readAncestorScale(canvas)).toBe(1.5);
    stage.remove();
  });

  it('ignores a non-uniform stretch and keeps only a uniform scale', () => {
    const stretched = document.createElement('div');
    stretched.style.transform = 'scale(2, 3)';
    const canvas = document.createElement('canvas');
    stretched.appendChild(canvas);
    document.body.appendChild(stretched);
    expect(readAncestorScale(canvas)).toBe(1);
    stretched.remove();
  });
});
