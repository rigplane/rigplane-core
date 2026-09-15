/**
 * MOR-2250 (PR 2 of 2) — `LinearSMeter`'s optional `lowerScale` prop: a
 * second, generic scale row below the bar. The pin that matters most is the
 * layout-stability one (last `it` below): the row's label/ticks must render
 * identically whether the reading is zero (RX/idle) or full-scale (TX) — only
 * the FILL and fault color may depend on the reading. If the row's structural
 * elements depended on `valueFraction`, the tile would change height crossing
 * RX/TX, which is exactly what the real IC-7300's shared bar does NOT do (its
 * bottom row is visible-but-empty while receiving).
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { ComponentProps } from 'svelte';
import LinearSMeter, { type LowerScaleDescriptor } from '../LinearSMeter.svelte';

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

const SWR_TICKS = [
  { value: 0, label: '1' },
  { value: 0.2, label: '1.5' },
  { value: 0.4, label: '2' },
  { value: 0.6, label: '2.5' },
  { value: 0.8, label: '3' },
  { value: 1, label: '∞' },
] as const;

function descriptor(over: Partial<LowerScaleDescriptor> = {}): LowerScaleDescriptor {
  return { label: 'SWR', ticks: SWR_TICKS, valueFraction: 0, fault: false, relevant: true, ...over };
}

function lowerSegs(target: HTMLElement) {
  return Array.from(target.querySelectorAll('[data-lower-segment]'));
}
function lowerFills(target: HTMLElement) {
  return Array.from(target.querySelectorAll('[data-lower-fill]')) as SVGRectElement[];
}
function viewBoxOf(target: HTMLElement): string {
  return target.querySelector('svg')!.getAttribute('viewBox')!;
}

describe('LinearSMeter lowerScale prop — absent by default', () => {
  it('renders no lower-scale elements at all when the prop is not passed', () => {
    const target = mountMeter({ value: 0 });
    expect(lowerSegs(target)).toHaveLength(0);
    expect(target.querySelector('[data-lower-row-label]')).toBeNull();
    expect(target.querySelector('svg')!.hasAttribute('data-lower-fault')).toBe(false);
  });
});

describe('LinearSMeter lowerScale prop — structural elements', () => {
  it('renders the row label and every tick label text', () => {
    const target = mountMeter({ value: 0, lowerScale: descriptor() });
    expect(target.querySelector('[data-lower-row-label]')?.textContent).toBe('SWR');
    for (const t of SWR_TICKS) {
      expect(target.querySelector(`[data-lower-tick-label="${t.value}"]`)?.textContent).toBe(t.label);
      expect(target.querySelector(`[data-lower-tick-mark="${t.value}"]`)).not.toBeNull();
    }
  });

  it('appends the unit, when supplied, to the row label', () => {
    const target = mountMeter({ value: 0, lowerScale: descriptor({ unit: 'W' }) });
    expect(target.querySelector('[data-lower-row-label]')?.textContent).toBe('SWR W');
  });

  it('renders exactly the default 20 lower-row background segments, mirroring the main bar', () => {
    const target = mountMeter({ value: 0, lowerScale: descriptor() });
    expect(lowerSegs(target)).toHaveLength(20);
  });
});

describe('LinearSMeter lowerScale prop — fill tracks valueFraction', () => {
  it('lights zero fill segments at valueFraction: 0', () => {
    const target = mountMeter({ value: 0, lowerScale: descriptor({ valueFraction: 0 }) });
    expect(lowerFills(target)).toHaveLength(0);
  });

  it('lights exactly floor(valueFraction * 20) full segments plus one partial for the remainder', () => {
    // 0.53 * 20 = 10.6 -> 10 full segments (indices 0..9) + 1 partial (index 10) = 11 fill rects.
    const target = mountMeter({ value: 0, lowerScale: descriptor({ valueFraction: 0.53 }) });
    const fills = lowerFills(target);
    expect(fills).toHaveLength(11);
    // The partial segment's width is narrower than a full segment's.
    const widths = fills.map((r) => Number(r.getAttribute('width')));
    expect(widths[10]).toBeLessThan(widths[0]);
  });

  it('lights all 20 segments at valueFraction: 1', () => {
    const target = mountMeter({ value: 0, lowerScale: descriptor({ valueFraction: 1 }) });
    expect(lowerFills(target)).toHaveLength(20);
  });
});

describe('LinearSMeter lowerScale prop — fault color', () => {
  it('fills with the normal active color when fault is false', () => {
    const target = mountMeter({ value: 0, lowerScale: descriptor({ valueFraction: 0.5, fault: false }) });
    const [fill] = lowerFills(target);
    expect(fill.getAttribute('fill')).toBe('var(--v2-accent-green-medium)');
    expect(target.querySelector('svg')!.getAttribute('data-lower-fault')).toBe('false');
  });

  it('fills with the fault color (BarGauge\'s own literal) when fault is true', () => {
    const target = mountMeter({ value: 0, lowerScale: descriptor({ valueFraction: 0.5, fault: true }) });
    const [fill] = lowerFills(target);
    expect(fill.getAttribute('fill')).toBe('var(--v2-accent-red, #ff4040)');
    expect(target.querySelector('svg')!.getAttribute('data-lower-fault')).toBe('true');
  });
});

describe('LinearSMeter lowerScale prop — relevance dims the row, not the tile (MOR-2250 fix cycle)', () => {
  // MUTATION KILLED: reading the S-meter tile's own `data-relevant` (a
  // different field, set from `meters.signal.relevant` in the caller)
  // instead of `lowerScale.relevant` — this component has no access to the
  // tile's attribute at all, only the descriptor field, so a mutant that
  // hardcodes the group's opacity to 1 regardless of `relevant` is the one
  // this test kills.
  it('dims the lower row\'s own <g> to the same 0.4 opacity the tile uses, and marks data-lower-relevant=false, when relevant is false', () => {
    const target = mountMeter({ value: 0, lowerScale: descriptor({ relevant: false, valueFraction: 0.5 }) });
    const group = target.querySelector('[data-lower-relevant]')!;
    expect(group.getAttribute('data-lower-relevant')).toBe('false');
    expect(group.getAttribute('opacity')).toBe('0.4');
  });

  it('renders the lower row at full opacity and data-lower-relevant=true when relevant is true', () => {
    const target = mountMeter({ value: 0, lowerScale: descriptor({ relevant: true, valueFraction: 0.5 }) });
    const group = target.querySelector('[data-lower-relevant]')!;
    expect(group.getAttribute('data-lower-relevant')).toBe('true');
    expect(group.getAttribute('opacity')).toBe('1');
  });

  // The row's structural elements (label/ticks) must still be present and
  // identical under a dim — MOR-2250's layout-stability ruling is about
  // geometry, not opacity; dimming must not also hide anything the
  // structural-elements test above already pins for the bright case.
  it('keeps the row\'s label and ticks present, only dimmed, while irrelevant', () => {
    const target = mountMeter({ value: 0, lowerScale: descriptor({ relevant: false }) });
    expect(target.querySelector('[data-lower-row-label]')?.textContent).toBe('SWR');
    expect(lowerSegs(target)).toHaveLength(20);
  });
});

describe('LinearSMeter lowerScale prop — layout stability across RX/TX (MOR-2250 owner ruling)', () => {
  it("renders the lower scale's label and ticks even when there is no reading (RX), so the tile does not change height crossing RX/TX", () => {
    const rx = mountMeter({ value: 0, lowerScale: descriptor({ valueFraction: 0, fault: false }) });
    const tx = mountMeter({ value: 0, lowerScale: descriptor({ valueFraction: 1, fault: true }) });

    // Structural elements present in BOTH states, byte-identical text.
    expect(rx.querySelector('[data-lower-row-label]')?.textContent).toBe('SWR');
    expect(tx.querySelector('[data-lower-row-label]')?.textContent).toBe('SWR');
    for (const t of SWR_TICKS) {
      expect(rx.querySelector(`[data-lower-tick-label="${t.value}"]`)?.textContent).toBe(t.label);
      expect(tx.querySelector(`[data-lower-tick-label="${t.value}"]`)?.textContent).toBe(t.label);
    }
    expect(lowerSegs(rx)).toHaveLength(20);
    expect(lowerSegs(tx)).toHaveLength(20);

    // The one thing that DOES vary: the fill count and color.
    expect(lowerFills(rx)).toHaveLength(0);
    expect(lowerFills(tx)).toHaveLength(20);

    // The tile's own geometry (viewBox height) is identical in both states —
    // this is the layout-stability property itself, not just its structural
    // symptom above.
    expect(viewBoxOf(rx)).toBe(viewBoxOf(tx));
  });
});

it.each([true, false])('retains a lower descriptor without inventing a main reading (mainPresent=%s)', (mainPresent) => {
  const target = mountMeter({ value: null, mainPresent, lowerScale: descriptor({
    stateText: 'IDLE', accessibleDescription: 'Not measuring in RX',
  }) });
  expect(lowerSegs(target)).toHaveLength(20);
  expect(target.querySelector('[data-lower-relevant]')?.getAttribute('aria-label')).toBe('Not measuring in RX');
  expect(target.textContent).toContain('IDLE');
  expect(target.textContent).not.toMatch(/dBm|uncalibrated/);
  expect(target.querySelectorAll('[data-main-relevant]')).toHaveLength(mainPresent ? 2 : 0);
  if (mainPresent) expect(target.textContent).toContain('S ?');
});

it('null clears normal-motion main fill and peak without remounting the lower scale', () => {
  vi.useFakeTimers();
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = vi.fn().mockReturnValue({ matches: false });
  const props: ComponentProps<typeof LinearSMeter> = proxy({ value: 255, lowerScale: descriptor() });
  try {
    const target = mountMeter(props);
    vi.advanceTimersByTime(600);
    flushSync();
    const svg = target.querySelector('svg');
    const lower = target.querySelector('[data-lower-tick-mark]');
    const main = () => target.querySelector('[data-main-relevant]')!;
    const lit = () => main().querySelectorAll('rect:not([data-segment])').length - 2;
    expect(lit()).toBeGreaterThan(10);
    props.value = null;
    flushSync();
    expect(lit()).toBe(0);
    expect(target.textContent).not.toContain('uncalibrated');
    props.value = 10;
    flushSync();
    expect(lit()).toBe(0);
    expect(target.querySelectorAll('line[stroke-width="2"]')).toHaveLength(0);
    expect(target.querySelector('svg') === svg).toBe(true);
    expect(target.querySelector('[data-lower-tick-mark]') === lower).toBe(true);
  } finally {
    components.forEach((component) => unmount(component));
    components = [];
    vi.useRealTimers(); window.matchMedia = originalMatchMedia; vi.restoreAllMocks();
  }
});
