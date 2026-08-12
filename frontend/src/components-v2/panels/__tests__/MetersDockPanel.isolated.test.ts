import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import MetersDockPanel from '../MetersDockPanel.svelte';
import { createSmoother } from '$lib/utils/smoothing.svelte';
import {
  formatAmps,
  formatVolts,
  formatCompDb,
  isAlcFault,
  isSwrFault,
  peakHoldDisplay,
  updatePeakHold,
} from '../meter-utils';

// MOR-1470: every meter with a profile-declared calibration table arrives
// in engineering units (backend interpolates at the observation boundary,
// MOR-469); the panel and the meter-utils helpers render that value
// directly. This file seeds the REAL capabilities store with an
// IC-7610-shaped profile (all seven tables) and feeds engineering-domain
// props; `hasTx` derives from the seeded `tx: true`.
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';

const IC7610_LIKE_S_METER_CAL = [
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

const IC7610_LIKE_TX_METER_CALS = {
  power: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 143, actual: 50, label: '50' },
    { raw: 212, actual: 100, label: '100' },
  ],
  swr: [
    { raw: 0, actual: 1.0, label: '1.0' },
    { raw: 48, actual: 1.5, label: '1.5' },
    { raw: 80, actual: 2.0, label: '2.0' },
    { raw: 120, actual: 3.0, label: '3.0' },
    { raw: 240, actual: 6.0, label: '6.0+' },
  ],
  alc: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 120, actual: 100, label: '100' },
  ],
  vd: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 13, actual: 10, label: '10' },
    { raw: 184, actual: 13.8, label: '13.8' },
    { raw: 241, actual: 16, label: '16' },
  ],
  id: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 151, actual: 10, label: '10' },
    { raw: 195, actual: 15, label: '15' },
    { raw: 212, actual: 25, label: '25' },
  ],
  comp: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 75, actual: 15, label: '15' },
    { raw: 150, actual: 30, label: '30' },
  ],
};

