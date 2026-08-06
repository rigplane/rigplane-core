import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import BarGauge from '../BarGauge.svelte';

// MOR-1282: BarGauge's optional peak-hold marker channel. Reuses
// meter-utils::updatePeakHold/peakHoldDisplay — the single MOR-1252 semantics
// implementation MetersDockPanel already channels through (see
// MetersDockPanel.peakhold.svelte.test.ts) — so the marker here and the
// dock's own held Po/SWR/ALC/Id readouts can never disagree about hold/decay
// timing. These tests drive the 100 ms decay ticker with fake timers, mirroring
// the dock's own test convention.

let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

beforeEach(() => {
  components = [];
  roots = [];
  vi.useFakeTimers();
  vi.setSystemTime(0);
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  roots.forEach((r) => r.remove());
  components = [];
  roots = [];
  vi.useRealTimers();
});

function mountReactive(props: Record<string, unknown>) {
  const state = $state(props);
  const t = document.createElement('div');
  document.body.appendChild(t);
  roots.push(t);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component = mount(BarGauge as any, { target: t, props: state });
  flushSync();
  components.push(component);
  return { t, state, component };
}

// Asserting on NodeList length (not `querySelector(...) + toBeNull()`) sidesteps
// a Svelte dev-mode "rune used outside .svelte" red herring vitest's
// failure-message pretty-printer trips when it inspects a live mounted node —
// the same convention `LinearSMeter.reduced-motion.svelte.test.ts` documents.
function markerCount(t: HTMLElement): number {
  return t.querySelectorAll('[data-testid="bar-gauge-peak-marker"]').length;
}

function markerX(t: HTMLElement): number | null {
  const el = t.querySelector('[data-testid="bar-gauge-peak-marker"]');
  const x = el?.getAttribute('x');
  return x === null || x === undefined ? null : parseFloat(x);
}

describe('BarGauge peak-hold marker (MOR-1282)', () => {
  it('renders no marker when showPeak is not set', () => {
    const { t } = mountReactive({ value: 0.8, label: 'Po', displayValue: '80W' });
    expect(markerCount(t)).toBe(0);
  });

  it('renders a marker at the latched peak once showPeak is set', () => {
    const { t } = mountReactive({ value: 0.8, label: 'Po', displayValue: '80W', showPeak: true });
    vi.advanceTimersByTime(100);
    flushSync();
    expect(markerX(t)).not.toBeNull();
  });

  it('holds the marker near the prior peak through a drop, then decays back', () => {
    const { t, state } = mountReactive({
      value: 1.0, label: 'Po', displayValue: '100W', showPeak: true,
    });
    vi.advanceTimersByTime(100);
    flushSync();
    const peakX = markerX(t) as number;
    expect(peakX).not.toBeNull();

    // Drop the live value; shortly after, the marker must still sit well
    // above the live trough (~44 + 0.05*210 ≈ 55), proving it is held near
    // the peak rather than tracking the drop instantaneously.
    state.value = 0.05;
    vi.advanceTimersByTime(100);
    flushSync();
    const heldX = markerX(t) as number;
    expect(heldX).toBeGreaterThan(150);

    // Advance past the full decay window — the marker must settle near the
    // live trough, not the stale peak.
    vi.advanceTimersByTime(1600);
    flushSync();
    const settledX = markerX(t) as number;
    expect(settledX).toBeLessThan(70);
    expect(settledX).toBeLessThan(peakX);
  });

  it('double-click resets the held peak', () => {
    const { t } = mountReactive({
      value: 1.0, label: 'Po', displayValue: '100W', showPeak: true,
    });
    vi.advanceTimersByTime(100);
    flushSync();
    expect(markerCount(t)).toBe(1);

    t.querySelector('svg')!.dispatchEvent(new Event('dblclick', { bubbles: true }));
    flushSync();
    expect(markerCount(t)).toBe(0);
  });
});
