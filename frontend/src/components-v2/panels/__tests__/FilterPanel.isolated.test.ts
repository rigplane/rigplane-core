import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import { formatFilterWidth } from '../filter-utils';
import { deriveIfShift } from '../filter-controls';

const mockProps = {
  currentMode: 'USB',
  currentFilter: 2,
  filterShape: 0,
  hasFilterShape: true,
  filterLabels: ['FIL1', 'FIL2', 'FIL3'],
  filterWidth: 2400,
  filterConfig: {
    defaults: [3000, 2400, 1800],
    fixed: false,
    minHz: 50,
    maxHz: 3600,
    stepHz: 50,
  } as { defaults: number[]; fixed: boolean; minHz: number; maxHz: number; stepHz: number } | null,
  ifShift: 0,
  hasIfShift: true,
  hasPbt: false,
  pbtInner: 0,
  pbtOuter: 0,
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

// MOR-1536: FilterPanel now also reads the filter-select armed signal —
// default unarmed here, this file's tests are not about that behavior
// (covered by `mor1536-armed-adoption.test.ts`).
const unarmed = { armed: false, value: null };
const widthLifecycle = {
  confirmed: 2400,
  target: null as number | null,
  phase: 'idle',
  busy: false,
  outcome: null as { phase: 'confirmed' | 'failed' | 'timed-out' | 'cancelled'; error?: string } | null,
  presentation: null as {
    lifecycleId: string; transitionId: string; receiver: 0 | 1; sessionEpoch: number;
    target: number; status: 'pending' | 'acknowledged' | 'confirmed' | 'failed' | 'timed-out' | 'cancelled';
  } | null,
};
const propsVersion = new SvelteMap([['value', 0]]);
const lifecycleState = new SvelteMap([['value', widthLifecycle]]);

function setMockProps(overrides: Partial<typeof mockProps>): void {
  Object.assign(mockProps, overrides);
  propsVersion.set('value', (propsVersion.get('value') ?? 0) + 1);
}

function setWidthLifecycle(overrides: Partial<typeof widthLifecycle>): void {
  lifecycleState.set('value', { ...(lifecycleState.get('value') ?? widthLifecycle), ...overrides });
}

function presentation(
  lifecycleId: string,
  status: NonNullable<typeof widthLifecycle.presentation>['status'],
  target: number,
  receiver: 0 | 1 = 0,
  sessionEpoch = 7,
) {
  return { lifecycleId, transitionId: `${sessionEpoch}:${lifecycleId}:${status}`, receiver, sessionEpoch, target, status };
}

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveFilterProps: () => {
    propsVersion.get('value');
    return { ...mockProps };
  },
  getFilterHandlers: () => mockHandlers,
  getFilterArmed: () => unarmed,
  getFilterWidthCommandLifecycle: () => lifecycleState.get('value')!,
}));

import FilterPanel from '../FilterPanel.svelte';

// ---------------------------------------------------------------------------
// formatFilterWidth
// ---------------------------------------------------------------------------

describe('formatFilterWidth', () => {

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

it('returns raw number string for values below 1000', () => {
    expect(formatFilterWidth(500)).toBe('500');
  });

  it('returns raw number string for minimum value 50', () => {
    expect(formatFilterWidth(50)).toBe('50');
  });

  it('returns raw number string for 999', () => {
    expect(formatFilterWidth(999)).toBe('999');
  });

  it('returns "1k" for exactly 1000 Hz', () => {
    expect(formatFilterWidth(1000)).toBe('1k');
  });

  it('returns "1.2k" for 1200 Hz', () => {
    expect(formatFilterWidth(1200)).toBe('1.2k');
  });

  it('returns "2.4k" for 2400 Hz', () => {
    expect(formatFilterWidth(2400)).toBe('2.4k');
  });

  it('returns "3k" for 3000 Hz', () => {
    expect(formatFilterWidth(3000)).toBe('3k');
  });

  it('returns "3.6k" for 3600 Hz', () => {
    expect(formatFilterWidth(3600)).toBe('3.6k');
  });
});

// ---------------------------------------------------------------------------
// FilterPanel component
// ---------------------------------------------------------------------------

let components: ReturnType<typeof mount>[] = [];

