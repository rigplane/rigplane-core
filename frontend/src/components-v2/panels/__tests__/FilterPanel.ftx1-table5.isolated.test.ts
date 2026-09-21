/**
 * MOR-1679 — FTX-1 filter-width rendering and write emission in
 * FilterPanel, pinned against the GENERATED capabilities fixture (see
 * `lib/runtime/adapters/__tests__/fixtures/ftx1-profile.ts`). Table 5
 * semantics: only table entries are ever emitted; bounds are the table's
 * first and last entry; fixed modes are not operable; a null width reads
 * as the existing '--- Hz' unavailable treatment.
 *
 * Same harness shape as `FilterPanel.isolated.test.ts` (module-scope
 * panel-adapters mock), which is why this file is isolated-pool.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import { setLocale } from '$lib/i18n';
import type { CommandScalarFeedback } from '../../../primitives/scalar/continuous-scalar.svelte';
import type { FilterModeConfig } from '$lib/types/capabilities';
import { FTX1_CAPABILITIES } from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';

const ftx1FilterConfig = FTX1_CAPABILITIES.filterConfig ?? {};

const mockProps = {
  currentMode: 'USB',
  currentFilter: 1,
  filterShape: 0,
  hasFilterShape: false,
  filterLabels: FTX1_CAPABILITIES.filters ?? [],
  filterWidth: 2400 as number,
  filterWidthMin: FTX1_CAPABILITIES.filterWidthMin ?? 50,
  filterWidthMax: FTX1_CAPABILITIES.filterWidthMax ?? 4000,
  filterConfig: (ftx1FilterConfig.SSB ?? null) as FilterModeConfig | null,
  ifShift: 0,
  hasIfShift: true,
  hasPbt: false,
  pbtInner: null as number | null,
  pbtOuter: null as number | null,
};

const mockHandlers = {
  onFilterChange: vi.fn(),
  onFilterWidthChange: vi.fn(),
  onFilterShapeChange: vi.fn(),
  onFilterPresetChange: vi.fn(),
  onFilterDefaults: vi.fn(),
  onIfShiftChange: vi.fn(),
  onPbtInnerChange: vi.fn(),
  onPbtOuterChange: vi.fn(),
  onPbtReset: vi.fn(),
};

const propsVersion = new SvelteMap([['value', 0]]);
const widthFeedbackStore = new SvelteMap([['value', null as Readonly<CommandScalarFeedback> | null]]);

function makeFeedback(control: string, confirmed: number): Readonly<CommandScalarFeedback> {
  propsVersion.get('value');
  return {
    confirmed, target: null, requestedTarget: null, phase: 'idle', busy: false,
    availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration: 1, sessionEpoch: 7,
    scope: { control, receiver: 0 }, repeatPolicy: 'latest-target-wins',
  } as Readonly<CommandScalarFeedback>;
}

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveFilterProps: () => {
    propsVersion.get('value');
    return { ...mockProps };
  },
  getFilterHandlers: () => mockHandlers,
  getFilterArmed: () => ({ armed: false, value: null }),
  getFilterWidthControlFeedback: () =>
    widthFeedbackStore.get('value') ?? makeFeedback('filter-width', mockProps.filterWidth),
  getPbtInnerHzControlFeedback: () => makeFeedback('pbt-inner', mockProps.pbtInner ?? 0),
  getPbtOuterHzControlFeedback: () => makeFeedback('pbt-outer', mockProps.pbtOuter ?? 0),
  getIfShiftControlFeedback: () => makeFeedback('if-shift', mockProps.ifShift),
}));

import FilterPanel from '../FilterPanel.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountPanel(): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(FilterPanel, { target }));
  flushSync();
  return target;
}

function setProps(overrides: Partial<typeof mockProps>): void {
  Object.assign(mockProps, overrides);
  if ('filterWidth' in overrides) {
    widthFeedbackStore.set('value', makeFeedback('filter-width', overrides.filterWidth as number));
  }
  propsVersion.set('value', (propsVersion.get('value') ?? 0) + 1);
  flushSync();
}

function widthSlider(t: HTMLElement): HTMLElement {
  const slider = t.querySelector<HTMLElement>('[role="slider"]');
  if (!slider) throw new Error('width slider did not mount');
  return slider;
}

function pressKey(t: HTMLElement, key: string): void {
  widthSlider(t).dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
}

beforeEach(() => {
  vi.useFakeTimers();
  components = [];
  setLocale('en-US');
  mockHandlers.onFilterWidthChange = vi.fn();
  mockHandlers.onIfShiftChange = vi.fn();
  setProps({
    currentMode: 'USB',
    currentFilter: 1,
    filterWidth: 2400,
  filterConfig: (ftx1FilterConfig.SSB ?? null) as FilterModeConfig | null,
    hasIfShift: true,
  });
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
  vi.useRealTimers();
});

describe('MOR-1679 FTX-1 table-mode width quantization (Table 5)', () => {
  it('bounds the WIDTH slider to the SSB table ends 300..4000, never 4500', () => {
    const t = mountPanel();
    const slider = widthSlider(t);
    expect(slider.getAttribute('aria-valuemin')).toBe('300');
    expect(slider.getAttribute('aria-valuemax')).toBe('4000');
    expect(slider.getAttribute('aria-valuemax')).not.toBe('4500');
  });

  it('USB stepping up from 3000 emits 3200', () => {
    setProps({ filterWidth: 3000 });
    const t = mountPanel();
    pressKey(t, 'ArrowRight');
    vi.advanceTimersByTime(60);
    expect(mockHandlers.onFilterWidthChange).toHaveBeenCalledWith(3200);
  });

  it('USB stepping up from the 4000 table end emits no write at all, never 4500', () => {
    setProps({ filterWidth: 4000 });
    const t = mountPanel();
    pressKey(t, 'ArrowRight');
    vi.advanceTimersByTime(60);
    // The candidate equals the confirmed canonical, so the binding sends
    // nothing — a step that cannot move stays silent rather than re-echo
    // the boundary value.
    expect(mockHandlers.onFilterWidthChange).not.toHaveBeenCalled();
  });

  it('DATA-U stepping down from 100 emits 50', () => {
    setProps({ currentMode: 'DATA-U', filterWidth: 100, filterConfig: ftx1FilterConfig['DATA-U'] ?? null });
    const t = mountPanel();
    pressKey(t, 'ArrowLeft');
    vi.advanceTimersByTime(60);
    expect(mockHandlers.onFilterWidthChange).toHaveBeenCalledWith(50);
  });

  it('a full USB up-sweep emits exactly the Table 5 entries in order', () => {
    setProps({ filterWidth: 300 });
    const t = mountPanel();
    // Each emitted write is confirmed by the readback (the real runtime's
    // cycle) before the next step, so the next step advances from it.
    const emitted: number[] = [];
    for (let i = 0; i < 22; i += 1) {
      pressKey(t, 'ArrowRight');
      vi.advanceTimersByTime(60);
      const width = mockHandlers.onFilterWidthChange.mock.calls.at(-1)?.[0] as number;
      emitted.push(width);
      setProps({ filterWidth: width });
    }
    expect(emitted).toEqual([
      400, 600, 850, 1100, 1200, 1500, 1650, 1800, 1950, 2100, 2250, 2400,
      2450, 2500, 2600, 2700, 2800, 2900, 3000, 3200, 3500, 4000,
    ]);
    pressKey(t, 'ArrowRight');
    vi.advanceTimersByTime(60);
    expect(mockHandlers.onFilterWidthChange).toHaveBeenCalledTimes(22);
  });

  it('wheel up from 3000 emits the immediate canonical table entry 3200', () => {
    setProps({ filterWidth: 3000 });
    const t = mountPanel();
    const slider = widthSlider(t);
    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    slider.dispatchEvent(new WheelEvent('wheel', {
      bubbles: true, cancelable: true, deltaY: -120,
    }));
    vi.advanceTimersByTime(60);
    expect(mockHandlers.onFilterWidthChange).toHaveBeenCalledWith(3200);
  });
});

describe('MOR-1679 FTX-1 fixed-width modes are not operable', () => {
  it.each([
    ['FM', 16000],
    ['AM', 9000],
    ['AM-N', 6000],
  ] as const)('%s shows its fixed %i Hz reading on a disabled slider that emits no write', (mode, hz) => {
    setProps({ currentMode: mode, filterWidth: hz, filterConfig: ftx1FilterConfig[mode] ?? null });
    const t = mountPanel();
    expect(t.querySelector('.vc-value')?.textContent).toBe(`${hz / 1000}kHz`);
    expect(widthSlider(t).getAttribute('aria-disabled')).toBe('true');
    pressKey(t, 'ArrowRight');
    pressKey(t, 'ArrowLeft');
    vi.advanceTimersByTime(60);
    expect(mockHandlers.onFilterWidthChange).not.toHaveBeenCalled();
  });

  it('the table-branch Reset button resets IF shift but emits no width write', () => {
    setProps({ currentMode: 'FM', filterWidth: 16000, filterConfig: ftx1FilterConfig.FM ?? null });
    const t = mountPanel();
    const reset = Array.from(t.querySelectorAll('button'))
      .find((button) => button.textContent?.trim() === 'Reset');
    if (!reset) throw new Error('Reset button did not mount');
    reset.click();
    flushSync();
    expect(mockHandlers.onFilterWidthChange).not.toHaveBeenCalled();
    expect(mockHandlers.onIfShiftChange).toHaveBeenCalledWith(0);
  });
});

describe('MOR-1679 FTX-1 null width (C4FM, SH code 00)', () => {
  beforeEach(() => {
    setProps({ currentMode: 'C4FM-DN', filterWidth: Number.NaN, filterConfig: null });
  });

  it('reads as the existing --- Hz unavailable treatment and emits no width write', () => {
    const t = mountPanel();
    expect(t.querySelector('.bw-value')?.textContent).toBe('--- Hz');
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).not.toContain('WIDTH');
    const row = t.querySelector<HTMLElement>('[data-filter-width-lifecycle]');
    if (!row) throw new Error('BW row did not mount');
    row.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    row.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -120 }));
    vi.advanceTimersByTime(60);
    expect(mockHandlers.onFilterWidthChange).not.toHaveBeenCalled();
  });
});
