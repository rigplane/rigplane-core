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

/**
 * MOR-1494 review round. `ifShiftControlStructural` is a plain boolean
 * SIBLING of `ifShift` on `filterPassband` (see `radio-view-model.ts`'s
 * `FilterPassbandViewModel` doc comment) — not a `FilterPassbandField`, so
 * `withPassbandField` above (which only knows the `{reading, availability}`
 * shape) cannot set it. A dedicated helper, same idiom as
 * `withModeFilterField`/`withPassbandField`.
 */
function withIfShiftControlStructural(view: RadioViewModel, value: boolean): RadioViewModel {
  return {
    ...view,
    filterPassband: { ...view.filterPassband!, ifShiftControlStructural: value } as FilterPassbandViewModel,
  };
}

/**
 * MOR-1502 review round. `filterShapeControlStructural` is a plain boolean
 * SIBLING of `filterShape` on `filterPassband` (same idiom as
 * `withIfShiftControlStructural` above, for the same reason
 * `withPassbandField` cannot set it).
 */
function withFilterShapeControlStructural(view: RadioViewModel, value: boolean): RadioViewModel {
  return {
    ...view,
    filterPassband: { ...view.filterPassband!, filterShapeControlStructural: value } as FilterPassbandViewModel,
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

function render(view: RadioViewModel, handlers: Handlers = {}, extra: { pendingFilter?: number | null } = {}) {
  const component = mount(FilterSurface, { target, props: { view, ...extra, ...handlers } });
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
  extra: { pendingFilter?: number | null } = {},
): void {
  const s = render(view, handlers, extra);
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

  // `filterShape` is deliberately excluded from this generic sweep (MOR-1502
  // review round) — its ROW gates on the separate
  // `filterShapeControlStructural` flag, not on its OWN field's
  // `availability.structural`, so setting that to ABSENT here would no
  // longer hide it. See the dedicated "filter-shape control gate is
  // separate from the derived fact" block below.

  it('renders no dataMode readout when dataMode is structurally absent', () => {
    const view = withPassbandField(base(), 'dataMode', { availability: ABSENT });
    withSurface(view, (s) => expect(s.group('filter-data-mode')).toBeNull());
  });

  // `ifShift` is deliberately excluded from this generic sweep (MOR-1494
  // review round) — its ROW gates on the separate `ifShiftControlStructural`
  // flag, not on its OWN field's `availability.structural`, so setting that
  // to ABSENT here would no longer hide it. See the dedicated
  // "IF-shift control gate is separate from the derived fact" block below.
  it.each(FILTER_PASSBAND_LEVELS.filter(([field]) => field !== 'ifShift'))(
    'renders no "%s" control when structurally absent',
    (field) => {
      const view = withPassbandField(base(), field, { availability: ABSENT });
      withSurface(view, (s) => expect(s.group(`filter-${field}`)).toBeNull());
    },
  );
});

// ── 2b. IF-shift control gate is separate from the derived fact (MOR-1494) ──

/**
 * MOR-1494 review round. IC-7300 (PBT-only, no `if_shift` command) rendered
 * the IF Shift row permanently disabled with a PBT-derived stand-in value —
 * a dead control shown instead of hidden. The fix splits the presentation
 * gate (`ifShiftControlStructural`, this block) from the derived-fact gate
 * (`ifShift.availability.structural`, UNCHANGED — pinned untouched by the
 * last test below, which is the trap a naive fix would have missed:
 * `scope-adapter.ts`'s passband-center overlay reads `filterPassband.ifShift`
 * directly and must keep getting the PBT-derived reading for a radio just
 * like this one).
 */
describe('IF-shift control gate is separate from the derived fact (MOR-1494)', () => {
  it('IC-7300-shaped (pbt, no if_shift): hides the ifShift row', () => {
    const view = withIfShiftControlStructural(base(), false);
    withSurface(view, (s) => expect(s.group('filter-ifShift')).toBeNull());
  });

  it('IC-7300-shaped (pbt, no if_shift): the underlying derived ifShift FACT stays structural and known — scope-adapter.ts still gets it', () => {
    const view = withIfShiftControlStructural(base(), false);
    // `ifShiftControlStructural: false` here mirrors what the adapter would
    // compute for a pbt-only radio; `ifShift`'s OWN field is UNTOUCHED by
    // this override and stays exactly what `base()` (a "fully observed"
    // fixture) gives it — the row-hiding change above must never reach into
    // this field.
    expect(view.filterPassband!.ifShift.availability.structural).toBe(true);
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'known', value: 0 });
  });

  it('FTX-1-shaped (if_shift, no pbt): renders the ifShift row, enabled, bound to the real value', () => {
    // base() defaults ifShiftControlStructural to true (see fixtures/
    // topologies.ts) — the FTX-1-shaped case, no override needed.
    withSurface(base(), (s) => {
      expect(s.group('filter-ifShift')).not.toBeNull();
      expect(s.input('filter-ifShift')!.disabled).toBe(false);
    });
  });

  it('neither if_shift nor pbt: hides the ifShift row, no crash', () => {
    let view = withIfShiftControlStructural(base(), false);
    view = withPassbandField(view, 'ifShift', {
      availability: { structural: false, operational: false },
    });
    withSurface(view, (s) => expect(s.group('filter-ifShift')).toBeNull());
  });

  it('still applies operational gating (disabled + reason) to a structurally-shown ifShift row', () => {
    const view = withPassbandField(base(), 'ifShift', {
      availability: { structural: true, operational: false },
    });
    withSurface(view, (s) => {
      expect(s.group('filter-ifShift')).not.toBeNull();
      expect(s.input('filter-ifShift')!.disabled).toBe(true);
      expect(s.group('filter-ifShift')!.dataset.disabledReason).toBe('field-not-observed');
    });
  });
});