function mountPanel(overrides?: Partial<typeof mockProps>) {
  if (overrides) setMockProps(overrides);
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(FilterPanel, { target: t });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  setMockProps({
    currentMode: 'USB',
    currentFilter: 2,
    filterShape: 0,
    hasFilterShape: true,
    filterLabels: ['FIL1', 'FIL2', 'FIL3'],
    filterWidth: 2400,
    filterConfig: {
      defaults: [3000, 2400, 1800],
      fixed: false,
      minHz: 50,
      maxHz: 3600,
      stepHz: 50,
    },
    ifShift: 0,
    hasIfShift: true,
    hasPbt: false,
    pbtInner: 0,
    pbtOuter: 0,
  });
  mockHandlers.onFilterChange = vi.fn();
  mockHandlers.onFilterWidthChange = vi.fn();
  mockHandlers.onFilterShapeChange = vi.fn();
  mockHandlers.onFilterPresetChange = vi.fn();
  mockHandlers.onFilterDefaults = vi.fn();
  mockHandlers.onIfShiftChange = vi.fn();
  mockHandlers.onPbtInnerChange = vi.fn();
  mockHandlers.onPbtOuterChange = vi.fn();
  mockHandlers.onPbtReset = vi.fn();
  lifecycleState.set('value', {
    confirmed: 2400,
    target: null,
    phase: 'idle',
    busy: false,
    outcome: null,
    presentation: null,
  });
});

afterEach(() => {
  components.forEach(c => unmount(c));
  document.body.innerHTML = '';
});

describe('panel structure', () => {
  it('renders filter selector buttons', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button')).map((button) => button.textContent?.trim());
    expect(buttons).toContain('FIL1');
    expect(buttons).toContain('FIL2');
    expect(buttons).toContain('FIL3');
  });

  it('renders a read-only BW display instead of a Width slider', () => {
    const t = mountPanel();
    expect(t.querySelector('.bw-label')?.textContent).toBe('BW');
    expect(t.querySelector('.bw-value')?.textContent).toBe('2.4kHz');
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).not.toContain('Width');
  });

  it('renders the IF Shift slider', () => {
    const t = mountPanel();
    const labels = Array.from(t.querySelectorAll('.vc-label'));
    expect(labels.some(el => el.textContent === 'IF Shift')).toBe(true);
  });

  it('renders the settings gear button', () => {
    const t = mountPanel();
    expect(t.querySelector('.settings-button')?.textContent?.trim()).toBe('⚙');
  });

  it('IF Shift slider has min=-1200, max=1200, step=25', () => {
    const t = mountPanel();
    const sliders = t.querySelectorAll<HTMLElement>('[role="slider"]');
    const ifShiftSlider = sliders[0];
    expect(ifShiftSlider.getAttribute('aria-valuemin')).toBe('-1200');
    expect(ifShiftSlider.getAttribute('aria-valuemax')).toBe('1200');
  });
});

