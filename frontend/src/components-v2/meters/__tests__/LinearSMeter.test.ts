import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';

// MOR-1451: `smeter-scale.ts` no longer ships a hardcoded per-radio fallback
// curve — a radio with no declared `[meters.s_meter]` table is UNCALIBRATED,
// and every text-producing function degrades to an honest raw-scale label
// instead of borrowing a foreign radio's numbers. The tests below that
// exercise "a" calibrated curve need one explicitly; this fixture — the
// values `rigs/ic7610.toml` used to declare, moved here so they read as
// what they are: one worked example, not a production default — stands in
// for "some radio profile has published a curve", matching every existing
// assertion's IC-7610-flavoured comments.
const IC7610_LIKE_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 52, actual: -36, label: 'S3' },
  { raw: 78, actual: -24, label: 'S5' },
  { raw: 103, actual: -12, label: 'S7' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 165, actual: 10, label: 'S9+10' },
  { raw: 200, actual: 20, label: 'S9+20' },
  { raw: 240, actual: 40, label: 'S9+40' },
];

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getSmeterCalibration: vi.fn(() => IC7610_LIKE_CAL),
  getSmeterRedline: vi.fn(() => null),
}));

import { getSmeterCalibration } from '$lib/stores/capabilities.svelte';
import LinearSMeter from '../LinearSMeter.svelte';
import {
  rawToSegments,
  rawToSUnit,
  rawToDbm,
  formatDbm,
  isSmeterCalibrated,
  calibratedToSUnit,
} from '../smeter-scale';

beforeEach(() => {
  vi.mocked(getSmeterCalibration).mockReturnValue(IC7610_LIKE_CAL);
});

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

// ── MOR-1451: no hardcoded per-radio fallback curve ─────────────────────────
// A radio whose profile declares no `[meters.s_meter]` table gets NO
// calibration server-side either (`_civ_rx.py`'s `_calibrated_meter_value`
// publishes the raw byte unchanged, flagged "uncalibrated" —
// `interpolate_meter`'s own `(value, calibrated)` contract). `LinearSMeter`
// therefore receives that raw byte directly as `value`, with no call-site
// conversion of any kind — exactly production's real data flow. It must
// never borrow another radio's numbers to interpret it.

describe('uncalibrated fallback — no radio-specific curve is fabricated (MOR-1451)', () => {
  beforeEach(() => {
    vi.mocked(getSmeterCalibration).mockReturnValue(null);
  });

  it('isSmeterCalibrated() is false with no profile curve', () => {
    expect(isSmeterCalibrated()).toBe(false);
  });

  it('rawToSUnit renders the plain raw number, never a fabricated S-unit', () => {
    expect(rawToSUnit(53)).toBe('53');
    expect(rawToSUnit(0)).toBe('0');
    expect(rawToSUnit(255)).toBe('255');
  });

  it('rawToDbm passes the raw value straight through (not a claimed dBm reading)', () => {
    expect(rawToDbm(53)).toBe(53);
  });

  it('formatDbm renders an explicit "uncalibrated" label, not a fabricated unit', () => {
    expect(formatDbm(null)).toBe('uncalibrated');
  });

  it('LinearSMeter renders the raw number and "uncalibrated" — never S9+40 (the reported bug), fed the raw byte exactly as the backend publishes it (no call-site conversion)', () => {
    const target = mountMeter({ value: 53 });
    const text = target.textContent ?? '';

    expect(text).not.toContain('S9+40');
    expect(text).toContain('53');
    expect(text).toContain('uncalibrated');
  });
});

// ── MOR-1451 conformance case: the IC-7300's own curve ──────────────────────
//
// IMPORTANT — the BACKEND, not the frontend, does the raw->calibrated
// conversion when a radio profile declares `[meters.s_meter]`
// (`_civ_rx.py`'s `_calibrated_meter_value` -> `interpolate_meter`, over
// `profile.meter_calibrations` — see `test_civ_rx_coverage.py`'s
// pre-existing "raw 111 -> -8" pin for a worked example on a different
// profile). `ServerState.main.sMeter` — and therefore `LinearSMeter`'s
// `value` prop — is ALREADY the calibrated dB-rel-S9 reading for any radio
// whose profile has a curve; it is raw device-scale ONLY for a radio with
// no curve (the `isSmeterCalibrated()` suite above). rigs/ic7300.toml's new
// `[meters.s_meter]` table (this PR) moves the IC-7300 from the second
// bucket into the first — its wire byte 53 now arrives at the frontend as
// -30 dB-rel-S9, calibrated, NOT raw. The assertions below exercise exactly
// that domain (`calibratedToSUnit`, not `rawToSUnit` on the wire byte —
// feeding the wire byte through a SECOND raw->calibrated conversion was an
// earlier, reverted draft of this fix and is exactly the double-conversion
// bug a PR reviewer caught). The reported "S9+40" symptom happened because,
// before this PR, the IC-7300 profile had NO table: the backend published
// raw 53 untouched (uncalibrated), and the frontend's since-removed
// hardcoded IC-7610-shaped fallback curve misread that raw byte as if it
// were already a calibrated dB-rel-S9 reading, clamping it to the fallback
// curve's top anchor (+40 dB) — "S9+40" regardless of the actual signal.
// Fixing that required BOTH halves: the profile table (so the backend
// calibrates this radio at all) and the honest-uncalibrated-fallback
// removal above (so a radio that still has no table never borrows a
// foreign curve again).

describe('IC-7300 profile conformance — calibrated dB-rel-S9 renders the correct S-unit, not S9+40 (MOR-1451)', () => {
  const IC7300_S_METER_CAL = [
    { raw: 0, actual: -54, label: 'S0' },
    { raw: 120, actual: 0, label: 'S9' },
    { raw: 241, actual: 60, label: 'S9+60' },
  ];

  beforeEach(() => {
    vi.mocked(getSmeterCalibration).mockReturnValue(IC7300_S_METER_CAL);
  });

  it('the anchor round-trips: raw axis 0/120/241 -> S0/S9/S9+60', () => {
    // Pure interpolator correctness over the published table —
    // independent of which domain a given caller feeds it (see the
    // calibrated-domain assertions below for what LinearSMeter's `value`
    // prop actually carries in production).
    expect(rawToSUnit(0)).toBe('S0');
    expect(rawToSUnit(120)).toBe('S9');
    expect(rawToSUnit(241)).toBe('S9+60');
  });

  it('calibratedToSUnit(0) -> S9, calibratedToSUnit(60) -> S9+60 (the documented anchors)', () => {
    expect(calibratedToSUnit(0)).toBe('S9');
    expect(calibratedToSUnit(60)).toBe('S9+60');
  });

  it('the live-evidence backend value (-30, from raw 53) renders S4, not the reported S9+40', () => {
    // -30 dB-rel-S9 is what the backend actually publishes for the
    // IC-7300 live-fixture's raw sMeter=53 under this profile's curve
    // (interpolate_meter(53, ...) == -30, pinned server-side in
    // tests/test_rig_ic7300.py). This is the value LinearSMeter's `value`
    // prop receives in production — never the raw byte itself.
    expect(calibratedToSUnit(-30)).not.toBe('S9+40');
    expect(calibratedToSUnit(-30)).toBe('S4');
  });

  it('LinearSMeter renders S4 for the calibrated live-evidence value end-to-end', () => {
    const target = mountMeter({ value: -30 });
    const text = target.textContent ?? '';

    expect(text).not.toContain('S9+40');
    expect(text).toContain('S4');
  });
});