// ── 2c. filter-shape control gate is separate from the derived fact (MOR-1502) ─

/**
 * MOR-1502. The FTX-1 (no `filter_shape` command) rendered the SHARP/SOFT
 * shape row permanently disabled — a dead control shown instead of hidden,
 * same class of defect MOR-1494 fixed for the IF-shift row. The fix splits
 * the presentation gate (`filterShapeControlStructural`, this block) from
 * the derived-fact gate (`filterShape.availability.structural`, UNCHANGED —
 * pinned untouched by the second test below, which is the trap a naive fix
 * would have missed: `scope-adapter.ts` reads `filterPassband.filterShape`
 * directly and must keep getting the reading for any radio whose capability
 * set declares filters at all, filter_shape-capable or not).
 */
describe('filter-shape control gate is separate from the derived fact (MOR-1502)', () => {
  it('FTX-1-shaped (filters, no filter_shape): hides the filter-shape row', () => {
    const view = withFilterShapeControlStructural(base(), false);
    withSurface(view, (s) => expect(s.group('filter-shape')).toBeNull());
  });

  it('FTX-1-shaped (filters, no filter_shape): the underlying derived filterShape FACT stays structural and known — scope-adapter.ts still gets it', () => {
    const view = withFilterShapeControlStructural(base(), false);
    // `filterShapeControlStructural: false` here mirrors what the adapter
    // would compute for an FTX-1-shaped radio; `filterShape`'s OWN field is
    // UNTOUCHED by this override and stays exactly what `base()` (a "fully
    // observed" fixture) gives it — the row-hiding change above must never
    // reach into this field.
    expect(view.filterPassband!.filterShape.availability.structural).toBe(true);
    expect(view.filterPassband!.filterShape.reading).toEqual({ status: 'known', value: 1 });
  });

  it('IC-7300-shaped (filter_shape): renders the filter-shape row, enabled, bound to the real value', () => {
    // base() defaults filterShapeControlStructural to true (see fixtures/
    // topologies.ts) — the IC-7300-shaped case, no override needed.
    withSurface(base(), (s) => {
      expect(s.group('filter-shape')).not.toBeNull();
      expect(s.button('filter-shape', 1)!.disabled).toBe(false);
    });
  });

  it('no filters at all: hides the filter-shape row, no crash', () => {
    let view = withFilterShapeControlStructural(base(), false);
    view = withPassbandField(view, 'filterShape', {
      availability: { structural: false, operational: false },
    });
    withSurface(view, (s) => expect(s.group('filter-shape')).toBeNull());
  });

  it('still applies operational gating (disabled + reason) to a structurally-shown filter-shape row', () => {
    const view = withPassbandField(base(), 'filterShape', {
      availability: { structural: true, operational: false },
    });
    withSurface(view, (s) => {
      expect(s.group('filter-shape')).not.toBeNull();
      expect(s.button('filter-shape', 1)!.disabled).toBe(true);
      expect(s.group('filter-shape')!.dataset.disabledReason).toBe('field-not-observed');
    });
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

  // `ifShift` stays in this sweep (unlike the structural-gating one above,
  // MOR-1494 review round): `base()`'s `ifShiftControlStructural` is true
  // and this override never touches it, so the row still shows; only the
  // field's own `operational` (unaffected by the MOR-1494 split) decides
  // whether it's disabled here.
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

  /**
   * MOR-1304 fix round (verify-MOR-1304 F3) — `HTMLElement.click()` is a
   * no-op on a `disabled` button in jsdom (and in every real browser): the
   * click-activation steps never run, so the handler's OWN guard
   * (`usable(...)` in `selectMode`/`selectFilter`/`selectShape`) is never
   * actually exercised — `disabled` alone would satisfy an assertion that
   * `onXChange` was never called, even with the guard deleted. Dispatching
   * the `MouseEvent` directly bypasses that suppression and reaches the
   * `onclick` handler regardless of `disabled`, so these tests can tell
   * "the button is disabled" apart from "the guard inside the handler holds"
   * — MF3/MF12/MF13 in the verify report, each SURVIVED under `.click()`.
   */
  function forceClick(button: HTMLButtonElement): void {
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  }

  // MUTATION KILLED (MF3): `selectMode`'s `usable(modeFilter.currentMode)`
  // guard dropped — clicking would arm a guess rather than a confirmed
  // selection.
  it('emits nothing when the mode is clicked on an unobserved reading', () => {
    const onModeChange = vi.fn();
    const view = withModeFilterField(base(), 'currentMode', { unknown: true });
    withSurface(view, (s) => {
      forceClick(s.button('filter-mode', 'LSB')!);
      flushSync();
      expect(onModeChange).not.toHaveBeenCalled();
    }, { onModeChange });
  });

  // MUTATION KILLED (MF12): `selectFilter`'s `usable(modeFilter.currentFilter)`
  // guard dropped.
  it('emits nothing when a filter is clicked on an unobserved reading', () => {
    const onFilterChange = vi.fn();
    const view = withModeFilterField(base(), 'currentFilter', { unknown: true });
    withSurface(view, (s) => {
      forceClick(s.button('filter-select', 2)!);
      flushSync();
      expect(onFilterChange).not.toHaveBeenCalled();
    }, { onFilterChange });
  });

  // MUTATION KILLED (MF13): `selectShape`'s `usable(filterPassband.filterShape)`
  // guard dropped.
  it('emits nothing when a shape is clicked on an unobserved reading', () => {
    const onFilterShapeChange = vi.fn();
    const view = withPassbandField(base(), 'filterShape', { unknown: true });
    withSurface(view, (s) => {
      forceClick(s.button('filter-shape', 1)!);
      flushSync();
      expect(onFilterShapeChange).not.toHaveBeenCalled();
    }, { onFilterShapeChange });
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

  // MUTATION KILLED (MF9, verify-MOR-1304 F3): `changeWidth`'s
  // `usable(modeFilter.filterWidth)` guard dropped. No prior test exercised
  // this — the slider's `disabled` attribute was never in question here, so
  // an `input` event dispatched directly (never suppressed for a disabled
  // range input the way `.click()` is for a disabled button) reaches the
  // handler and proves the guard itself, not merely the DOM attribute.
  it('emits nothing when the width slider is moved on an unobserved reading', () => {
    const onFilterWidthChange = vi.fn();
    const view = withModeFilterField(base(), 'filterWidth', { unknown: true });
    withSurface(view, (s) => {
      const input = s.input('filter-width')!;
      input.value = '2800';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      flushSync();
      expect(onFilterWidthChange).not.toHaveBeenCalled();
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
      // MUTATION KILLED (MF1b): `numberOf(filterPassband[field], min)`'s
      // fallback swapped for a non-min literal (e.g. 999) — an unread thumb
      // is free to claim any position unless the fallback itself is pinned,
      // BEFORE the dispatched input below overwrites it.
      expect(input.value).toBe('-1200');
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
      // MUTATION KILLED (MF1): `numberOf(modeFilter.filterWidth, 0)`'s
      // fallback swapped for a non-zero literal — an unread thumb is free to
      // claim any position unless the fallback itself is pinned. The DOM
      // clamps the fallback `0` to the slider's own `min` (`50`, this view's
      // known `filterWidthMin`), which is itself the honest rendered
      // position and still distinguishes the fallback from a fabricated
      // literal like `777` (which sits inside [50, 3600] and would NOT clamp).
      expect(s.input('filter-width')!.value).toBe('50');
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

// ── 8b. Pending-target affordance (MOR-1441 leg 2) ──────────────────────────

describe('pending-target affordance (MOR-1441 leg 2)', () => {
  // base(): currentFilter known(1) — 'FIL1'.
  it('marks only the pending choice, leaves the CONFIRMED choice aria-pressed, and marks the group', () => {
    withSurface(base(), (s) => {
      const group = s.group('filter-select')!;
      expect(group.dataset.filterStatus).toBe('pending');
      expect(s.button('filter-select', 1)!.getAttribute('aria-pressed')).toBe('true');
      expect(s.button('filter-select', 1)!.dataset.pending).toBe('false');
      expect(s.button('filter-select', 3)!.getAttribute('aria-pressed')).toBe('false');
      expect(s.button('filter-select', 3)!.dataset.pending).toBe('true');
    }, {}, { pendingFilter: 3 });
  });

  it('renders confirmed status and no pending marker when nothing is pending', () => {
    withSurface(base(), (s) => {
      const group = s.group('filter-select')!;
      expect(group.dataset.filterStatus).toBe('confirmed');
      for (const value of [1, 2, 3]) expect(s.button('filter-select', value)!.dataset.pending).toBe('false');
    });
  });

  it('renders a screen-reader announcement only while pending', () => {
    withSurface(base(), (s) => {
      expect(s.group('filter-select')!.querySelector('.sr-only')).not.toBeNull();
    }, {}, { pendingFilter: 3 });
    withSurface(base(), (s) => {
      expect(s.group('filter-select')!.querySelector('.sr-only')).toBeNull();
    });
  });

  // THE seam test (MOR-1441 leg-1 lesson applied to a discrete control): a
  // click while a DIFFERENT choice is pending must still dispatch the
  // CLICKED value verbatim — never something read off the pending display.
  // Unlike the frequency digits' arithmetic base, a choice button's value is
  // always the literal clicked value, so this pins that clicking anywhere
  // OTHER than the pending choice is unaffected by it, and clicking the
  // pending choice itself dispatches THAT explicit value too (never
  // suppressed, never silently reinterpreted).
  it('SEAM: clicking a choice while a DIFFERENT value is pending dispatches the CLICKED value, unaffected by pending', () => {
    const onFilterChange = vi.fn();
    withSurface(base(), (s) => {
      s.button('filter-select', 2)!.click();
      flushSync();
      expect(onFilterChange).toHaveBeenCalledExactlyOnceWith(2);
    }, { onFilterChange }, { pendingFilter: 3 });
  });

  it('SEAM: clicking the PENDING choice itself still dispatches it explicitly, never suppressed', () => {
    const onFilterChange = vi.fn();
    withSurface(base(), (s) => {
      s.button('filter-select', 3)!.click();
      flushSync();
      expect(onFilterChange).toHaveBeenCalledExactlyOnceWith(3);
    }, { onFilterChange }, { pendingFilter: 3 });
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
