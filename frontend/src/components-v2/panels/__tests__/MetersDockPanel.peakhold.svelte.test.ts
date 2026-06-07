import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import MetersDockPanel from '../MetersDockPanel.svelte';

// Peak-hold display ballistics (MOR-498). On a real radio the Po/ALC/SWR/Id
// meters peak on each voice syllable and decay slowly; the web meter must do
// the same so the displayed NUMBER does not collapse to the inter-syllable
// trough. These tests drive the 100 ms decay ticker with fake timers and a
// controllable Date.now so the held value is deterministic.
//
// The fill width goes through an rAF-driven smoother (disabled under fake
// timers); the NUMBER is computed directly from the held raw, so we assert on
// the NUMBER here. The fill<->held-raw coupling is covered by the pure
// composition test in meter-utils.test.ts.

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true),
}));
vi.mock('$lib/runtime/adapters/capabilities-adapter', () => ({
  getMeterCalibration: vi.fn(() => null),
  getMeterRedline: vi.fn(() => null),
}));

let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

beforeEach(() => {
  components = [];
  roots = [];
  vi.useFakeTimers();
  // Anchor wall-clock time; stepAllPeaks() reads Date.now() for latch/decay.
  vi.setSystemTime(0);
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  roots.forEach((r) => r.remove());
  components = [];
  roots = [];
  vi.useRealTimers();
});

// A $state props proxy so prop mutations re-render the mounted component.
function mountReactive(props: Record<string, unknown>) {
  const state = $state(props);
  const t = document.createElement('div');
  document.body.appendChild(t);
  roots.push(t);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component = mount(MetersDockPanel as any, { target: t, props: state });
  flushSync();
  components.push(component);
  return { t, state };
}

function poNumber(t: HTMLElement): string | null | undefined {
  return t.querySelector('[data-meter="po"] .tile-value')?.textContent;
}

describe('MetersDockPanel TX peak-hold ballistics (MOR-498)', () => {
  it('holds the Po NUMBER near a prior peak through the inter-syllable gap', () => {
    // raw 212 -> 100W (a voice peak); raw 5 -> ~2W (an inter-syllable trough).
    const { t, state } = mountReactive({ powerMeter: 212, txActive: true });
    // Latch the peak on the first tick.
    vi.advanceTimersByTime(100);
    flushSync();
    expect(poNumber(t)).toBe('100W');

    // Signal drops into a gap. Without peak-hold the number would immediately
    // read the trough (~2W); with peak-hold it must still read close to peak
    // shortly after the drop (well within the ~1.5 s decay window).
    state.powerMeter = 5;
    vi.advanceTimersByTime(100); // t=200ms, ~133ms into decay of a 1500ms window
    flushSync();
    const held = parseInt(poNumber(t) ?? '0', 10);
    // The instantaneous trough is ~2W; the held value is still near the 100W
    // peak. The exact figure tracks the linear raw decay (~80W here), so we
    // assert it is far above the trough rather than pinning the boundary.
    expect(held).toBeGreaterThan(70);
  });

  it('decays the held Po NUMBER back to the live trough after ~1.5 s', () => {
    const { t, state } = mountReactive({ powerMeter: 212, txActive: true });
    vi.advanceTimersByTime(100); // latch peak at t=100
    flushSync();
    expect(poNumber(t)).toBe('100W');

    state.powerMeter = 5; // drop to trough
    // Advance past the 1.5 s decay window (relative to the latch at t=100).
    vi.advanceTimersByTime(1600); // t=1700ms
    flushSync();
    const settled = parseInt(poNumber(t) ?? '0', 10);
    // Fully decayed: reads the live trough (~2W), not the stale 100W peak.
    expect(settled).toBeLessThan(10);
  });

  it('stays at 0W on RX (no stale TX peak bleed)', () => {
    // Idle/RX: raw stays 0 -> 0W held value never rises.
    const { t } = mountReactive({ powerMeter: 0, txActive: false });
    vi.advanceTimersByTime(300);
    flushSync();
    expect(poNumber(t)).toBe('0W');
  });

  it('holds the Id NUMBER near a prior peak through a gap', () => {
    // raw 212 -> 25.0 A peak; raw 0 -> 0.0 A trough.
    const { t, state } = mountReactive({
      powerMeter: 0,
      idMeter: 212,
      txActive: true,
    });
    vi.advanceTimersByTime(100);
    flushSync();
    expect(t.querySelector('[data-meter="id"] .tile-value')?.textContent).toBe('25.0 A');

    state.idMeter = 0;
    vi.advanceTimersByTime(100); // shortly into decay
    flushSync();
    const amps = parseFloat(
      t.querySelector('[data-meter="id"] .tile-value')?.textContent ?? '0',
    );
    // Live trough is 0.0 A; the held value stays well above it during the
    // hold window (the Id calibration curve is compressed near the top, so the
    // held raw maps to ~14 A here — still far above the 0 A trough).
    expect(amps).toBeGreaterThan(10);
  });

  it('leaves the Vd NUMBER instantaneous (no peak-hold on the supply rail)', () => {
    const { t, state } = mountReactive({
      powerMeter: 0,
      vdMeter: 241, // 16.0 V
      txActive: false,
    });
    vi.advanceTimersByTime(100);
    flushSync();
    expect(t.querySelector('[data-meter="vd"] .tile-value')?.textContent).toBe('16.0 V');

    // A drop in supply voltage must be reflected immediately, not held.
    state.vdMeter = 184; // 13.8 V
    vi.advanceTimersByTime(100);
    flushSync();
    expect(t.querySelector('[data-meter="vd"] .tile-value')?.textContent).toBe('13.8 V');
  });
});
