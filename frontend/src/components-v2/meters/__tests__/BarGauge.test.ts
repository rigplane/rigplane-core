import { describe, it, expect } from 'vitest';
import {
  DEFAULT_ZONES,
  valueToSegments,
  getSegmentZone,
  dimColor,
  valueFontSize,
} from '../bar-gauge-utils';
import type { Zone } from '../bar-gauge-utils';

// ── valueToSegments ──────────────────────────────────────────────────────────

describe('valueToSegments', () => {
  it('maps 0 to 0 segments', () => {
    expect(valueToSegments(0, 10)).toBe(0);
  });

  it('maps 1.0 to full segment count', () => {
    expect(valueToSegments(1.0, 10)).toBe(10);
  });

  it('maps 0.5 to half the segments', () => {
    expect(valueToSegments(0.5, 10)).toBe(5);
  });

  it('returns fractional segments for intermediate values', () => {
    expect(valueToSegments(0.25, 10)).toBe(2.5);
  });

  it('clamps values above 1.0 to segCount', () => {
    expect(valueToSegments(1.5, 10)).toBe(10);
  });

  it('clamps negative values to 0', () => {
    expect(valueToSegments(-0.3, 10)).toBe(0);
  });

  it('works with non-standard segment counts', () => {
    expect(valueToSegments(0.5, 20)).toBe(10);
  });

  it('returns a fractional value for 0.1', () => {
    expect(valueToSegments(0.1, 10)).toBeCloseTo(1.0);
  });
});

// ── getSegmentZone ───────────────────────────────────────────────────────────

describe('getSegmentZone with default zones', () => {
  // Segment midpoints: seg i → pos = (i + 0.5) / 10
  // Zones: green ≤0.6, yellow ≤0.8, red ≤1.0

  it('segment 0 (pos 0.05) → green zone', () => {
    const z = getSegmentZone(0, 10, DEFAULT_ZONES);
    expect(z.color).toBe('#14A665');
  });

  it('segment 5 (pos 0.55) → green zone', () => {
    const z = getSegmentZone(5, 10, DEFAULT_ZONES);
    expect(z.color).toBe('#14A665');
  });

  it('segment 6 (pos 0.65) → yellow zone', () => {
    const z = getSegmentZone(6, 10, DEFAULT_ZONES);
    expect(z.color).toBe('#F2CF4A');
  });

  it('segment 7 (pos 0.75) → yellow zone', () => {
    const z = getSegmentZone(7, 10, DEFAULT_ZONES);
    expect(z.color).toBe('#F2CF4A');
  });

  it('segment 8 (pos 0.85) → red zone', () => {
    const z = getSegmentZone(8, 10, DEFAULT_ZONES);
    expect(z.color).toBe('#F14C42');
  });

  it('segment 9 (pos 0.95) → red zone', () => {
    const z = getSegmentZone(9, 10, DEFAULT_ZONES);
    expect(z.color).toBe('#F14C42');
  });

  it('returns the last zone for a segment beyond all zone ends', () => {
    // All 10 segments of a single-zone config
    const singleZone: Zone[] = [{ end: 0.5, color: '#00FF00' }];
    // Segment 9 (pos 0.95) exceeds the only zone's end — should fall back to last
    const z = getSegmentZone(9, 10, singleZone);
    expect(z.color).toBe('#00FF00');
  });
});

describe('getSegmentZone with custom zones', () => {
  const twoZones: Zone[] = [
    { end: 0.5, color: '#0000FF' }, // blue
    { end: 1.0, color: '#FF0000' }, // red
  ];

  it('segment 4 (pos 0.45) → first zone with 2 custom zones', () => {
    const z = getSegmentZone(4, 10, twoZones);
    expect(z.color).toBe('#0000FF');
  });

  it('segment 5 (pos 0.55) → second zone with 2 custom zones', () => {
    const z = getSegmentZone(5, 10, twoZones);
    expect(z.color).toBe('#FF0000');
  });

  it('segment 9 (pos 0.95) → second zone with 2 custom zones', () => {
    const z = getSegmentZone(9, 10, twoZones);
    expect(z.color).toBe('#FF0000');
  });
});

// ── dimColor ─────────────────────────────────────────────────────────────────

