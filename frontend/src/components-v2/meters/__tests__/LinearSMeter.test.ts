import { describe, it, expect, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import LinearSMeter from '../LinearSMeter.svelte';
import {
  rawToSegments,
  rawToSUnit,
  rawToDbm,
  formatDbm,
} from '../smeter-scale';

let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

function mountMeter(props: ComponentProps<typeof LinearSMeter>) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  roots.push(target);
  const component = mount(LinearSMeter, { target, props });
  flushSync();
  components.push(component);
  return target;
}

afterEach(() => {
  components.forEach((component) => unmount(component));
  roots.forEach((root) => root.remove());
  components = [];
  roots = [];
});

// ── rawToSegments ──────────────────────────────────────────────────────────

describe('rawToSegments', () => {
  it('maps S0 (raw 0) to 0 segments', () => {
    expect(rawToSegments(0)).toBe(0);
  });

  it('maps S1 (raw 26 in the IC-7610 profile) to ~1.22 segments', () => {
    expect(rawToSegments(26)).toBeCloseTo((1 / 9) * 11, 5);
  });

  it('maps S9 (raw 130 in the IC-7610 profile) to exactly 11 segments', () => {
    expect(rawToSegments(130)).toBe(11);
  });

  it('maps S9+20 (raw 200) to its calibrated tick position', () => {
    const expected = 11 + ((200 - 130) / (240 - 130)) * 9;
    expect(rawToSegments(200)).toBeCloseTo(expected, 5);
  });

  it('maps the top calibrated anchor (raw 240) to exactly 20 segments', () => {
    expect(rawToSegments(240)).toBe(20);
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

  it('returns S1 for raw 26', () => {
    expect(rawToSUnit(26)).toBe('S1');
  });

  it('returns S5 for raw 78', () => {
    expect(rawToSUnit(78)).toBe('S5');
  });

  it('returns S9 for raw 130', () => {
    expect(rawToSUnit(130)).toBe('S9');
  });

  it('returns S9+ for raw just above S9 but below S9+10', () => {
    expect(rawToSUnit(140)).toBe('S9+');
  });

  it('returns S9+20 for raw 200', () => {
    expect(rawToSUnit(200)).toBe('S9+20');
  });

  it('returns S9+40 for raw 240', () => {
    expect(rawToSUnit(240)).toBe('S9+40');
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

  it('returns 0 dBm at S9 (raw 130)', () => {
    expect(rawToDbm(130)).toBe(0);
  });

  it('returns 20 dBm at S9+20 (raw 200)', () => {
    expect(rawToDbm(200)).toBe(20);
  });

  it('returns 40 dBm at max (raw 255)', () => {
    expect(rawToDbm(255)).toBe(40);
  });

  it('interpolates between breakpoints', () => {
    // raw 172 is between 165 (+10) and 200 (+20) in the IC-7610 profile.
    const dbm = rawToDbm(172);
    expect(dbm).toBeGreaterThanOrEqual(10);
    expect(dbm).toBeLessThanOrEqual(20);
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

  it('~6 segments at S5 (raw 78)', () => {
    const segs = rawToSegments(78);
    expect(segs).toBeGreaterThan(6);
    expect(segs).toBeLessThan(7);
  });

  it('11 full segments at S9 (raw 130)', () => {
    expect(Math.floor(rawToSegments(130))).toBe(11);
  });

  it('16 full segments at S9+20 (raw 200)', () => {
    expect(Math.floor(rawToSegments(200))).toBe(16);
  });

  it('20 full segments at the top calibrated anchor (raw 240)', () => {
    expect(Math.floor(rawToSegments(240))).toBe(20);
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

describe('LinearSMeter calibrated S-meter domain', () => {
  it('renders S9 and -73 dBm for a calibrated 0 dB-rel-S9 reading', () => {
    const target = mountMeter({ value: 0 });
    const text = target.textContent ?? '';

    expect(text).toContain('S9');
    expect(text).toContain('\u221273 dBm');
  });

  it('renders S9+20 and -53 dBm for a calibrated +20 dB reading', () => {
    const target = mountMeter({ value: 20 });
    const text = target.textContent ?? '';

    expect(text).toContain('S9+20');
    expect(text).toContain('\u221253 dBm');
  });
});