describe('Filter Width lifecycle presentation (MOR-1665)', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());
  const tableConfig = {
    defaults: [2400, 2400, 2400], fixed: false, minHz: 1800, maxHz: 3000, stepHz: 1,
    table: [1800, 2400, 3000],
  } as typeof mockProps.filterConfig;

  it('keeps the canonical BW readout distinct from a pending target and marks the group busy', () => {
    setWidthLifecycle({
      target: 3000, phase: 'acknowledged', busy: true,
      presentation: presentation('main-command', 'acknowledged', 3000),
    });
    const t = mountPanel();

    const group = t.querySelector<HTMLElement>('[data-filter-width-lifecycle]');
    expect(group?.getAttribute('aria-busy')).toBe('true');
    expect(t.querySelector('[data-confirmed-width]')?.textContent).toBe('2.4kHz');
    expect(t.querySelector('[data-pending-width-target]')?.textContent).toContain('3kHz');
    expect(t.querySelector('[data-filter-width-live]')?.textContent).toContain('not yet confirmed');
  });

  it.each([
    ['confirmed', 'confirmed'],
    ['failed', 'not applied'],
    ['timed-out', 'timed out'],
    ['cancelled', 'cancelled'],
  ] as const)('announces the terminal %s outcome once without changing canonical BW', (phase, message) => {
    setWidthLifecycle({
      target: null,
      phase: phase === 'confirmed' ? 'confirmed' : 'idle',
      busy: false,
      outcome: { phase },
      presentation: presentation('main-command', phase, 3000),
    });
    const t = mountPanel();

    expect(t.querySelector<HTMLElement>('[data-filter-width-lifecycle]')?.getAttribute('aria-busy')).toBe('false');
    expect(t.querySelector('[data-confirmed-width]')?.textContent).toBe('2.4kHz');
    expect(t.querySelector('[data-pending-width-target]')).toBeNull();
    expect(t.querySelector('[data-filter-width-live]')?.textContent).toContain(message);
  });

  it.each(['keyboard', 'pointer'] as const)(
    'keeps table visuals canonical through raw %s input, pending, and accepted state',
    (input) => {
      const t = mountPanel({ filterConfig: tableConfig });
      const slider = t.querySelector<HTMLElement>('[role="slider"]')!;
      const hbar = t.querySelector<HTMLElement>('.vc-hbar')!;
      if (input === 'keyboard') {
        slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
        vi.advanceTimersByTime(60);
      } else {
        slider.setPointerCapture = vi.fn();
        vi.spyOn(hbar, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 100 } as DOMRect);
        slider.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 100, pointerId: 1 }));
      }
      flushSync();

      expect(mockHandlers.onFilterWidthChange).toHaveBeenCalledWith(3000);
      expect(t.querySelector('.vc-value')?.textContent).toBe('2.4kHz');
      expect(hbar.getAttribute('style')).toContain('--vc-fill-percent: 50%');
      expect(slider.getAttribute('aria-valuenow')).toBe('1');
      expect(t.querySelector('[data-pending-width-target]')).toBeNull();

      setWidthLifecycle({
        target: 3000, phase: 'pending', busy: true,
        presentation: presentation(`table-${input}`, 'pending', 3000),
      });
      flushSync();
      expect(t.querySelector('[data-pending-width-target]')?.textContent).toContain('3kHz');
      expect(t.querySelector('.vc-value')?.textContent).toBe('2.4kHz');

      setMockProps({ filterWidth: 3000 });
      setWidthLifecycle({
        confirmed: 3000, target: null, phase: 'confirmed', busy: false, outcome: { phase: 'confirmed' },
        presentation: presentation(`table-${input}`, 'confirmed', 3000),
      });
      flushSync();
      expect(t.querySelector('.vc-value')?.textContent).toBe('3kHz');
      expect(hbar.getAttribute('style')).toContain('--vc-fill-percent: 100%');
      expect(slider.getAttribute('aria-valuenow')).toBe('2');
      expect(t.querySelector('[data-pending-width-target]')).toBeNull();
    },
  );

  it.each(['failed', 'timed-out', 'cancelled'] as const)(
    'keeps table visuals canonical when a raw target is %s',
    (status) => {
      const t = mountPanel({ filterConfig: tableConfig });
      const slider = t.querySelector<HTMLElement>('[role="slider"]')!;
      slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      vi.advanceTimersByTime(60);
      setWidthLifecycle({
        target: null, phase: 'idle', busy: false, outcome: { phase: status },
        presentation: presentation(`table-${status}`, status, 3000),
      });
      flushSync();
      expect(t.querySelector('.vc-value')?.textContent).toBe('2.4kHz');
      expect(t.querySelector('.vc-hbar')?.getAttribute('style')).toContain('--vc-fill-percent: 50%');
      expect(slider.getAttribute('aria-valuenow')).toBe('1');
      expect(t.querySelector('[data-pending-width-target]')).toBeNull();
    },
  );

  it('uses the exact new lifecycle target instead of a stale prior pending target', () => {
    const t = mountPanel();
    setWidthLifecycle({
      target: 3000, phase: 'pending', busy: true, outcome: null,
      presentation: presentation('old-main', 'pending', 3000),
    });
    flushSync();
    setMockProps({ filterWidth: 1800 });
    setWidthLifecycle({
      confirmed: 1800, target: null, phase: 'idle', busy: false, outcome: { phase: 'failed' },
      presentation: presentation('new-main', 'failed', 1800),
    });
    flushSync();

    const status = t.querySelector('[data-filter-width-live]')?.textContent ?? '';
    expect(status).toContain('1.8kHz was not applied');
    expect(status).not.toContain('3kHz');
    expect(t.querySelector('[data-confirmed-width]')?.textContent).toBe('1.8kHz');
  });

  it('freezes one terminal message across retained reads and canonical-only changes', async () => {
    setWidthLifecycle({
      target: null, phase: 'idle', busy: false, outcome: { phase: 'failed' },
      presentation: presentation('failed-main', 'failed', 1800),
    });
    const t = mountPanel();
    const live = t.querySelector('[data-filter-width-live]')!;
    const frozen = live.textContent;
    const mutations: MutationRecord[] = [];
    const observer = new MutationObserver((records) => mutations.push(...records));
    observer.observe(live, { childList: true, characterData: true, subtree: true });

    setMockProps({ filterWidth: 1800 });
    setWidthLifecycle({ confirmed: 1800 });
    flushSync();
    await Promise.resolve();

    expect(live.textContent).toBe(frozen);
    expect(mutations).toHaveLength(0);
    observer.disconnect();
  });

  it('isolates superseding receiver/session identities and clears the live region on GC', () => {
    const t = mountPanel();
    setWidthLifecycle({
      target: 3000, phase: 'pending', busy: true,
      presentation: presentation('main-old', 'pending', 3000, 0, 7),
    });
    flushSync();
    setWidthLifecycle({
      target: 2100, phase: 'pending', busy: true,
      presentation: presentation('sub-new', 'pending', 2100, 1, 8),
    });
    flushSync();
    expect(t.querySelector('[data-filter-width-live]')?.textContent).toContain('2.1kHz');
    expect(t.querySelector('[data-pending-width-target]')?.textContent).toContain('2.1kHz');

    setWidthLifecycle({ target: null, phase: 'idle', busy: false, outcome: null, presentation: null });
    flushSync();
    expect(t.querySelector('[data-filter-width-live]')?.textContent).toBe('');
  });

  it('restores an open modal draft to canonical state on one failed transition', () => {
    const t = mountPanel();
    (t.querySelector('.settings-button') as HTMLButtonElement).click();
    flushSync();
    const activeSlider = document.querySelectorAll<HTMLElement>('.filter-modal [role="slider"]')[1];
    activeSlider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(60);
    flushSync();
    expect(activeSlider.getAttribute('aria-valuenow')).not.toBe('2400');

    setWidthLifecycle({
      target: null, phase: 'idle', busy: false, outcome: { phase: 'failed' },
      presentation: presentation('failed-draft', 'failed', 2450),
    });
    flushSync();
    expect(activeSlider.getAttribute('aria-valuenow')).toBe('2400');
    expect(t.querySelector('[data-filter-width-live]')?.textContent).toContain('2.5kHz was not applied');
  });
});