function makeCaps(): Capabilities {
  return {
    model: 'IC-7610',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB', 'LSB', 'CW', 'AM', 'FM'],
    filters: ['FIL1', 'FIL2', 'FIL3'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    meterCalibrations: {
      s_meter: IC7610_LIKE_S_METER_CAL,
      ...IC7610_LIKE_TX_METER_CALS,
    },
  };
}

beforeEach(() => {
  setCapabilities(makeCaps());
});

afterEach(() => {
  clearCapabilities();
});

// ---------------------------------------------------------------------------
// New formatters
// ---------------------------------------------------------------------------

describe('formatAmps (calibrated: input is amps)', () => {
  it('returns 0.0 A for 0 A', () => {
    expect(formatAmps(0)).toBe('0.0 A');
  });
  it('returns 10.0 A', () => {
    expect(formatAmps(10)).toBe('10.0 A');
  });
  it('returns 15.0 A', () => {
    expect(formatAmps(15)).toBe('15.0 A');
  });
  it('clamps beyond-scale readings to the 25 A top knot', () => {
    expect(formatAmps(300)).toBe('25.0 A');
  });
});

describe('formatVolts (calibrated: input is volts)', () => {
  it('returns 0.0 V for 0 V', () => {
    expect(formatVolts(0)).toBe('0.0 V');
  });
  it('returns 10.0 V', () => {
    expect(formatVolts(10)).toBe('10.0 V');
  });
  it('returns 16.0 V at the top knot', () => {
    expect(formatVolts(16)).toBe('16.0 V');
  });
});

describe('formatCompDb (calibrated: input is dB)', () => {
  it('returns 0 dB for 0', () => {
    expect(formatCompDb(0)).toBe('0 dB');
  });
  it('returns 15 dB', () => {
    expect(formatCompDb(15)).toBe('15 dB');
  });
  it('returns 30 dB at the top knot', () => {
    expect(formatCompDb(30)).toBe('30 dB');
  });
});

describe('isSwrFault (calibrated: input is the ratio)', () => {
  it('is false at SWR 1.0', () => {
    expect(isSwrFault(1.0)).toBe(false);
  });
  it('is false at SWR exactly 2.0', () => {
    expect(isSwrFault(2.0)).toBe(false);
  });
  it('is true above 2.0', () => {
    expect(isSwrFault(3.0)).toBe(true);
  });
  it('is true at the off-scale top', () => {
    expect(isSwrFault(6.0)).toBe(true);
  });
});

describe('isAlcFault (calibrated: input is normalized 0-1)', () => {
  it('is false at 0% ALC', () => {
    expect(isAlcFault(0)).toBe(false);
  });
  it('is false at 90% ALC', () => {
    expect(isAlcFault(0.9)).toBe(false);
  });
  it('is true above 90% ALC', () => {
    expect(isAlcFault(0.95)).toBe(true);
  });
});

describe('updatePeakHold', () => {
  it('initializes state when undefined', () => {
    const s = updatePeakHold(undefined, 42, 1000);
    expect(s).toEqual({ latchedPeak: 42, latchedAt: 1000 });
  });
  it('re-latches on a strictly higher current and bumps timestamp', () => {
    const s = updatePeakHold({ latchedPeak: 100, latchedAt: 0 }, 120, 500);
    expect(s).toEqual({ latchedPeak: 120, latchedAt: 500 });
  });
  it('keeps latched state unchanged when current is lower and decay not elapsed', () => {
    const s0 = { latchedPeak: 100, latchedAt: 0 };
    const s = updatePeakHold(s0, 0, 1000);
    // Same reference — no state churn during the hold window.
    expect(s).toBe(s0);
  });
  it('re-anchors to current once decay window has elapsed', () => {
    const s = updatePeakHold({ latchedPeak: 100, latchedAt: 0 }, 25, 2000);
    expect(s).toEqual({ latchedPeak: 25, latchedAt: 2000 });
  });
  it('repeated ticks do not compound (linear, not exponential, decay)', () => {
    // Simulate the 100ms ticker feeding (peak=100, current=0) over 1s.
    let state = updatePeakHold(undefined, 100, 0);
    for (let t = 100; t <= 1000; t += 100) {
      state = updatePeakHold(state, 0, t);
    }
    // State is still the original latched peak — decay happens at render.
    expect(state).toEqual({ latchedPeak: 100, latchedAt: 0 });
    // Displayed value after 1s (half the 2s window) is ~50, not ~3 (compound).
    expect(peakHoldDisplay(state, 0, 1000)).toBeCloseTo(50, 5);
  });
});

describe('peakHoldDisplay', () => {
  it('equals the latched peak at t=0', () => {
    expect(peakHoldDisplay({ latchedPeak: 100, latchedAt: 0 }, 0, 0)).toBe(100);
  });
  it('is linear at t = decayMs/2 regardless of tick cadence', () => {
    expect(peakHoldDisplay({ latchedPeak: 100, latchedAt: 0 }, 0, 1000, 2000)).toBeCloseTo(
      50,
      5,
    );
  });
  it('clamps to current once the window elapses', () => {
    expect(peakHoldDisplay({ latchedPeak: 100, latchedAt: 0 }, 0, 2000, 2000)).toBe(0);
    expect(peakHoldDisplay({ latchedPeak: 100, latchedAt: 0 }, 7, 2500, 2000)).toBe(7);
  });
  it('never shows below the live current sample', () => {
    // Rising signal mid-decay should dominate the decaying marker.
    expect(peakHoldDisplay({ latchedPeak: 100, latchedAt: 0 }, 80, 1500, 2000)).toBe(80);
  });
  it('returns current when no latched state exists', () => {
    expect(peakHoldDisplay(undefined, 42, 1000)).toBe(42);
  });
});

// ---------------------------------------------------------------------------
// MetersDockPanel component
// ---------------------------------------------------------------------------

let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

function mountPanel(props: ComponentProps<typeof MetersDockPanel>) {
  const t = document.createElement('div');
  document.body.appendChild(t);
  roots.push(t);
  const component = mount(MetersDockPanel, { target: t, props });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  roots = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  roots.forEach((r) => r.remove());
  components = [];
  roots = [];
});

// Engineering-domain props (what the backend publishes for a rig whose
// profile declares the tables): 50 W, SWR 1.5, ALC at 50% of redline,
// 0 dB-rel-S9 (= S9).
const fullProps: ComponentProps<typeof MetersDockPanel> = {
  sValue: 0,
  powerMeter: 50,
  swrMeter: 1.5,
  alcMeter: 0.5,
  txActive: false,
};

