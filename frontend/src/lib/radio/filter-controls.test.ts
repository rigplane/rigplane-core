import { describe, expect, it } from 'vitest';

import {
  nbDepthDisplayToRaw,
  nbDepthRawToDisplay,
  nrDisplayToRaw,
  nrRawToDisplay,
} from './filter-controls';

// MOR-490: NR-level slider is 0-15 (front-panel scale), wire is 0-255 BCD.
// With no capabilities loaded these helpers use the IC-7610 fallback range
// (raw 0..255 <-> display 0..15), which is the path exercised in tests.

describe('nrDisplayToRaw (fallback range)', () => {
  it('maps the full-scale slider value to the full-scale wire value', () => {
    expect(nrDisplayToRaw(15)).toBe(255);
  });

  it('maps zero to zero', () => {
    expect(nrDisplayToRaw(0)).toBe(0);
  });

  it('maps the midpoint slider value to the midpoint wire value', () => {
    // round(8 * 255 / 15) = round(136) = 136
    expect(nrDisplayToRaw(8)).toBe(136);
  });

  it('clamps out-of-range display values to the wire range', () => {
    expect(nrDisplayToRaw(-5)).toBe(0);
    expect(nrDisplayToRaw(99)).toBe(255);
  });
});

describe('nrRawToDisplay (fallback range)', () => {
  it('maps the full-scale wire value to the full-scale slider value', () => {
    expect(nrRawToDisplay(255)).toBe(15);
  });

  it('maps zero to zero', () => {
    expect(nrRawToDisplay(0)).toBe(0);
  });

  it('maps the midpoint wire value to the midpoint slider value', () => {
    // round(128 * 15 / 255) = round(7.53) = 8
    expect(nrRawToDisplay(128)).toBe(8);
  });

  it('clamps out-of-range wire values to the slider range', () => {
    expect(nrRawToDisplay(-1)).toBe(0);
    expect(nrRawToDisplay(999)).toBe(15);
  });
});

describe('NR display <-> raw round-trip', () => {
  it('round-trips the slider endpoints exactly', () => {
    expect(nrRawToDisplay(nrDisplayToRaw(0))).toBe(0);
    expect(nrRawToDisplay(nrDisplayToRaw(15))).toBe(15);
  });
});

// MOR-498: NB-depth slider is 1-10 (front-panel scale), wire is 0-9.
// With no capabilities loaded these helpers use the IC-7610 fallback range
// (raw 0..9 <-> display 1..10): a simple +1/-1 offset.

describe('nbDepthDisplayToRaw (fallback range)', () => {
  it('maps display 1 to wire 0', () => {
    expect(nbDepthDisplayToRaw(1)).toBe(0);
  });

  it('maps display 6 to wire 5', () => {
    expect(nbDepthDisplayToRaw(6)).toBe(5);
  });

  it('maps display 10 to wire 9', () => {
    expect(nbDepthDisplayToRaw(10)).toBe(9);
  });

  it('clamps out-of-range display values to the wire range', () => {
    expect(nbDepthDisplayToRaw(-5)).toBe(0);
    expect(nbDepthDisplayToRaw(99)).toBe(9);
  });
});

describe('nbDepthRawToDisplay (fallback range)', () => {
  it('maps wire 0 to display 1', () => {
    expect(nbDepthRawToDisplay(0)).toBe(1);
  });

  it('maps wire 5 to display 6', () => {
    expect(nbDepthRawToDisplay(5)).toBe(6);
  });

  it('maps wire 9 to display 10', () => {
    expect(nbDepthRawToDisplay(9)).toBe(10);
  });

  it('clamps out-of-range wire values to the slider range', () => {
    expect(nbDepthRawToDisplay(-1)).toBe(1);
    expect(nbDepthRawToDisplay(999)).toBe(10);
  });
});

describe('NB-depth display <-> raw round-trip', () => {
  it('round-trips the slider endpoints exactly', () => {
    expect(nbDepthRawToDisplay(nbDepthDisplayToRaw(1))).toBe(1);
    expect(nbDepthRawToDisplay(nbDepthDisplayToRaw(10))).toBe(10);
  });
});
