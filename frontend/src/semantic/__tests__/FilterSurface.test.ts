/**
 * MOR-1304 — the semantic mode/filter surface (vocabulary slice 4B).
 *
 * Covers BOTH fact groups the surface renders: `modeFilter` (MOR-1280 —
 * mode, filter selection, filter width) and `filterPassband` (MOR-1284 —
 * filter shape, IF-shift, PBT inner/outer, DATA submode). Every test names
 * the doctrine it pins: two-level availability (MOR-977), no fabricated
 * selection on an unobserved reading, and the F2-style independence between
 * `filterWidth` and its own `filterWidthMin`/`filterWidthMax` bounds.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import FilterSurface, {
  FILTER_PASSBAND_LEVELS, FILTER_SHAPES, type FilterPassbandLevelField,
} from '../FilterSurface.svelte';
import { topologyFixtures, withFilterPassband, withModeFilter } from '../fixtures/topologies';
import type {
  Availability, FilterPassbandViewModel, ModeFilterViewModel, RadioViewModel,
} from '../radio-view-model';

const base = (): RadioViewModel =>
  withFilterPassband(withModeFilter(topologyFixtures['1/single']));

type ModeFilterKey = keyof ModeFilterViewModel;
type FilterPassbandKey = keyof FilterPassbandViewModel;

function withModeFilterField(
  view: RadioViewModel, field: ModeFilterKey,
  over: { availability?: Availability; unknown?: boolean },
): RadioViewModel {
  const group = view.modeFilter!;
  const current = group[field] as { reading: unknown; availability: Availability };
  return {
    ...view,
    modeFilter: {
      ...group,
      [field]: {
        reading: over.unknown ? { status: 'unknown' } : current.reading,
        availability: over.availability ?? current.availability,
      },
    } as ModeFilterViewModel,
  };
}

function withPassbandField(
  view: RadioViewModel, field: FilterPassbandKey,
  over: { availability?: Availability; unknown?: boolean },
): RadioViewModel {
  const group = view.filterPassband!;
  const current = group[field] as { reading: unknown; availability: Availability };
  return {
    ...view,
    filterPassband: {
      ...group,
      [field]: {
        reading: over.unknown ? { status: 'unknown' } : current.reading,
        availability: over.availability ?? current.availability,
      },
    } as FilterPassbandViewModel,
  };
}

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Handlers = {
  onModeChange?: (mode: string) => void;
  onFilterChange?: (filter: number) => void;
  onFilterWidthChange?: (width: number) => void;
  onFilterShapeChange?: (shape: number) => void;
  onIfShiftChange?: (value: number) => void;
  onPbtInnerChange?: (value: number) => void;
  onPbtOuterChange?: (value: number) => void;
};

function render(view: RadioViewModel, handlers: Handlers = {}) {
  const component = mount(FilterSurface, { target, props: { view, ...handlers } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="filter-surface"]'),
    group: (testId: string) => q<HTMLElement>(`[data-testid="${testId}"]`),
    button: (testId: string, value: string | number) => q<HTMLButtonElement>(
      `[data-testid="${testId}-${value}"]`,
    ),
    input: (testId: string) => q<HTMLInputElement>(`[data-testid="${testId}"] input`),
    output: (testId: string) => q<HTMLElement>(`[data-testid="${testId}"] output`),
  };
}

function withSurface(
  view: RadioViewModel, fn: (s: ReturnType<typeof render>) => void, handlers: Handlers = {},
): void {
  const s = render(view, handlers);
  try { fn(s); } finally { s.dispose(); }
}

// ── 1. Whole-group gating ───────────────────────────────────────────────────

describe('whole-group gating', () => {
  it('renders nothing for a view model carrying neither group', () => {
    withSurface(topologyFixtures['1/single'], (s) => {
      expect(s.root()).toBeNull();
    });
  });

  it('renders only the modeFilter controls when filterPassband is absent', () => {
    withSurface(withModeFilter(topologyFixtures['1/single']), (s) => {
      expect(s.root()).not.toBeNull();
      expect(s.group('filter-mode')).not.toBeNull();
      expect(s.group('filter-shape')).toBeNull();
      expect(s.group('filter-data-mode')).toBeNull();
    });
  });

  it('renders only the filterPassband controls when modeFilter is absent', () => {
    withSurface(withFilterPassband(topologyFixtures['1/single']), (s) => {
      expect(s.root()).not.toBeNull();
      expect(s.group('filter-shape')).not.toBeNull();
      expect(s.group('filter-mode')).toBeNull();
    });
  });
});

// ── 2. Structural gating: absent, never a disabled promise ──────────────────

describe('structural availability decides whether a control EXISTS', () => {
  const ABSENT: Availability = { structural: false, operational: false };

  it('renders no mode control when currentMode is structurally absent', () => {
    const view = withModeFilterField(base(), 'currentMode', { availability: ABSENT });
    withSurface(view, (s) => expect(s.group('filter-mode')).toBeNull());
  });

  it('renders no filter-width control when filterWidth is structurally absent', () => {
    const view = withModeFilterField(base(), 'filterWidth', { availability: ABSENT });
    withSurface(view, (s) => expect(s.group('filter-width')).toBeNull());
  });

  it('renders no shape control when filterShape is structurally absent', () => {
    const view = withPassbandField(base(), 'filterShape', { availability: ABSENT });
    withSurface(view, (s) => expect(s.group('filter-shape')).toBeNull());
  });

  it('renders no dataMode readout when dataMode is structurally absent', () => {
    const view = withPassbandField(base(), 'dataMode', { availability: ABSENT });
    withSurface(view, (s) => expect(s.group('filter-data-mode')).toBeNull());
  });

  it.each(FILTER_PASSBAND_LEVELS)('renders no "%s" control when structurally absent', (field) => {
    const view = withPassbandField(base(), field, { availability: ABSENT });
    withSurface(view, (s) => expect(s.group(`filter-${field}`)).toBeNull());
  });
});

// ── 3. Operational gating: present, disabled, with a reason ─────────────────

describe('operational availability decides whether a control is USABLE', () => {
  const PRESENT_UNREADABLE: Availability = { structural: true, operational: false };

  it('keeps the width slider present but disabled when unreadable', () => {
    const view = withModeFilterField(base(), 'filterWidth', { availability: PRESENT_UNREADABLE });
    withSurface(view, (s) => {
      expect(s.input('filter-width')!.disabled).toBe(true);
      expect(s.group('filter-width')!.dataset.disabledReason).toBe('field-not-observed');
    });
  });

  it('never enables the width slider on an unobserved reading', () => {
    const view = withModeFilterField(base(), 'filterWidth', { unknown: true });
    withSurface(view, (s) => {
      expect(s.input('filter-width')!.disabled).toBe(true);
      expect(s.output('filter-width')!.textContent).toBe('?');
    });
  });

  it.each(FILTER_PASSBAND_LEVELS)('keeps "%s" present but disabled when unreadable', (field) => {
    const view = withPassbandField(base(), field, { availability: PRESENT_UNREADABLE });
    withSurface(view, (s) => {
      expect(s.input(`filter-${field}`)!.disabled).toBe(true);
      expect(s.group(`filter-${field}`)!.dataset.disabledReason).toBe('field-not-observed');
    });
  });

  it('disables mode buttons and marks none pressed on an unobserved reading', () => {
    const view = withModeFilterField(base(), 'currentMode', { unknown: true });
    withSurface(view, (s) => {
      for (const choice of view.modeFilter!.modeChoices) {
        const button = s.button('filter-mode', choice)!;
        expect(button.disabled).toBe(true);
        expect(button.getAttribute('aria-pressed')).toBe('false');
      }
    });
  });
});

// ── 4. Choice-field emission: mode, filter selection, shape ─────────────────

describe('choice fields emit the caller intent only when usable', () => {
  it('emits onModeChange with the clicked choice', () => {
    const onModeChange = vi.fn();
    withSurface(base(), (s) => {
      s.button('filter-mode', 'LSB')!.click();
      flushSync();
      expect(onModeChange).toHaveBeenCalledExactlyOnceWith('LSB');
    }, { onModeChange });
  });

  it('marks the current mode pressed and no other', () => {
    withSurface(base(), (s) => {
      expect(s.button('filter-mode', 'USB')!.getAttribute('aria-pressed')).toBe('true');
      expect(s.button('filter-mode', 'LSB')!.getAttribute('aria-pressed')).toBe('false');
    });
  });

  it('emits onFilterChange with the 1-based filter index', () => {
    const onFilterChange = vi.fn();
    withSurface(base(), (s) => {
      s.button('filter-select', 2)!.click();
      flushSync();
      expect(onFilterChange).toHaveBeenCalledExactlyOnceWith(2);
    }, { onFilterChange });
  });

  it('emits onFilterShapeChange with the clicked shape value', () => {
    const onFilterShapeChange = vi.fn();
    withSurface(base(), (s) => {
      s.button('filter-shape', 1)!.click();
      flushSync();
      expect(onFilterShapeChange).toHaveBeenCalledExactlyOnceWith(1);
    }, { onFilterShapeChange });
  });

  it.each(FILTER_SHAPES)('renders a "%s" button labelled "%s"', (value, label) => {
    withSurface(base(), (s) => {
      expect(s.button('filter-shape', value)!.textContent).toBe(label);
    });
  });

  // MUTATION KILLED: emitting a choice intent from an unobserved reading —
  // clicking would arm a guess rather than a confirmed selection.
  it('emits nothing when a choice is clicked on an unobserved reading', () => {
    const onModeChange = vi.fn();
    const view = withModeFilterField(base(), 'currentMode', { unknown: true });
    withSurface(view, (s) => {
      s.button('filter-mode', 'LSB')!.click();
      flushSync();
      expect(onModeChange).not.toHaveBeenCalled();
    }, { onModeChange });
  });
});

// ── 5. Level-field emission: width, IF-shift, PBT inner/outer ───────────────

describe('level fields emit the raw value, unrescaled', () => {
  it('emits onFilterWidthChange with the raw slider value', () => {
    const onFilterWidthChange = vi.fn();
    withSurface(base(), (s) => {
      const input = s.input('filter-width')!;
      input.value = '2800';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      flushSync();
      expect(onFilterWidthChange).toHaveBeenCalledExactlyOnceWith(2800);
    }, { onFilterWidthChange });
  });

  const passbandHandlers: Record<FilterPassbandLevelField, keyof Handlers> = {
    ifShift: 'onIfShiftChange', pbtInner: 'onPbtInnerChange', pbtOuter: 'onPbtOuterChange',
  };

  it.each(FILTER_PASSBAND_LEVELS)('emits (%s) via its own handler with min/max/step', (field, _label, min, max, step) => {
    const spy = vi.fn();
    withSurface(base(), (s) => {
      const input = s.input(`filter-${field}`)!;
      expect(input.min).toBe(String(min));
      expect(input.max).toBe(String(max));
      expect(input.step).toBe(String(step));
      input.value = String(max);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      flushSync();
      expect(spy).toHaveBeenCalledExactlyOnceWith(max);
    }, { [passbandHandlers[field]]: spy });
  });

  // MUTATION KILLED: emitting a level intent from an unobserved reading.
  it('emits nothing from an unobserved passband level', () => {
    const onIfShiftChange = vi.fn();
    const view = withPassbandField(base(), 'ifShift', { unknown: true });
    withSurface(view, (s) => {
      const input = s.input('filter-ifShift')!;
      input.value = '100';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      flushSync();
      expect(onIfShiftChange).not.toHaveBeenCalled();
    }, { onIfShiftChange });
  });
});

// ── 6. filterWidth bounds read their OWN field, independently (F2-style) ────

describe('filterWidth and its bounds carry independent availability', () => {
  // MUTATION KILLED: gating the width slider on filterWidthMin/Max's
  // availability instead of filterWidth's own — a shared "modeObserved" stand-
  // in for the whole group, exactly the fabrication the MOR-1280 F2 fix bans.
  it('keeps the width slider enabled when only the BOUNDS are unobserved', () => {
    let view = withModeFilterField(base(), 'filterWidthMin', {
      availability: { structural: true, operational: false },
    });
    view = withModeFilterField(view, 'filterWidthMax', {
      availability: { structural: true, operational: false },
    });
    withSurface(view, (s) => {
      expect(s.input('filter-width')!.disabled).toBe(false);
    });
  });

  it('disables the width slider when filterWidth itself is unobserved, bounds untouched', () => {
    const view = withModeFilterField(base(), 'filterWidth', { unknown: true });
    withSurface(view, (s) => {
      expect(s.input('filter-width')!.disabled).toBe(true);
      // Bounds are still their own known values — used for min/max regardless.
      expect(s.input('filter-width')!.min).toBe('50');
      expect(s.input('filter-width')!.max).toBe('3600');
    });
  });

  it('falls back to a sane default range when the bounds are unobserved', () => {
    let view = withModeFilterField(base(), 'filterWidthMin', { unknown: true });
    view = withModeFilterField(view, 'filterWidthMax', { unknown: true });
    withSurface(view, (s) => {
      expect(s.input('filter-width')!.min).toBe('50');
      expect(s.input('filter-width')!.max).toBe('9999');
    });
  });
});

// ── 7. filterShape unknown-but-structural is accepted, honest (carry-fwd 4) ─

describe('filterShape renders honest unknown rather than a fabricated default', () => {
  it('marks neither SHARP nor SOFT pressed while unobserved', () => {
    const view = withPassbandField(base(), 'filterShape', { unknown: true });
    withSurface(view, (s) => {
      for (const [value] of FILTER_SHAPES) {
        expect(s.button('filter-shape', value)!.getAttribute('aria-pressed')).toBe('false');
        expect(s.button('filter-shape', value)!.disabled).toBe(true);
      }
    });
  });
});

// ── 8. dataMode is an honest readout, never a control ───────────────────────

describe('dataMode renders as a readout', () => {
  it('shows the known value', () => {
    withSurface(base(), (s) => {
      expect(s.output('filter-data-mode')!.textContent).toBe('0');
    });
  });

  it('shows "?" honestly when unobserved, never a fabricated default', () => {
    const view = withPassbandField(base(), 'dataMode', { unknown: true });
    withSurface(view, (s) => {
      expect(s.output('filter-data-mode')!.textContent).toBe('?');
    });
  });

  it('carries no button or input — it is read-only', () => {
    withSurface(base(), (s) => {
      const readout = s.group('filter-data-mode')!;
      expect(readout.querySelector('button')).toBeNull();
      expect(readout.querySelector('input')).toBeNull();
    });
  });
});

// ── 9. No re-derivation — facts only (carry-forwards 1 and 5) ───────────────

describe('the surface never re-derives what the adapter already computed', () => {
  /** Strip comments first — the file's own header PROSE necessarily names
   *  the functions it must not import, while explaining why. Same stripper
   *  `TxAuxSurface.test.ts` uses for its analogous source-text pin. */
  const withoutComments = (text: string): string => text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
  const source = withoutComments(readFileSync('src/semantic/FilterSurface.svelte', 'utf8'));

  it('imports neither resolveFilterModeConfig nor a PBT/IF-shift formula', () => {
    expect(source).not.toMatch(/resolveFilterModeConfig/);
    expect(source).not.toMatch(/pbtRawToHz/);
    expect(source).not.toMatch(/deriveIfShift/);
  });

  it('holds no runtime, transport, audio, or store import', () => {
    expect(source).not.toMatch(/\$lib\/transport/);
    expect(source).not.toMatch(/audio-manager/);
    expect(source).not.toMatch(/\$lib\/runtime/);
  });
});
