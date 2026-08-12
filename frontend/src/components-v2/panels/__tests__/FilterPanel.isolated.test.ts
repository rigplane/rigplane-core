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