describe('dimColor', () => {
  it('returns a 7-character hex string', () => {
    const result = dimColor('#14A665');
    expect(result).toMatch(/^#[0-9a-f]{6}$/);
  });

  it('returns a darker color than the input', () => {
    const input = '#14A665';
    const dimmed = dimColor(input);
    // Sum of channels should be lower in dimmed version
    const sumInput = parseInt(input.slice(1, 3), 16) + parseInt(input.slice(3, 5), 16) + parseInt(input.slice(5, 7), 16);
    const sumDimmed = parseInt(dimmed.slice(1, 3), 16) + parseInt(dimmed.slice(3, 5), 16) + parseInt(dimmed.slice(5, 7), 16);
    expect(sumDimmed).toBeLessThan(sumInput);
  });

  it('dimmed green still has a greenish hue (G channel dominant)', () => {
    const dimmed = dimColor('#14A665');
    const g = parseInt(dimmed.slice(3, 5), 16);
    const r = parseInt(dimmed.slice(1, 3), 16);
    expect(g).toBeGreaterThan(r);
  });

  it('dimmed red still has a reddish hue (R channel dominant)', () => {
    const dimmed = dimColor('#F14C42');
    const r = parseInt(dimmed.slice(1, 3), 16);
    const b = parseInt(dimmed.slice(5, 7), 16);
    expect(r).toBeGreaterThan(b);
  });

  it('custom blend=0 returns the background color', () => {
    // blend=0 → 100% background (#07090D)
    expect(dimColor('#FFFFFF', 0)).toBe('#07090d');
  });

  it('custom blend=1 returns the original color (lowercase)', () => {
    // blend=1 → 100% source color
    expect(dimColor('#14A665', 1)).toBe('#14a665');
  });

  it('default blend (0.12) produces a very dark result', () => {
    const dimmed = dimColor('#14A665');
    const r = parseInt(dimmed.slice(1, 3), 16);
    const g = parseInt(dimmed.slice(3, 5), 16);
    const b = parseInt(dimmed.slice(5, 7), 16);
    // All channels should be below 40 (very dark)
    expect(r).toBeLessThan(40);
    expect(g).toBeLessThan(40);
    expect(b).toBeLessThan(40);
  });
});

// ── DEFAULT_ZONES ────────────────────────────────────────────────────────────

describe('DEFAULT_ZONES', () => {
  it('has exactly 3 zones', () => {
    expect(DEFAULT_ZONES).toHaveLength(3);
  });

  it('zones are in ascending end order', () => {
    for (let i = 1; i < DEFAULT_ZONES.length; i++) {
      expect(DEFAULT_ZONES[i].end).toBeGreaterThan(DEFAULT_ZONES[i - 1].end);
    }
  });

  it('last zone ends at 1.0', () => {
    expect(DEFAULT_ZONES[DEFAULT_ZONES.length - 1].end).toBe(1.0);
  });
});

// ── valueFontSize (MOR-1535) ─────────────────────────────────────────────────
//
// `displayValue` renders as SVG text at x=260 in a 0-300 viewBox (a fixed
// ~40px column) — SVG text clips silently rather than wrapping or
// ellipsizing when it overflows. At the non-compact VALUE_FS=11 the budget
// is ~6 characters; MOR-1527's own "NNN raw" honesty tag is 7 characters
// and would clip on MeterPanel.svelte, BarGauge's sole non-compact consumer
// (dead code per MOR-1535's audit, but not deleted here — no hollowing
// without an owner call).

describe('valueFontSize', () => {
  it('keeps the base font size for text within the ~6-char budget', () => {
    expect(valueFontSize('35W', 11)).toBe(11);
    expect(valueFontSize('S9+40', 11)).toBe(11);
  });

  it('steps the font size down below the base for text past the budget', () => {
    const fs = valueFontSize('158 raw', 11);
    expect(fs).toBeLessThan(11);
  });

  it('never steps size up past the base font size', () => {
    expect(valueFontSize('1', 11)).toBeLessThanOrEqual(11);
  });

  it('never steps down below a legible floor even for very long text', () => {
    const fs = valueFontSize('a very long display value indeed', 11);
    expect(fs).toBeGreaterThanOrEqual(6);
  });

  it('compact base (9) also steps down once text exceeds its own budget', () => {
    const base = valueFontSize('35W', 9);
    const stepped = valueFontSize('a much longer value', 9);
    expect(stepped).toBeLessThan(base);
  });
});
