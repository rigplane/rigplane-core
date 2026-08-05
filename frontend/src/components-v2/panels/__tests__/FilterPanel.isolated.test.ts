import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { formatFilterWidth } from '../filter-utils';
import { deriveIfShift } from '../filter-controls';

const mockProps = {
  currentMode: 'USB',
  currentFilter: 2,
  filterShape: 0,
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

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveFilterProps: () => mockProps,
  getFilterHandlers: () => mockHandlers,
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
  if (overrides) Object.assign(mockProps, overrides);
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(FilterPanel, { target: t });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  Object.assign(mockProps, {
    currentMode: 'USB',
    currentFilter: 2,
    filterShape: 0,
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

describe('IF Shift semantics', () => {
  it('can be derived from current PBT offsets', () => {
    expect(deriveIfShift(-150, 50)).toBe(-50);
  });
});