describe('MetersDockPanel structure', () => {
  it('renders the STATION METERS header', () => {
    const t = mountPanel(fullProps);
    expect(t.querySelector('.dock-title')?.textContent).toBe('STATION METERS');
  });

  it('renders four tiles when all four state fields are defined', () => {
    const t = mountPanel(fullProps);
    expect(t.querySelectorAll('.dock-tile')).toHaveLength(4);
  });

  it('renders tiles in fixed priority order Po, SWR, ALC, S', () => {
    const t = mountPanel(fullProps);
    const keys = Array.from(t.querySelectorAll('.dock-tile')).map((el) =>
      el.getAttribute('data-meter'),
    );
    expect(keys).toEqual(['po', 'swr', 'alc', 's']);
  });

  it('shows RX state label when txActive is false', () => {
    const t = mountPanel(fullProps);
    const state = t.querySelector('.dock-tx-state');
    expect(state?.textContent).toBe('RX');
    expect(state?.getAttribute('data-active')).toBe('false');
  });

  it('shows TX state label when txActive is true', () => {
    const t = mountPanel({ ...fullProps, txActive: true });
    const state = t.querySelector('.dock-tx-state');
    expect(state?.textContent).toBe('TX');
    expect(state?.getAttribute('data-active')).toBe('true');
  });
});

describe('MetersDockPanel capability gating', () => {
  it('hides Po tile when powerMeter is undefined', () => {
    const t = mountPanel({ ...fullProps, powerMeter: undefined });
    expect(t.querySelector('[data-meter="po"]')).toBeNull();
    expect(t.querySelectorAll('.dock-tile')).toHaveLength(3);
  });

  it('hides SWR tile when swrMeter is undefined', () => {
    const t = mountPanel({ ...fullProps, swrMeter: undefined });
    expect(t.querySelector('[data-meter="swr"]')).toBeNull();
  });

  it('hides ALC tile when alcMeter is undefined', () => {
    const t = mountPanel({ ...fullProps, alcMeter: undefined });
    expect(t.querySelector('[data-meter="alc"]')).toBeNull();
  });

  it('hides S tile when sValue is undefined', () => {
    const t = mountPanel({ ...fullProps, sValue: undefined });
    expect(t.querySelector('[data-meter="s"]')).toBeNull();
  });

  it('renders no tiles when all state fields are undefined', () => {
    const t = mountPanel({
      sValue: undefined,
      powerMeter: undefined,
      swrMeter: undefined,
      alcMeter: undefined,
      txActive: false,
    });
    expect(t.querySelectorAll('.dock-tile')).toHaveLength(0);
  });

  it('still renders the header when every tile is hidden', () => {
    const t = mountPanel({
      sValue: undefined,
      powerMeter: undefined,
      swrMeter: undefined,
      alcMeter: undefined,
      txActive: false,
    });
    expect(t.querySelector('.dock-title')?.textContent).toBe('STATION METERS');
  });

  it('renders Id tile when idMeter is defined', () => {
    const t = mountPanel({ ...fullProps, idMeter: 10 });
    const tile = t.querySelector('[data-meter="id"]');
    expect(tile).not.toBeNull();
    expect(tile?.querySelector('.tile-value')?.textContent).toBe('10.0 A');
  });

  it('hides Id tile when idMeter is undefined', () => {
    const t = mountPanel({ ...fullProps, idMeter: undefined });
    expect(t.querySelector('[data-meter="id"]')).toBeNull();
  });

  it('renders Vd tile when vdMeter is defined', () => {
    const t = mountPanel({ ...fullProps, vdMeter: 10 });
    const tile = t.querySelector('[data-meter="vd"]');
    expect(tile).not.toBeNull();
    expect(tile?.querySelector('.tile-value')?.textContent).toBe('10.0 V');
  });

  it('hides Vd tile when vdMeter is undefined', () => {
    const t = mountPanel({ ...fullProps, vdMeter: undefined });
    expect(t.querySelector('[data-meter="vd"]')).toBeNull();
  });

  it('renders COMP tile when compMeter is defined and compressorOn=true', () => {
    const t = mountPanel({ ...fullProps, compMeter: 15, compressorOn: true });
    const tile = t.querySelector('[data-meter="comp"]');
    expect(tile).not.toBeNull();
    expect(tile?.querySelector('.tile-value')?.textContent).toBe('15 dB');
  });

  it('hides COMP tile when compressorOn is false', () => {
    const t = mountPanel({ ...fullProps, compMeter: 15, compressorOn: false });
    expect(t.querySelector('[data-meter="comp"]')).toBeNull();
  });

  it('hides COMP tile when compressorOn is undefined (gating)', () => {
    const t = mountPanel({ ...fullProps, compMeter: 15 });
    expect(t.querySelector('[data-meter="comp"]')).toBeNull();
  });

  it('hides COMP tile when compMeter is undefined even with compressorOn=true', () => {
    const t = mountPanel({ ...fullProps, compMeter: undefined, compressorOn: true });
    expect(t.querySelector('[data-meter="comp"]')).toBeNull();
  });

  it('renders all seven tiles when all state fields are defined', () => {
    const t = mountPanel({
      ...fullProps,
      idMeter: 10,
      vdMeter: 13.8,
      compMeter: 15,
      compressorOn: true,
    });
    expect(t.querySelectorAll('.dock-tile')).toHaveLength(7);
    const keys = Array.from(t.querySelectorAll('.dock-tile')).map((el) =>
      el.getAttribute('data-meter'),
    );
    expect(keys).toEqual(['po', 'swr', 'alc', 'id', 'vd', 'comp', 's']);
  });
});

