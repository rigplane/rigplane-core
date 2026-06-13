import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true),
}));
vi.mock('$lib/runtime/adapters/capabilities-adapter', () => ({
  getMeterCalibration: vi.fn(() => null),
  getMeterRedline: vi.fn(() => null),
}));

// ---------------------------------------------------------------------------
// New formatters
// ---------------------------------------------------------------------------

describe('formatAmps', () => {
  it('returns 0.0 A for raw 0', () => {
    expect(formatAmps(0)).toBe('0.0 A');
  });
  it('returns 10.0 A at knot raw=151', () => {
    expect(formatAmps(151)).toBe('10.0 A');
  });
  it('returns 15.0 A at knot raw=195', () => {
    expect(formatAmps(195)).toBe('15.0 A');
  });
  it('clamps raw 300 to last knot (25.0 A)', () => {
    expect(formatAmps(300)).toBe('25.0 A');
  });
});

describe('formatVolts', () => {
  it('returns 0.0 V for raw 0', () => {
    expect(formatVolts(0)).toBe('0.0 V');
  });
  it('returns 10.0 V at knot raw=13', () => {
    expect(formatVolts(13)).toBe('10.0 V');
  });
  it('returns 16.0 V at knot raw=241', () => {
    expect(formatVolts(241)).toBe('16.0 V');
  });
});

describe('formatCompDb', () => {
  it('returns 0 dB for raw 0', () => {
    expect(formatCompDb(0)).toBe('0 dB');
  });
  it('returns 15 dB at knot raw=75', () => {
    expect(formatCompDb(75)).toBe('15 dB');
  });
  it('returns 30 dB at knot raw=150', () => {
    expect(formatCompDb(150)).toBe('30 dB');
  });
});

describe('isSwrFault', () => {
  it('is false at SWR 1.0 (raw=0)', () => {
    expect(isSwrFault(0)).toBe(false);
  });
  it('is false at SWR exactly 2.0 (raw=80)', () => {
    expect(isSwrFault(80)).toBe(false);
  });
  it('is true above 2.0 (raw=120 -> 3.0)', () => {
    expect(isSwrFault(120)).toBe(true);
  });
  it('is true at raw=255 (infinity)', () => {
    expect(isSwrFault(255)).toBe(true);
  });
});

