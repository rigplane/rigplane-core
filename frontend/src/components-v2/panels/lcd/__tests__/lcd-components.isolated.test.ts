/**
 * Component-level render tests for AmberFrequency and AmberSmeter.
 *
 * Uses native svelte mount() in jsdom. AmberSmeter depends on smeter-scale,
 * which reads from the capabilities store — mocked below to a fixture
 * calibration table (formerly the smeter-scale.ts hardcoded IC-7610
 * default; MOR-1451 removed that silent fallback, so a fixture is now
 * required for these tests to exercise a "calibrated" AmberSmeter at all).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';
import type { MeterCalPoint } from '$lib/types/capabilities';

// Fixture calibration table — the numbers `rigs/ic7610.toml` declares, no
// longer a production default (MOR-1451): a radio profile with no
// `[meters.s_meter]` table now renders an honest raw-scale label instead of
// borrowing these.
const IC7610_LIKE_S_METER_CAL: MeterCalPoint[] = [
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

// Mutable indirection so the MOR-2034 discrimination suite (bottom of file)
// can swap in a non-uniform table for its own tests, then hand the default
// back — `vi.mock` factories are hoisted, but this arrow function re-reads
// the variable on every call, so a later reassignment is picked up without
// re-registering the mock.
let activeSMeterCal: MeterCalPoint[] = IC7610_LIKE_S_METER_CAL;

// Mock capabilities store before any component import
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getSmeterCalibration: () => activeSMeterCal,
  getSmeterRedline: () => null,
  isAudioFftScope: () => false,
  hasAudioFft: () => false,
  hasDualReceiver: () => false,
  getCapabilities: () => null,
  // meter-utils calibrated formatters resolve through these; null → the
  // hardcoded IC-7610 fallback knots (power/swr/alc/vd/id/comp — unaffected
  // by MOR-1451, out of its scope). s_meter is the one exception: it has no
  // such fallback, hence the explicit fixture above.
  getMeterCalibration: (meterType: string) =>
    meterType === 's_meter' ? activeSMeterCal : null,
  getMeterRedline: () => null,
  getControlRange: () => null,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  getActiveReceiver: vi.fn(),
  getRadioState: vi.fn(),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({
  getChannel: () => ({
    connect: vi.fn(),
    disconnect: vi.fn(),
    onBinary: vi.fn(() => vi.fn()),
  }),
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  markScopeFrame: vi.fn(),
}));

vi.mock('$lib/runtime/props/panel-props', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/props/panel-props')>(),
  resolveFilterModeConfig: () => null,
}));

import AmberFrequency from '../AmberFrequency.svelte';
import AmberSmeter from '../AmberSmeter.svelte';
import {
  formatPowerWatts,
  formatSwr,
  formatAlc,
  formatCompDb,
} from '../../meter-utils';
import { calibratedToSUnit, calibratedToDbm, formatDbm } from '../../../meters/smeter-scale';

let target: HTMLDivElement;

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(() => {
  document.body.removeChild(target);
});

// ── AmberFrequency ──────────────────────────────────────────────────────────

describe('AmberFrequency', () => {
  it('mounts without errors', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 14_074_000 } });
    expect(target.querySelector('.lcd-freq')).not.toBeNull();
    unmount(component);
  });

  it('renders frequency digits for 14.074.000 Hz', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 14_074_000 } });
    const active = target.querySelector('.freq-active')!;
    const mhz = active.querySelector('.seg-mhz')!.textContent;
    const khz = active.querySelector('.seg-khz')!.textContent;
    const hz = active.querySelector('.seg-hz')!.textContent;
    expect(mhz).toBe('14');
    expect(khz).toBe('074');
    expect(hz).toBe('000');
    unmount(component);
  });

  it('displays correct digit segments (mhz + dot + khz + dot + hz)', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 7_200_500 } });
    const active = target.querySelector('.freq-active')!;
    // 5 span children: seg-mhz, seg-dot, seg-khz, seg-dot, seg-hz
    const spans = active.querySelectorAll('span');
    expect(spans.length).toBe(5);
    expect(spans[0].textContent).toBe('7');    // mhz
    expect(spans[1].textContent).toBe('.');    // dot
    expect(spans[2].textContent).toBe('200');  // khz
    expect(spans[3].textContent).toBe('.');    // dot
    expect(spans[4].textContent).toBe('500');  // hz
    unmount(component);
  });

  it('handles zero frequency gracefully (shows dashes)', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 0 } });
    const active = target.querySelector('.freq-active')!;
    expect(active.querySelector('.seg-mhz')!.textContent).toBe('--');
    expect(active.querySelector('.seg-khz')!.textContent).toBe('---');
    expect(active.querySelector('.seg-hz')!.textContent).toBe('---');
    unmount(component);
  });

  it('handles negative frequency as zero (shows dashes)', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: -100 } });
    const active = target.querySelector('.freq-active')!;
    expect(active.querySelector('.seg-mhz')!.textContent).toBe('--');
    unmount(component);
  });

  it('renders ghost segments (all-8s) for LCD look', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 14_074_000 } });
    const ghost = target.querySelector('.freq-ghost')!;
    expect(ghost.querySelector('.seg-khz')!.textContent).toBe('888');
    expect(ghost.querySelector('.seg-hz')!.textContent).toBe('888');
    unmount(component);
  });

  it('applies large size class by default', () => {
    const component = mount(AmberFrequency, { target, props: { freqHz: 14_074_000 } });
    expect(target.querySelector('.lcd-freq-large')).not.toBeNull();
    unmount(component);
  });

  it('applies small size class when size="small"', () => {
    const component = mount(AmberFrequency, {
      target,
      props: { freqHz: 14_074_000, size: 'small' },
    });
    expect(target.querySelector('.lcd-freq-small')).not.toBeNull();
    unmount(component);
  });
});

// ── AmberSmeter ─────────────────────────────────────────────────────────────

describe('AmberSmeter', () => {
  it('mounts without errors', () => {
    const component = mount(AmberSmeter, { target, props: { value: 0 } });
    expect(target.querySelector('.lcd-smeter')).not.toBeNull();
    unmount(component);
  });

  it('renders 192 bar segments', () => {
    const component = mount(AmberSmeter, { target, props: { value: 100 } });
    const segs = target.querySelectorAll('.seg');
    expect(segs.length).toBe(192);
    unmount(component);
  });

  it('displays signal level indicator (readout)', () => {
    const component = mount(AmberSmeter, { target, props: { value: 100 } });
    const readout = target.querySelector('.meter-readout')!;
    expect(readout.querySelector('.readout-s')).not.toBeNull();
    expect(readout.querySelector('.readout-dbm')).not.toBeNull();
    unmount(component);
  });

  it('handles the calibrated floor — no filled segments', () => {
    const component = mount(AmberSmeter, { target, props: { value: -54 } });
    const filled = target.querySelectorAll('.seg.filled');
    expect(filled.length).toBe(0);
    // S-unit readout should show S0
    expect(target.querySelector('.readout-s')!.textContent).toBe('S0');
    unmount(component);
  });

  it('fills proportional segments for a mid-range calibrated signal', () => {
    const component = mount(AmberSmeter, { target, props: { value: -24 } });
    const filled = target.querySelectorAll('.seg.filled');
    expect(filled.length).toBeGreaterThan(35);
    expect(filled.length).toBeLessThan(75);
    unmount(component);
  });

  it('fills to the top calibrated anchor for the strongest signal', () => {
    const component = mount(AmberSmeter, { target, props: { value: 40 } });
    const filled = target.querySelectorAll('.seg.filled');
    expect(filled.length).toBe(192);
    unmount(component);
  });

  it('marks over-S9 segments with over-s9 class', () => {
    const component = mount(AmberSmeter, { target, props: { value: 20 } });
    const overS9 = target.querySelectorAll('.seg.filled.over-s9');
    expect(overS9.length).toBeGreaterThan(0);
    unmount(component);
  });

  it('renders scale ticks and labels', () => {
    const component = mount(AmberSmeter, { target, props: { value: 100 } });
    const scale = target.querySelector('.meter-scale')!;
    // Major ticks have labels (S1, S3, S5, S7, S9, +10, +20, +30, +40)
    const labels = scale.querySelectorAll('.tick-label');
    expect(labels.length).toBeGreaterThan(0);
    // S label present
    expect(scale.querySelector('.scale-s-label')!.textContent).toBe('S');
    unmount(component);
  });

  it('applies tx class when txActive is true', () => {
    const component = mount(AmberSmeter, { target, props: { value: 100, txActive: true } });
    const txSegs = target.querySelectorAll('.seg.filled.tx');
    expect(txSegs.length).toBeGreaterThan(0);
    unmount(component);
  });

  it('shows dBm in readout', () => {
    const component = mount(AmberSmeter, { target, props: { value: 0 } });
    const dbm = target.querySelector('.readout-dbm')!;
    expect(dbm.textContent).toContain('\u221273');
    expect(dbm.textContent).toContain('dBm');
    unmount(component);
  });

  // ── MOR-483 part 2: PO/SWR/ALC/COMP readouts use calibrated formatters ──

  it('PO readout uses calibrated formatPowerWatts, not raw/255*100', () => {
    const component = mount(AmberSmeter, { target, props: { value: 143, source: 'PO' } });
    const sub = target.querySelector('.readout-dbm')!;
    expect(sub.textContent).toBe(formatPowerWatts(143)); // '50W', not '56W'
    unmount(component);
  });

  it('SWR readout uses calibrated formatSwr, not 1.0+raw/255*8.9', () => {
    const component = mount(AmberSmeter, { target, props: { value: 80, source: 'SWR' } });
    const sub = target.querySelector('.readout-dbm')!;
    expect(sub.textContent).toBe(formatSwr(80)); // '2.0', not '3.8'
    unmount(component);
  });

  it('ALC readout uses calibrated formatAlc, not raw/255*100', () => {
    const component = mount(AmberSmeter, { target, props: { value: 60, source: 'ALC' } });
    const sub = target.querySelector('.readout-dbm')!;
    expect(sub.textContent).toBe(formatAlc(60)); // '50%', not '24%'
    unmount(component);
  });

  it('COMP readout uses calibrated formatCompDb, not raw/255*20', () => {
    const component = mount(AmberSmeter, { target, props: { value: 75, source: 'COMP' } });
    const sub = target.querySelector('.readout-dbm')!;
    expect(sub.textContent).toBe(formatCompDb(75)); // '15 dB', not '6dB'
    unmount(component);
  });
});

// ── MOR-2034: non-uniform-calibration discrimination ────────────────────────
// The S-source tests above ('handles the calibrated floor', 'shows dBm in
// readout') only assert exact text at S0 and S9 — both calibration ANCHORS,
// reproduced identically by any reasonable derivation — and `IC7610_LIKE_S_
// METER_CAL` is itself uniform between them (6 dB/S-unit). Neither can tell
// AmberSmeter's real `calibratedToSUnit`/`calibratedToDbm` delegation
// (`AmberSmeter.svelte`'s import) apart from a hardcoded 6 dB/S-unit
// reimplementation — the exact shape MOR-2024 found and fixed in a sibling
// file (`components-v2/panels/meter-utils.ts`'s old `formatSMeter`).
// AmberSmeter is outside `meter-contract.ts`'s `METER_REGISTRY` census (that
// census is `.svelte` files directly under `components-v2/meters/`; this one
// lives under `components-v2/panels/lcd/`, mounted by the `lcd-cockpit`/
// `lcd-scope` skins), so nothing else in the suite would catch a regression
// here. This block reuses `meter-contract.test.ts`'s own non-uniform
// fixture and probe reasoning (`components-v2/meters/__tests__/
// meter-contract.test.ts` — see its file header for the full worked-out
// argument for why these three probes rule out every fixed per-S-unit step)
// against the real mounted component, the same technique that file already
// applies to `LinearSMeter.svelte`.
describe('AmberSmeter — non-uniform calibration (MOR-2034)', () => {
  const NON_UNIFORM_CAL: MeterCalPoint[] = [
    { raw: 0, actual: -54, label: 'S0' },
    { raw: 26, actual: -48, label: 'S1' },
    { raw: 52, actual: -45, label: 'S2' },
    { raw: 78, actual: -42, label: 'S3' },
    { raw: 104, actual: -36, label: 'S4' },
    { raw: 130, actual: -30, label: 'S5' },
    { raw: 156, actual: -12, label: 'S6' }, // +18 dB step, vs. 3-6 dB elsewhere
    { raw: 182, actual: -6, label: 'S7' },
    { raw: 208, actual: -3, label: 'S8' },
    { raw: 230, actual: 0, label: 'S9' },
    { raw: 255, actual: 20, label: 'S9+20' },
  ];

  beforeEach(() => {
    activeSMeterCal = NON_UNIFORM_CAL;
  });

  afterEach(() => {
    activeSMeterCal = IC7610_LIKE_S_METER_CAL;
  });

  // Kills: AmberSmeter reimplementing its own S-unit math (a fixed step or
  // any other formula) instead of delegating to calibratedToSUnit — on this
  // non-uniform table the two would diverge at one of these three probes
  // even though they agree everywhere on the uniform table above. Reads
  // `.readout-s` specifically, not the whole component's textContent: the
  // scale ruler renders odd S-unit tick labels (S1/S3/S5/S7/S9) elsewhere in
  // the same component (see `majorTicks` in `AmberSmeter.svelte`), so a
  // whole-textContent search would risk a false pass the way
  // `meter-contract.test.ts`'s file header warns about; scoping to the
  // readout element side-steps that collision entirely.
  it.each([-43, -33, -11])(
    'renders the exact S-unit calibratedToSUnit computes at probe %d, not a local approximation',
    (actual) => {
      const component = mount(AmberSmeter, { target, props: { value: actual } });
      const readout = target.querySelector('.readout-s')!.textContent;
      expect(readout).toBe(calibratedToSUnit(actual));
      unmount(component);
    },
  );

  it.each([-43, -33, -11])(
    'renders the exact dBm text calibratedToDbm/formatDbm compute at probe %d, not a local approximation',
    (actual) => {
      const component = mount(AmberSmeter, { target, props: { value: actual } });
      const readout = target.querySelector('.readout-dbm')!.textContent;
      expect(readout).toBe(formatDbm(calibratedToDbm(actual)));
      unmount(component);
    },
  );
});