describe('MetersDockPanel relevance dimming (MOR-485 revert of MOR-483 p1)', () => {
  // MOR-483 part-1 HID TX-only tiles on RX, which made the dock layout JUMP on
  // every RX<->TX transition. Reverted to the prior DIMMED behavior: all meter
  // tiles always render; non-relevant ones carry data-relevant='false' (dimmed)
  // but stay in the layout, so switching RX<->TX never reflows the grid.
  it('renders TX-only tiles and marks them relevant when txActive=true', () => {
    const t = mountPanel({
      ...fullProps,
      txActive: true,
      idMeter: 10,
      compMeter: 15,
      compressorOn: true,
    });
    expect(t.querySelector('[data-meter="po"]')?.getAttribute('data-relevant')).toBe('true');
    expect(t.querySelector('[data-meter="swr"]')?.getAttribute('data-relevant')).toBe('true');
    expect(t.querySelector('[data-meter="alc"]')?.getAttribute('data-relevant')).toBe('true');
    expect(t.querySelector('[data-meter="id"]')?.getAttribute('data-relevant')).toBe('true');
    expect(t.querySelector('[data-meter="comp"]')?.getAttribute('data-relevant')).toBe('true');
    // S is the RX indicator — not relevant during TX, but still rendered.
    expect(t.querySelector('[data-meter="s"]')?.getAttribute('data-relevant')).toBe('false');
  });

  it('renders TX-only tiles DIMMED (present, not relevant) when txActive=false', () => {
    const t = mountPanel({
      ...fullProps,
      txActive: false,
      idMeter: 10,
      compMeter: 15,
      compressorOn: true,
    });
    // Tiles stay in the layout (no reflow) but are dimmed via data-relevant.
    expect(t.querySelector('[data-meter="po"]')).not.toBeNull();
    expect(t.querySelector('[data-meter="po"]')?.getAttribute('data-relevant')).toBe('false');
    expect(t.querySelector('[data-meter="swr"]')?.getAttribute('data-relevant')).toBe('false');
    expect(t.querySelector('[data-meter="alc"]')?.getAttribute('data-relevant')).toBe('false');
    expect(t.querySelector('[data-meter="id"]')?.getAttribute('data-relevant')).toBe('false');
    expect(t.querySelector('[data-meter="comp"]')?.getAttribute('data-relevant')).toBe('false');
    // S is the RX indicator — relevant (bright) on RX.
    expect(t.querySelector('[data-meter="s"]')?.getAttribute('data-relevant')).toBe('true');
  });

  it('renders S tile in both RX and TX', () => {
    const rx = mountPanel({ ...fullProps, txActive: false });
    expect(rx.querySelector('[data-meter="s"]')).not.toBeNull();
    expect(rx.querySelector('[data-meter="s"]')?.getAttribute('data-relevant')).toBe('true');
    const tx = mountPanel({ ...fullProps, txActive: true });
    expect(tx.querySelector('[data-meter="s"]')).not.toBeNull();
    expect(tx.querySelector('[data-meter="s"]')?.getAttribute('data-relevant')).toBe('false');
  });

  it('keeps Vd tile relevant in both RX and TX (supply rail always readable)', () => {
    const rx = mountPanel({ ...fullProps, vdMeter: 13.8, txActive: false });
    expect(rx.querySelector('[data-meter="vd"]')).not.toBeNull();
    expect(rx.querySelector('[data-meter="vd"]')?.getAttribute('data-relevant')).toBe('true');
    const tx = mountPanel({ ...fullProps, vdMeter: 13.8, txActive: true });
    expect(tx.querySelector('[data-meter="vd"]')).not.toBeNull();
    expect(tx.querySelector('[data-meter="vd"]')?.getAttribute('data-relevant')).toBe('true');
  });

  it('renders the same tile set on RX and TX (no reflow on transition)', () => {
    const props = {
      ...fullProps,
      idMeter: 10,
      vdMeter: 13.8,
      compMeter: 15,
      compressorOn: true,
    };
    const rxKeys = Array.from(
      mountPanel({ ...props, txActive: false }).querySelectorAll('.dock-tile'),
    ).map((el) => el.getAttribute('data-meter'));
    const txKeys = Array.from(
      mountPanel({ ...props, txActive: true }).querySelectorAll('.dock-tile'),
    ).map((el) => el.getAttribute('data-meter'));
    expect(rxKeys).toEqual(['po', 'swr', 'alc', 'id', 'vd', 'comp', 's']);
    expect(txKeys).toEqual(rxKeys);
  });
});

