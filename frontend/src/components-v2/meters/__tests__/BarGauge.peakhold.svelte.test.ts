import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import BarGauge from '../BarGauge.svelte';
import type {
  MeterContinuitySession,
  MeterSourceIdentity,
} from '../../../primitives/meters/meter-ballistics.svelte';

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

function fillCount(t: HTMLElement): number {
  return t.querySelectorAll('[data-gauge-fill]').length;
}

const MAIN_SOURCE = {
  providerGeneration: 1, scope: 'receiver', receiver: 'MAIN', path: 'main.sMeter',
} as const satisfies MeterSourceIdentity;
const SESSION_1 = { controlSessionEpoch: 1 } as const satisfies MeterContinuitySession;

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

it('null empties immediately and clears old fill/peak before a smaller sample', () => {
  const { t, state } = mountReactive({ value: 1, label: 'Po', displayValue: '100W', showPeak: true, fault: true });
  vi.advanceTimersByTime(600);
  flushSync();
  const svg = t.querySelector('svg');
  const track = t.querySelector('rect[x="44"]');
  expect(t.querySelectorAll('rect').length).toBeGreaterThan(13);
  expect(svg?.getAttribute('data-fault')).toBe('true');
  state.value = null;
  state.displayValue = 'IDLE';
  state.showPeak = false;
  flushSync();
  expect(t.querySelector('svg') === svg).toBe(true);
  expect(t.querySelector('rect[x="44"]') === track).toBe(true);
  expect(t.querySelectorAll('rect')).toHaveLength(12);
  expect(svg?.getAttribute('data-fault')).toBe('false');
  expect(markerCount(t)).toBe(0);
  state.value = 0.1;
  state.displayValue = '10W';
  state.showPeak = true;
  flushSync();
  expect(markerX(t)).toBe(64);
  expect(t.querySelectorAll('rect')).toHaveLength(13);
  vi.advanceTimersByTime(100);
  flushSync();
  expect(t.querySelectorAll('rect').length).toBeLessThanOrEqual(14);
});

describe('BarGauge source continuity (MOR-2402)', () => {
  const highThenLow = (source: MeterSourceIdentity = MAIN_SOURCE) => {
    const mounted = mountReactive({
      value: 1, label: 'Po', displayValue: '100W', showPeak: true,
      source, session: SESSION_1,
    });
    vi.advanceTimersByTime(600);
    flushSync();
    const retainedFill = fillCount(mounted.t);
    expect(retainedFill).toBeGreaterThan(5);
    mounted.state.value = 0.1;
    mounted.state.displayValue = '10W';
    flushSync();
    expect(fillCount(mounted.t)).toBe(retainedFill);
    const retainedPeak = markerX(mounted.t)!;
    expect(retainedPeak).toBeGreaterThan(64);
    return { ...mounted, retainedFill, retainedPeak };
  };

  it('keeps omitted context legacy-compatible, then clears at legacy/qualified boundaries', () => {
    const { t, state } = mountReactive({
      value: 1, label: 'Po', displayValue: '100W', showPeak: true,
    });
    vi.advanceTimersByTime(600);
    flushSync();
    const retainedFill = fillCount(t);
    expect(retainedFill).toBeGreaterThan(5);
    state.value = 0.1;
    state.displayValue = '10W';
    flushSync();
    expect(fillCount(t)).toBe(retainedFill);
    const retainedPeak = markerX(t)!;
    expect(retainedPeak).toBeGreaterThan(64);

    state.source = MAIN_SOURCE;
    state.session = SESSION_1;
    flushSync();
    expect(fillCount(t)).toBe(1);
    expect(markerX(t)).toBe(64);

    state.value = 1;
    flushSync();
    state.value = 0.1;
    flushSync();
    expect(markerX(t)).toBeGreaterThan(64);
    state.source = undefined;
    state.session = undefined;
    flushSync();
    expect(fillCount(t)).toBe(1);
    expect(markerX(t)).toBe(64);
  });

  it('treats reconstructed equal tuples as the same source and observes the equal sample', () => {
    const { t, state, retainedFill, retainedPeak } = highThenLow();
    state.source = { ...MAIN_SOURCE };
    state.session = { ...SESSION_1 };
    flushSync();
    expect(fillCount(t)).toBe(retainedFill);
    expect(markerX(t)).toBe(retainedPeak);
  });

  it.each([
    ['provider generation', { ...MAIN_SOURCE, providerGeneration: 2 }, SESSION_1],
    ['scope', { ...MAIN_SOURCE, scope: 'radio' }, SESSION_1],
    ['receiver', { ...MAIN_SOURCE, receiver: 'SUB' }, SESSION_1],
    ['path', { ...MAIN_SOURCE, path: 'sub.sMeter' }, SESSION_1],
    ['control-session epoch', MAIN_SOURCE, { controlSessionEpoch: 2 }],
  ] as readonly [string, MeterSourceIdentity, MeterContinuitySession][])(
    're-seeds segment fill and raw-sample peak when only %s changes',
    (_label, nextSource, nextSession) => {
      const { t, state } = highThenLow();
      state.source = nextSource;
      state.session = nextSession;
      flushSync();
      expect(fillCount(t)).toBe(1);
      expect(markerX(t)).toBe(64);
    },
  );

  it.each([
    ['source', null, undefined],
    ['session', undefined, null],
  ] as const)('gives explicit null %s precedence over an omitted peer', (_label, source, session) => {
    const { t, state } = highThenLow();
    state.source = source;
    state.session = session;
    flushSync();
    expect(fillCount(t)).toBe(0);
    expect(markerCount(t)).toBe(0);
  });
});