describe('isAlcFault', () => {
  it('is false at 0% ALC', () => {
    expect(isAlcFault(0)).toBe(false);
  });
  it('is false at 90% ALC (raw=108, redline 120)', () => {
    expect(isAlcFault(108)).toBe(false);
  });
  it('is true above 90% ALC (raw=115)', () => {
    expect(isAlcFault(115)).toBe(true);
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

const fullProps: ComponentProps<typeof MetersDockPanel> = {
  sValue: 0,
  powerMeter: 143,
  swrMeter: 48,
  alcMeter: 60,
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
    const t = mountPanel({ ...fullProps, idMeter: 151 });
    const tile = t.querySelector('[data-meter="id"]');
    expect(tile).not.toBeNull();
    expect(tile?.querySelector('.tile-value')?.textContent).toBe('10.0 A');
  });

  it('hides Id tile when idMeter is undefined', () => {
    const t = mountPanel({ ...fullProps, idMeter: undefined });
    expect(t.querySelector('[data-meter="id"]')).toBeNull();
  });

  it('renders Vd tile when vdMeter is defined', () => {
    const t = mountPanel({ ...fullProps, vdMeter: 13 });
    const tile = t.querySelector('[data-meter="vd"]');
    expect(tile).not.toBeNull();
    expect(tile?.querySelector('.tile-value')?.textContent).toBe('10.0 V');
  });

  it('hides Vd tile when vdMeter is undefined', () => {
    const t = mountPanel({ ...fullProps, vdMeter: undefined });
    expect(t.querySelector('[data-meter="vd"]')).toBeNull();
  });

  it('renders COMP tile when compMeter is defined and compressorOn=true', () => {
    const t = mountPanel({ ...fullProps, compMeter: 75, compressorOn: true });
    const tile = t.querySelector('[data-meter="comp"]');
    expect(tile).not.toBeNull();
    expect(tile?.querySelector('.tile-value')?.textContent).toBe('15 dB');
  });

  it('hides COMP tile when compressorOn is false', () => {
    const t = mountPanel({ ...fullProps, compMeter: 75, compressorOn: false });
    expect(t.querySelector('[data-meter="comp"]')).toBeNull();
  });

  it('hides COMP tile when compressorOn is undefined (gating)', () => {
    const t = mountPanel({ ...fullProps, compMeter: 75 });
    expect(t.querySelector('[data-meter="comp"]')).toBeNull();
  });

  it('hides COMP tile when compMeter is undefined even with compressorOn=true', () => {
    const t = mountPanel({ ...fullProps, compMeter: undefined, compressorOn: true });
    expect(t.querySelector('[data-meter="comp"]')).toBeNull();
  });

  it('renders all seven tiles when all state fields are defined', () => {
    const t = mountPanel({
      ...fullProps,
      idMeter: 100,
      vdMeter: 13,
      compMeter: 75,
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
      idMeter: 100,
      compMeter: 75,
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
      idMeter: 100,
      compMeter: 75,
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
    const rx = mountPanel({ ...fullProps, vdMeter: 180, txActive: false });
    expect(rx.querySelector('[data-meter="vd"]')).not.toBeNull();
    expect(rx.querySelector('[data-meter="vd"]')?.getAttribute('data-relevant')).toBe('true');
    const tx = mountPanel({ ...fullProps, vdMeter: 180, txActive: true });
    expect(tx.querySelector('[data-meter="vd"]')).not.toBeNull();
    expect(tx.querySelector('[data-meter="vd"]')?.getAttribute('data-relevant')).toBe('true');
  });

  it('renders the same tile set on RX and TX (no reflow on transition)', () => {
    const props = {
      ...fullProps,
      idMeter: 100,
      vdMeter: 13,
      compMeter: 75,
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
  it('fills the SWR bar to ~100% at SWR 3.0 (raw=120), not ~47%', () => {
    // The bar must agree with the calibrated number, not raw/255.
    const t = mountPanel({ ...fullProps, txActive: true, swrMeter: 120 });
    const fill = t.querySelector('[data-meter="swr"] .tile-bar-fill') as HTMLElement;
    expect(parseFloat(fill.style.width)).toBeGreaterThan(95);
  });

  it('fills the Vd bar near full at the 16 V knot (raw=241), not ~5%', () => {
    const t = mountPanel({ ...fullProps, vdMeter: 241, txActive: false });
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
  it('flags SWR tile as fault when raw > 2.0 during TX', () => {
    const t = mountPanel({ ...fullProps, swrMeter: 120, txActive: true });
    expect(t.querySelector('[data-meter="swr"]')?.getAttribute('data-fault')).toBe('true');
  });

  it('does not flag SWR fault during RX (tile dimmed, no fault)', () => {
    // SWR is TX-only; on RX the tile is DIMMED (present) and never faulted.
    const t = mountPanel({ ...fullProps, swrMeter: 120, txActive: false });
    expect(t.querySelector('[data-meter="swr"]')?.getAttribute('data-fault')).toBe('false');
  });

  it('flags ALC tile as fault when raw above 90% of redline during TX', () => {
    const t = mountPanel({ ...fullProps, alcMeter: 115, txActive: true });
    expect(t.querySelector('[data-meter="alc"]')?.getAttribute('data-fault')).toBe('true');
  });

  it('does not flag ALC fault at exactly 90%', () => {
    const t = mountPanel({ ...fullProps, alcMeter: 108, txActive: true });
    expect(t.querySelector('[data-meter="alc"]')?.getAttribute('data-fault')).toBe('false');
  });

  it('does not flag SWR fault at exactly 2.0', () => {
    const t = mountPanel({ ...fullProps, swrMeter: 80, txActive: true });
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
  it('starts the Po bar-fill at the raw target on the first synchronous render', () => {
    // powerMeter=128 -> raw fillPct ~50%. With the v2 seed the smoother is
    // initialized at the current target, so the bar-fill width must equal
    // the raw fillPct on first paint (no flash to 0). This asserts the seed
    // wires correctly through getSmoother(key, initial).
    const t = mountPanel({ ...fullProps, powerMeter: 128, txActive: true });
    const fill = t.querySelector('[data-meter="po"] .tile-bar-fill') as HTMLElement;
    expect(fill).not.toBeNull();
    const fillPct = parseFloat(fill.style.width);
    // 128/255 * 100 ≈ 50.2%. Allow a small tolerance for floating-point.
    expect(fillPct).toBeGreaterThan(40);
    expect(fillPct).toBeLessThan(60);
  });
});