describe('MetersDockPanel calibrated bar fill (MOR-482)', () => {
  it('fills the SWR bar to half scale at ratio 3.0 (top knot 6.0)', () => {
    // The bar must agree with the calibrated number: ratio / top knot.
    const t = mountPanel({ ...fullProps, txActive: true, swrMeter: 3.0 });
    const fill = t.querySelector('[data-meter="swr"] .tile-bar-fill') as HTMLElement;
    const pct = parseFloat(fill.style.width);
    expect(pct).toBeGreaterThan(45);
    expect(pct).toBeLessThan(55);
  });

  it('fills the SWR bar to ~100% at the off-scale top knot', () => {
    const t = mountPanel({ ...fullProps, txActive: true, swrMeter: 6.0 });
    const fill = t.querySelector('[data-meter="swr"] .tile-bar-fill') as HTMLElement;
    expect(parseFloat(fill.style.width)).toBeGreaterThan(95);
  });

  it('fills the Vd bar near full at the 16 V top knot', () => {
    const t = mountPanel({ ...fullProps, vdMeter: 16, txActive: false });
    const fill = t.querySelector('[data-meter="vd"] .tile-bar-fill') as HTMLElement;
    expect(parseFloat(fill.style.width)).toBeGreaterThan(95);
  });

  it('fills the S bar to ~100% at the strongest calibrated reading', () => {
    const t = mountPanel({ ...fullProps, sValue: 40, txActive: false });
    const fill = t.querySelector('[data-meter="s"] .tile-bar-fill') as HTMLElement;
    expect(parseFloat(fill.style.width)).toBeGreaterThan(99);
  });

  it('places the S bar at the shared S9 position for a calibrated 0 dB reading', () => {
    const t = mountPanel({ ...fullProps, sValue: 0, txActive: false });
    const fill = t.querySelector('[data-meter="s"] .tile-bar-fill') as HTMLElement;
    const pct = parseFloat(fill.style.width);
    expect(pct).toBeGreaterThan(53);
    expect(pct).toBeLessThan(56);
  });
});

