import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  rawToSegments,
  rawToSUnit,
  rawToDbm,
  formatDbm,
} from '../smeter-scale';

// ── rawToSegments ──────────────────────────────────────────────────────────

describe('rawToSegments', () => {
  it('maps S0 (raw 0) to 0 segments', () => {
    expect(rawToSegments(0)).toBe(0);
  });

  it('maps S1 (raw 18) to ~1.22 segments', () => {
    expect(rawToSegments(18)).toBeCloseTo((18 / 162) * 11, 5);
  });

  it('maps S9 (raw 162) to exactly 11 segments', () => {
    expect(rawToSegments(162)).toBe(11);
  });

  it('maps S9+20 (raw 202) to ~14.87 segments', () => {
    const expected = 11 + ((202 - 162) / (255 - 162)) * 9;
    expect(rawToSegments(202)).toBeCloseTo(expected, 5);
  });

  it('maps max (raw 255) to exactly 20 segments', () => {
    expect(rawToSegments(255)).toBe(20);
  });

  it('clamps values below 0', () => {
    expect(rawToSegments(-10)).toBe(0);
  });

  it('clamps values above 255', () => {
    expect(rawToSegments(300)).toBe(20);
  });

  it('returns fractional values for intermediate inputs', () => {
    const v = rawToSegments(81); // midway in S0-S9 zone
    expect(v).toBeGreaterThan(0);
    expect(v).toBeLessThan(11);
  });
});

// ── rawToSUnit ─────────────────────────────────────────────────────────────

describe('rawToSUnit', () => {
  it('returns S0 for raw 0', () => {
    expect(rawToSUnit(0)).toBe('S0');
  });

  it('returns S1 for raw 18', () => {
    expect(rawToSUnit(18)).toBe('S1');
  });

  it('returns S5 for raw 90', () => {
    expect(rawToSUnit(90)).toBe('S5');
  });

  it('returns S9 for raw 162', () => {
    expect(rawToSUnit(162)).toBe('S9');
  });

  it('returns S9+ for raw just above S9 but below S9+10', () => {
    expect(rawToSUnit(170)).toBe('S9+');
  });

  it('returns S9+20 for raw 202', () => {
    expect(rawToSUnit(202)).toBe('S9+20');
  });

  it('returns S9+40 for raw 241', () => {
    expect(rawToSUnit(241)).toBe('S9+40');
  });

  it('returns S9+40 for raw 255 (max in default cal)', () => {
    expect(rawToSUnit(255)).toBe('S9+40');
  });

  it('clamps out-of-range values', () => {
    expect(rawToSUnit(-5)).toBe('S0');
    expect(rawToSUnit(999)).toBe('S9+40');
  });
});

// ── rawToDbm ──────────────────────────────────────────────────────────────

describe('rawToDbm', () => {
  it('returns -54 dBm at S0 (raw 0)', () => {
    expect(rawToDbm(0)).toBe(-54);
  });

  it('returns 0 dBm at S9 (raw 162)', () => {
    expect(rawToDbm(162)).toBe(0);
  });

  it('returns 20 dBm at S9+20 (raw 202)', () => {
    expect(rawToDbm(202)).toBe(20);
  });

  it('returns 40 dBm at max (raw 255)', () => {
    expect(rawToDbm(255)).toBe(40);
  });

  it('interpolates between breakpoints', () => {
    // raw 172 is halfway between 162 (0) and 182 (10) → t=0.5 → 5
    const dbm = rawToDbm(172);
    expect(dbm).toBeGreaterThanOrEqual(0);
    expect(dbm).toBeLessThanOrEqual(10);
  });
});

// ── formatDbm ─────────────────────────────────────────────────────────────

describe('formatDbm', () => {
  it('formats negative values with unicode minus', () => {
    expect(formatDbm(-67)).toBe('\u221267 dBm');
  });

  it('formats -127 dBm', () => {
    expect(formatDbm(-127)).toBe('\u2212127 dBm');
  });

  it('formats positive values with plus sign', () => {
    expect(formatDbm(0)).toBe('+0 dBm');
  });
});

// ── Segment rendering logic (segment count → active segments) ─────────────

describe('segment rendering logic', () => {
  it('0 active segments at S0 (raw 0)', () => {
    expect(Math.floor(rawToSegments(0))).toBe(0);
  });

  it('~6 segments at S5 (raw 90)', () => {
    const segs = rawToSegments(90);
    expect(segs).toBeGreaterThan(6);
    expect(segs).toBeLessThan(7);
  });

  it('11 full segments at S9 (raw 162)', () => {
    expect(Math.floor(rawToSegments(162))).toBe(11);
  });

  it('14 full segments at S9+20 (raw 202)', () => {
    expect(Math.floor(rawToSegments(202))).toBe(14);
  });

  it('20 full segments at max (raw 255)', () => {
    expect(Math.floor(rawToSegments(255))).toBe(20);
  });

  it('fractional segment for mid-S-unit value', () => {
    const segs = rawToSegments(27); // halfway between S1 and S2
    expect(segs % 1).toBeGreaterThan(0);
  });
});

// ── Smoother release τ (MOR-481) ───────────────────────────────────────────
// The bar fill must track the fast numeric readout within ~150 ms. The
// falling-edge time constant is the second arg to createSmoother(); a slow
// release (e.g. 0.25 ≈ 250 ms) makes the bar visibly lag the number on
// downward steps. Pin the snappier release here so a regression is caught.

describe('LinearSMeter smoother release τ', () => {
  const source = readFileSync(
    resolve(process.cwd(), 'src/components-v2/meters/LinearSMeter.svelte'),
    'utf8',
  );

  it('calls createSmoother with the snappy release τ (0.10), not the slow 0.25', () => {
    const match = source.match(/createSmoother\(\s*([0-9.]+)\s*,\s*([0-9.]+)/);
    expect(match).not.toBeNull();
    const attack = Number(match![1]);
    const release = Number(match![2]);
    // Attack unchanged (fast punch-in).
    expect(attack).toBeCloseTo(0.06, 5);
    // Release reduced from 0.25 → 0.10 so the bar reaches the target within
    // ~150 ms. Anything ≥ 0.25 reintroduces the visible lag (MOR-481).
    expect(release).toBeCloseTo(0.1, 5);
    expect(release).toBeLessThan(0.25);
  });
});