describe('PBT sliders visibility', () => {
  it('does not render PBT sliders when hasPbt is false (default)', () => {
    const t = mountPanel();
    const labels = Array.from(t.querySelectorAll('.vc-label')).map(el => el.textContent);
    expect(labels).not.toContain('PBT Inner');
    expect(labels).not.toContain('PBT Outer');
  });

  it('does not render PBT sliders when hasPbt=false explicitly', () => {
    const t = mountPanel({ hasPbt: false });
    const labels = Array.from(t.querySelectorAll('.vc-label')).map(el => el.textContent);
    expect(labels).not.toContain('PBT Inner');
    expect(labels).not.toContain('PBT Outer');
  });

  it('renders PBT Inner slider when hasPbt=true', () => {
    const t = mountPanel({ hasPbt: true, pbtInner: 100, pbtOuter: -50 });
    const labels = Array.from(t.querySelectorAll('.vc-label')).map(el => el.textContent);
    expect(labels).toContain('PBT Inner');
  });

  it('renders PBT Outer slider when hasPbt=true', () => {
    const t = mountPanel({ hasPbt: true, pbtInner: 100, pbtOuter: -50 });
    const labels = Array.from(t.querySelectorAll('.vc-label')).map(el => el.textContent);
    expect(labels).toContain('PBT Outer');
  });

  it('renders Reset PBT button when hasPbt=true', () => {
    const t = mountPanel({ hasPbt: true, pbtInner: 100, pbtOuter: -50 });
    const buttons = Array.from(t.querySelectorAll('button')).map(el => el.textContent?.trim());
    expect(buttons).toContain('Reset');
  });

  it('renders 3 sliders total when hasPbt=true', () => {
    const t = mountPanel({ hasPbt: true, pbtInner: 0, pbtOuter: 0 });
    expect(t.querySelectorAll('[role="slider"]').length).toBe(3);
  });

  it('renders 1 slider total when hasPbt=false', () => {
    const t = mountPanel();
    expect(t.querySelectorAll('[role="slider"]').length).toBe(1);
  });
});

