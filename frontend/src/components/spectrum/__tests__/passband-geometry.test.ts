import { describe, expect, it } from 'vitest';

import {
  canResizeFromRightEdge,
  getFilterWidthFromRightEdgePx,
  getPassbandEdgesHz,
  getPassbandGeometry,
} from '../passband-geometry';

describe('getPassbandEdgesHz', () => {
  it('places USB passband above carrier', () => {
    expect(getPassbandEdgesHz('USB', 2400, 0)).toEqual({ leftHz: 0, rightHz: 2400 });
  });

  it('places LSB passband below carrier', () => {
    expect(getPassbandEdgesHz('LSB', 2400, 0)).toEqual({ leftHz: -2400, rightHz: 0 });
  });

  it('centers AM passband around carrier', () => {
    expect(getPassbandEdgesHz('AM', 3000, 0)).toEqual({ leftHz: -1500, rightHz: 1500 });
  });

  it('centers CW passband around carrier', () => {
    expect(getPassbandEdgesHz('CW', 500, 0)).toEqual({ leftHz: -250, rightHz: 250 });
  });

  it('centers CW-R passband around carrier', () => {
    expect(getPassbandEdgesHz('CW-R', 500, 0)).toEqual({ leftHz: -250, rightHz: 250 });
  });

  it('centers FM passband around carrier (#1719)', () => {
    expect(getPassbandEdgesHz('FM', 15000, 0)).toEqual({ leftHz: -7500, rightHz: 7500 });
  });

  it('centers narrow/wide FM variants around carrier (#1719)', () => {
    expect(getPassbandEdgesHz('FM-N', 10000, 0)).toEqual({ leftHz: -5000, rightHz: 5000 });
    expect(getPassbandEdgesHz('WFM', 200000, 0)).toEqual({ leftHz: -100000, rightHz: 100000 });
    expect(getPassbandEdgesHz('DATA-FM', 15000, 0)).toEqual({ leftHz: -7500, rightHz: 7500 });
    expect(getPassbandEdgesHz('DATA-FM-N', 10000, 0)).toEqual({ leftHz: -5000, rightHz: 5000 });
  });

  it('centers digital FM-based modes around carrier (#1719)', () => {
    expect(getPassbandEdgesHz('DV', 7000, 0)).toEqual({ leftHz: -3500, rightHz: 3500 });
    expect(getPassbandEdgesHz('C4FM-DN', 12500, 0)).toEqual({ leftHz: -6250, rightHz: 6250 });
    expect(getPassbandEdgesHz('C4FM-VW', 12500, 0)).toEqual({ leftHz: -6250, rightHz: 6250 });
  });

  it('centers narrow AM around carrier (#1719)', () => {
    expect(getPassbandEdgesHz('AM-N', 6000, 0)).toEqual({ leftHz: -3000, rightHz: 3000 });
  });

  it('applies IF shift to both passband edges', () => {
    expect(getPassbandEdgesHz('USB', 2400, 300)).toEqual({ leftHz: 300, rightHz: 2700 });
  });

  it('handles zero passband width', () => {
    expect(getPassbandEdgesHz('USB', 0, 0)).toEqual({ leftHz: 0, rightHz: 0 });
  });

  it('normalizes lowercase mode names', () => {
    expect(getPassbandEdgesHz('usb', 2400, 0)).toEqual({ leftHz: 0, rightHz: 2400 });
    expect(getPassbandEdgesHz('lsb', 2400, 0)).toEqual({ leftHz: -2400, rightHz: 0 });
  });
});

describe('getPassbandGeometry', () => {
  it('converts passband width into pixels', () => {
    expect(getPassbandGeometry('USB', 2400, 0, 12000, 600)).toEqual({
      leftPx: 300,
      rightPx: 420,
      widthPx: 120,
    });
  });

  it('moves the overlay right when IF shift is positive', () => {
    expect(getPassbandGeometry('USB', 2400, 300, 12000, 600)).toEqual({
      leftPx: 315,
      rightPx: 435,
      widthPx: 120,
    });
  });

  it('clamps geometry to the visible span', () => {
    expect(getPassbandGeometry('USB', 2400, 6000, 12000, 600)).toEqual({
      leftPx: 600,
      rightPx: 600,
      widthPx: 0,
    });
  });

  it('returns null for zero passband width', () => {
    expect(getPassbandGeometry('USB', 0, 0, 12000, 600)).toBeNull();
  });

  it('returns null for zero span', () => {
    expect(getPassbandGeometry('USB', 2400, 0, 0, 600)).toBeNull();
  });

  it('returns null for zero canvas width', () => {
    expect(getPassbandGeometry('USB', 2400, 0, 12000, 0)).toBeNull();
  });
});

describe('getPassbandGeometry with tunePx offset (#552)', () => {
  it('centers passband on tunePx when provided', () => {
    // USB 2400 Hz passband, 12 kHz span, 600px width, indicator at 240px (40%)
    const geo = getPassbandGeometry('USB', 2400, 0, 12000, 600, 240);
    expect(geo).not.toBeNull();
    // USB passband is to the right of carrier: left=carrier, right=carrier+2400
    // At 600px/12000Hz = 0.05 px/Hz, 2400Hz = 120px
    expect(geo!.leftPx).toBe(240);   // carrier position
    expect(geo!.rightPx).toBe(360);  // carrier + 120px
    expect(geo!.widthPx).toBe(120);
  });

  it('defaults to center when tunePx is omitted', () => {
    const geo = getPassbandGeometry('USB', 2400, 0, 12000, 600);
    expect(geo).not.toBeNull();
    expect(geo!.leftPx).toBe(300);   // center of 600px
    expect(geo!.rightPx).toBe(420);
  });

  it('passband follows carrier left of center in CTR+Filter mode', () => {
    // Carrier at 42% of scope (filter center offset), 100px width
    const tunePx = 42;
    const geo = getPassbandGeometry('USB', 2400, 0, 20000, 100, tunePx);
    expect(geo).not.toBeNull();
    expect(geo!.leftPx).toBe(42);    // carrier position
    expect(geo!.rightPx).toBe(54);   // 42 + 2400/20000*100 = 42 + 12
  });

  it('LSB passband extends left from carrier', () => {
    const geo = getPassbandGeometry('LSB', 2400, 0, 12000, 600, 360);
    expect(geo).not.toBeNull();
    // LSB: passband below carrier. left=carrier-2400Hz, right=carrier
    expect(geo!.leftPx).toBe(240);   // 360 - 120px
    expect(geo!.rightPx).toBe(360);  // carrier position
  });
});

describe('getFilterWidthFromRightEdgePx', () => {
  it('derives USB width from the dragged right edge', () => {
    expect(getFilterWidthFromRightEdgePx('USB', 0, 12000, 600, 420)).toBe(2400);
  });

  it('accounts for IF shift when resizing USB passband', () => {
    expect(getFilterWidthFromRightEdgePx('USB', 300, 12000, 600, 435)).toBe(2400);
  });

  it('derives symmetric AM width from the right edge', () => {
    expect(getFilterWidthFromRightEdgePx('AM', 0, 12000, 600, 375)).toBe(3000);
  });

  it('derives symmetric FM width from the right edge (#1719)', () => {
    expect(getFilterWidthFromRightEdgePx('FM', 0, 12000, 600, 375, 15000)).toBe(3000);
  });

  it('disables right-edge resize for LSB', () => {
    expect(canResizeFromRightEdge('LSB')).toBe(false);
    expect(getFilterWidthFromRightEdgePx('LSB', 0, 12000, 600, 300)).toBeNull();
  });
});