describe('MetersDockPanel fault highlighting', () => {
  it('flags SWR tile as fault above ratio 2.0 during TX', () => {
    const t = mountPanel({ ...fullProps, swrMeter: 3.0, txActive: true });
    expect(t.querySelector('[data-meter="swr"]')?.getAttribute('data-fault')).toBe('true');
  });

  it('does not flag SWR fault during RX (tile dimmed, no fault)', () => {
    // SWR is TX-only; on RX the tile is DIMMED (present) and never faulted.
    const t = mountPanel({ ...fullProps, swrMeter: 3.0, txActive: false });
    expect(t.querySelector('[data-meter="swr"]')?.getAttribute('data-fault')).toBe('false');
  });

  it('flags ALC tile as fault above 90% of the redline during TX', () => {
    const t = mountPanel({ ...fullProps, alcMeter: 0.95, txActive: true });
    expect(t.querySelector('[data-meter="alc"]')?.getAttribute('data-fault')).toBe('true');
  });

  it('does not flag ALC fault at exactly 90%', () => {
    const t = mountPanel({ ...fullProps, alcMeter: 0.9, txActive: true });
    expect(t.querySelector('[data-meter="alc"]')?.getAttribute('data-fault')).toBe('false');
  });

  it('does not flag SWR fault at exactly 2.0', () => {
    const t = mountPanel({ ...fullProps, swrMeter: 2.0, txActive: true });
    expect(t.querySelector('[data-meter="swr"]')?.getAttribute('data-fault')).toBe('false');
  });
});

describe('MetersDockPanel peak-hold', () => {
  it('renders a peak marker on Po tile during TX', () => {
    const t = mountPanel({ ...fullProps, txActive: true });
    const marker = t.querySelector('[data-meter="po"] [data-testid="peak-marker"]');
    expect(marker).not.toBeNull();
  });

  it('does not render peak marker on S tile', () => {
    const t = mountPanel({ ...fullProps, txActive: false });
    const marker = t.querySelector('[data-meter="s"] [data-testid="peak-marker"]');
    expect(marker).toBeNull();
  });

  it('hides peak marker when tile is not relevant', () => {
    // Po is dimmed (not relevant) during RX (txActive=false) -> no peak shown.
    const t = mountPanel({ ...fullProps, txActive: false });
    const tile = t.querySelector('[data-meter="po"]');
    expect(tile).not.toBeNull();
    const marker = t.querySelector('[data-meter="po"] [data-testid="peak-marker"]');
    expect(marker).toBeNull();
  });

  it('dblclick reset handler runs on the tile without error', () => {
    const t = mountPanel({ ...fullProps, txActive: true });
    const tile = t.querySelector('[data-meter="po"]') as HTMLElement;
    expect(tile.querySelector('[data-testid="peak-marker"]')).not.toBeNull();
    expect(() => {
      tile.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
      flushSync();
    }).not.toThrow();
  });
});

describe('MetersDockPanel formatted values', () => {
  it('displays Po in watts', () => {
    const t = mountPanel(fullProps);
    expect(t.querySelector('[data-meter="po"] .tile-value')?.textContent).toBe('50W');
  });

  it('displays SWR ratio', () => {
    const t = mountPanel(fullProps);
    expect(t.querySelector('[data-meter="swr"] .tile-value')?.textContent).toBe('1.5');
  });

  it('displays ALC percentage', () => {
    const t = mountPanel(fullProps);
    expect(t.querySelector('[data-meter="alc"] .tile-value')?.textContent).toBe('50%');
  });

  it('displays S-meter as S-units', () => {
    const t = mountPanel(fullProps);
    expect(t.querySelector('[data-meter="s"] .tile-value')?.textContent).toBe('S9');
  });
});

// ---------------------------------------------------------------------------
// Issue #938 — bar-fill smoothing
// ---------------------------------------------------------------------------

describe('createSmoother initial value (issue #938)', () => {
  it('seeds the internal state with the supplied initialValue', () => {
    const s = createSmoother(0.05, 0.15, 42);
    expect(s.value).toBe(42);
  });
});

describe('MetersDockPanel bar-fill smoothing', () => {
  it('starts the Po bar-fill at the live target on the first synchronous render', () => {
    // powerMeter=50 W of 100 W full scale -> fillPct ~50%. With the v2 seed
    // the smoother is initialized at the current target, so the bar-fill
    // width must equal the live fillPct on first paint (no flash to 0).
    // This asserts the seed wires correctly through getSmoother(key, initial).
    const t = mountPanel({ ...fullProps, powerMeter: 50, txActive: true });
    const fill = t.querySelector('[data-meter="po"] .tile-bar-fill') as HTMLElement;
    expect(fill).not.toBeNull();
    const fillPct = parseFloat(fill.style.width);
    expect(fillPct).toBeGreaterThan(40);
    expect(fillPct).toBeLessThan(60);
  });
});