/**
 * MOR-1494: capability-absent controls must be HIDDEN, not shown dead.
 * IC-7300 (PBT-only, no `if_shift` command) previously rendered the IF
 * Shift slider permanently disabled with a PBT-derived stand-in value.
 * `if_shift` is a real command only on Yaesu-family radios (e.g. FTX-1);
 * Icom radios expose PBT Inner/Outer instead. The panel must render
 * IF Shift only when `hasIfShift` (data-driven from the radio's own
 * capability set) is true — never from a hardcoded model/family check.
 */
describe('IF Shift visibility (MOR-1494)', () => {
  it('hides the IF Shift row for an IC-7300-shaped capability set (pbt, no if_shift)', () => {
    const t = mountPanel({ hasIfShift: false, hasPbt: true, pbtInner: 0, pbtOuter: 0 });
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).not.toContain('IF Shift');
    expect(labels).not.toContain('IF Shift (derived)');
  });

  it('renders only the real PBT Inner/Outer sliders for an IC-7300-shaped capability set, bound to their own values', () => {
    const t = mountPanel({ hasIfShift: false, hasPbt: true, pbtInner: 100, pbtOuter: -50 });
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).toContain('PBT Inner');
    expect(labels).toContain('PBT Outer');
    const sliders = t.querySelectorAll<HTMLElement>('[role="slider"]');
    expect(sliders.length).toBe(2);
    // PBT Inner renders before PBT Outer (source order) -- values must track
    // their OWN prop, not a shared/derived stand-in.
    expect(sliders[0].getAttribute('aria-valuenow')).toBe('100');
    expect(sliders[1].getAttribute('aria-valuenow')).toBe('-50');
  });

  it('renders the IF Shift row for an FTX-1-shaped capability set (if_shift, no pbt), bound to the real ifShift value', () => {
    const t = mountPanel({ hasIfShift: true, hasPbt: false, ifShift: 275 });
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).toContain('IF Shift');
    const sliders = t.querySelectorAll<HTMLElement>('[role="slider"]');
    expect(sliders.length).toBe(1);
    expect(sliders[0].getAttribute('aria-valuenow')).toBe('275');
  });

  it('leaves the FTX-1-shaped IF Shift slider enabled (no PBT to disable it on)', () => {
    const t = mountPanel({ hasIfShift: true, hasPbt: false });
    const sliders = t.querySelectorAll<HTMLElement>('[role="slider"]');
    expect(sliders.length).toBe(1);
    expect(sliders[0].getAttribute('aria-disabled')).not.toBe('true');
  });

  it('hides the IF Shift row when the capability set has neither if_shift nor pbt', () => {
    const t = mountPanel({ hasIfShift: false, hasPbt: false });
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).not.toContain('IF Shift');
    expect(t.querySelectorAll('[role="slider"]').length).toBe(0);
  });
});

describe('callbacks', () => {

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('calls onFilterChange when a filter button is clicked', () => {
    const t = mountPanel();
    const button = Array.from(t.querySelectorAll('button')).find(el => el.textContent?.trim() === 'FIL3') as HTMLButtonElement;
    button.click();
    expect(mockHandlers.onFilterChange).toHaveBeenCalledWith(3);
  });

  it('calls onIfShiftChange when IF Shift slider changes', () => {
    const t = mountPanel();
    const slider = t.querySelectorAll<HTMLElement>('[role="slider"]')[0];
    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
    vi.advanceTimersByTime(60);

    expect(mockHandlers.onIfShiftChange).toHaveBeenCalled();
  });

  it('calls onPbtInnerChange when PBT Inner slider changes', () => {
    const t = mountPanel({ hasPbt: true, pbtInner: 0, pbtOuter: 0 });
    const sliders = t.querySelectorAll<HTMLElement>('[role="slider"]');
    sliders[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(60);

    expect(mockHandlers.onPbtInnerChange).toHaveBeenCalled();
  });

  it('calls onPbtOuterChange when PBT Outer slider changes', () => {
    const t = mountPanel({ hasPbt: true, pbtInner: 0, pbtOuter: 0 });
    const sliders = t.querySelectorAll<HTMLElement>('[role="slider"]');
    sliders[2].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
    vi.advanceTimersByTime(60);

    expect(mockHandlers.onPbtOuterChange).toHaveBeenCalled();
  });

  it('calls onPbtReset when the reset button is clicked', () => {
    const t = mountPanel({ hasPbt: true, pbtInner: 100, pbtOuter: -100 });
    const button = Array.from(t.querySelectorAll('button')).find(el => el.textContent?.trim() === 'Reset') as HTMLButtonElement;
    button.click();
    expect(mockHandlers.onPbtReset).toHaveBeenCalledOnce();
  });

  it('opens the settings modal and calls onFilterPresetChange from a modal slider', () => {
    const t = mountPanel();
    const gear = t.querySelector('.settings-button') as HTMLButtonElement;
    gear.click();
    flushSync();

    const modal = document.querySelector('.filter-modal');
    expect(modal).not.toBeNull();

    const sliders = modal?.querySelectorAll<HTMLElement>('[role="slider"]') ?? [];
    sliders[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(60);
    expect(mockHandlers.onFilterPresetChange).toHaveBeenCalled();
  });

  it('calls onFilterDefaults when restore defaults is clicked in the modal', () => {
    const t = mountPanel();
    const gear = t.querySelector('.settings-button') as HTMLButtonElement;
    gear.click();
    flushSync();

    const button = Array.from(document.querySelectorAll('button')).find(el => el.textContent?.trim() === 'Restore Defaults') as HTMLButtonElement;
    button.click();
    expect(mockHandlers.onFilterDefaults).toHaveBeenCalledWith([3000, 2400, 1800]);
  });

  it('shows SHARP and SOFT shape buttons in the modal', () => {
    const t = mountPanel();
    const gear = t.querySelector('.settings-button') as HTMLButtonElement;
    gear.click();
    flushSync();

    const buttons = Array.from(document.querySelectorAll('button')).map((el) => el.textContent?.trim());
    expect(buttons).toContain('SHARP');
    expect(buttons).toContain('SOFT');
  });

  it('calls onFilterShapeChange when the SOFT button is clicked in the modal', () => {
    const t = mountPanel();
    const gear = t.querySelector('.settings-button') as HTMLButtonElement;
    gear.click();
    flushSync();

    const button = Array.from(document.querySelectorAll('button')).find((el) => el.textContent?.trim() === 'SOFT') as HTMLButtonElement;
    button.click();
    expect(mockHandlers.onFilterShapeChange).toHaveBeenCalledWith(1);
  });
});

/**
 * MOR-1503: capability-absent controls must be HIDDEN, not shown dead
 * (same class as MOR-1494's IF-shift row). `filter_shape` is a real
 * command only on Icom radios (e.g. IC-7300, `rigs/ic7300.toml` declares
 * the capability); the FTX-1's capability set does not include it, yet
 * the settings modal rendered the SHARP/SOFT shape buttons
 * unconditionally. The panel must render the shape section only when
 * `hasFilterShape` (data-driven from the radio's own capability set) is
 * true — never from a hardcoded model/family check.
 */
describe('filter shape visibility (MOR-1503)', () => {
  it('hides the SHARP/SOFT shape buttons in the modal for an FTX-1-shaped capability set (no filter_shape)', () => {
    const t = mountPanel({ hasFilterShape: false });
    const gear = t.querySelector('.settings-button') as HTMLButtonElement;
    gear.click();
    flushSync();

    const buttons = Array.from(document.querySelectorAll('button')).map((el) => el.textContent?.trim());
    expect(buttons).not.toContain('SHARP');
    expect(buttons).not.toContain('SOFT');
  });

  it('hides the whole shape section in the modal for an FTX-1-shaped capability set', () => {
    const t = mountPanel({ hasFilterShape: false });
    const gear = t.querySelector('.settings-button') as HTMLButtonElement;
    gear.click();
    flushSync();

    expect(document.querySelector('.shape-section')).toBeNull();
  });

  it('renders the SHARP/SOFT shape buttons in the modal for an IC-7300-shaped capability set (filter_shape)', () => {
    const t = mountPanel({ hasFilterShape: true });
    const gear = t.querySelector('.settings-button') as HTMLButtonElement;
    gear.click();
    flushSync();

    const buttons = Array.from(document.querySelectorAll('button')).map((el) => el.textContent?.trim());
    expect(buttons).toContain('SHARP');
    expect(buttons).toContain('SOFT');
  });
});

describe('IF Shift semantics', () => {
  it('can be derived from current PBT offsets', () => {
    expect(deriveIfShift(-150, 50)).toBe(-50);
  });
});

/**
 * A12 (MOR-1409, Core #2317) — FilterPanel.svelte consumer-boundary fix.
 *
 * `panel-props.ts`'s `toFilterProps`/`toAudioSpectrumProps` now return a
 * `NaN` sentinel for an unobserved `filterWidth` (no more fabricated 2400 Hz
 * stand-in, deferred from A11 by adjudication 5245697359). Left unguarded,
 * `formatWidthDisplay`'s call to `formatFilterWidth(NaN)` renders the
 * literal substring "NaNkHz" in the BW readout (`:207-208`) and the
 * fixed-config modal row (`:299`) — exactly the defect the adjudication
 * named. The fix is a `Number.isFinite` guard inside FilterPanel's own
 * local `formatWidthDisplay`, matching the `'--'`/`'---'` placeholder
 * convention `frequency-format.ts` established at A11 (corr. 5245817033/
 * 5245876185) — never the literal "NaN" substring.
 *
 * Separately, `:38`'s `normalizedLabels` re-fabrication
 * (`filterLabels.length > 0 ? filterLabels : ['FIL1','FIL2','FIL3']`)
 * substitutes the three-label default back in whenever `filterLabels` is
 * the honest empty array (unknown capability) — this must pass the empty
 * array through unchanged so the modal renders zero rows, not three
 * fabricated ones.
 */
describe('FilterPanel — no fabricated defaults at the consumer boundary (MOR-1409 A12)', () => {
  it('does not render a "NaN" substring in the BW readout for a non-finite filterWidth', () => {
    const t = mountPanel({ filterWidth: Number.NaN });
    const bwValue = t.querySelector('.bw-value')?.textContent ?? '';
    expect(bwValue).not.toMatch(/NaN/);
  });

  it('renders the established "---"-family placeholder in the BW readout for a non-finite filterWidth', () => {
    const t = mountPanel({ filterWidth: Number.NaN });
    const bwValue = t.querySelector('.bw-value')?.textContent ?? '';
    expect(bwValue).toBe('--- Hz');
  });

  it('still renders the real formatted width for a finite filterWidth', () => {
    const t = mountPanel({ filterWidth: 2400 });
    expect(t.querySelector('.bw-value')?.textContent).toBe('2.4kHz');
  });

  it('does not render a "NaN" substring in the fixed-config modal row for a non-finite filterWidth', () => {
    const t = mountPanel({
      filterWidth: Number.NaN,
      filterConfig: { defaults: [], fixed: true, minHz: 50, maxHz: 3600, stepHz: 50 },
    });
    const gear = t.querySelector('.settings-button') as HTMLButtonElement;
    gear.click();
    flushSync();
    const fixedValues = Array.from(document.querySelectorAll('.modal-fixed-value')).map(
      (el) => el.textContent,
    );
    expect(fixedValues.some((text) => /NaN/.test(text ?? ''))).toBe(false);
  });

  it('renders zero filter-selector buttons for an empty (unknown) filterLabels catalog, not a fabricated FIL1/FIL2/FIL3', () => {
    const t = mountPanel({ filterLabels: [] });
    const buttons = Array.from(t.querySelectorAll('button')).map((button) => button.textContent?.trim());
    expect(buttons).not.toContain('FIL1');
    expect(buttons).not.toContain('FIL2');
    expect(buttons).not.toContain('FIL3');
  });

  it('renders zero modal filter rows for an empty (unknown) filterLabels catalog', () => {
    const t = mountPanel({ filterLabels: [] });
    const gear = t.querySelector('.settings-button') as HTMLButtonElement;
    gear.click();
    flushSync();
    const rows = document.querySelectorAll('.modal-filter-row');
    expect(rows.length).toBe(0);
  });

  it('still renders the real filter-selector buttons for a populated filterLabels catalog', () => {
    const t = mountPanel({ filterLabels: ['FIL1', 'FIL2'] });
    const buttons = Array.from(t.querySelectorAll('button')).map((button) => button.textContent?.trim());
    expect(buttons).toContain('FIL1');
    expect(buttons).toContain('FIL2');
    expect(buttons).not.toContain('FIL3');
  });
});
