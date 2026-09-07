import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { FiniteControlAppearance } from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import { createFiniteRendererContext } from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import type { FilterFiniteChoiceValue } from '../filter-instruments';
import { topologyFixtures, withFilterPassband, withModeFilter } from '../fixtures/topologies';
import type { ModeFilterViewModel, RadioViewModel } from '../radio-view-model';
import FilterInstrumentHostFixture from './fixtures/FilterInstrumentHostFixture.svelte';

const base = (): RadioViewModel => withFilterPassband(withModeFilter(topologyFixtures['1/single']));
const appearance = {
  action: FiniteControlRendererFixture as FiniteControlAppearance<FilterFiniteChoiceValue>['action'],
  toggle: FiniteControlRendererFixture as FiniteControlAppearance<FilterFiniteChoiceValue>['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance<FilterFiniteChoiceValue>['choice'],
} satisfies FiniteControlAppearance<FilterFiniteChoiceValue>;

let target: HTMLDivElement;
beforeEach(() => {
  target = document.createElement('div'); document.body.appendChild(target); resetRetainedInvocations();
});
afterEach(() => target.remove());

function modeReading(
  view: RadioViewModel, name: 'currentMode' | 'currentFilter',
  reading: { status: 'unknown' } | { status: 'known'; value: string | number },
  operational = true,
): RadioViewModel {
  const modeFilter = view.modeFilter!;
  return { ...view, modeFilter: { ...modeFilter, [name]: {
    ...modeFilter[name], availability: { ...modeFilter[name].availability, operational }, reading,
  } } as ModeFilterViewModel };
}

describe('FilterInstrumentHost Mode and Filter ownership', () => {
  it('renders exact native choices in grouped and independent layouts with pending separate from truth', () => {
    const onModeChange = vi.fn(), onFilterChange = vi.fn();
    const props = proxy({ view: base(), presentation: 'grouped' as 'grouped' | 'independent',
      pendingFilter: 2, onModeChange, onFilterChange });
    const component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    const labels = (id: string) => [...target.querySelectorAll(`[data-testid="${id}"] button`)]
      .map(button => button.textContent);
    expect(target.querySelectorAll('[data-testid="grouped-filter-composition"] button')).toHaveLength(8);
    expect(labels('filter-mode')).toEqual(['USB', 'LSB', 'CW', 'RTTY', 'FM']);
    expect(labels('filter-select')).toEqual(['FIL1', 'FIL2', 'FIL3']);
    expect(target.querySelector('[data-testid="filter-mode-USB"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(target.querySelector('[data-testid="filter-select-1"]')?.getAttribute('aria-pressed')).toBe('true');
    expect((target.querySelector('[data-testid="filter-select-2"]') as HTMLElement).dataset.pending).toBe('true');
    expect(target.querySelector('[data-testid="filter-select"]')?.getAttribute('aria-describedby')).not.toBeNull();
    (target.querySelector('[data-testid="filter-mode-LSB"]') as HTMLButtonElement).click();
    (target.querySelector('[data-testid="filter-select-2"]') as HTMLButtonElement).click();
    expect(onModeChange).toHaveBeenCalledExactlyOnceWith('LSB');
    expect(onFilterChange).toHaveBeenCalledExactlyOnceWith(2);
    props.presentation = 'independent'; flushSync();
    expect(target.querySelector('[data-testid="grouped-filter-composition"]')).toBeNull();
    expect(target.querySelectorAll('[data-testid="independent-filter-composition"] button')).toHaveLength(8);
    expect(target.querySelectorAll('[data-slot="mode"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-slot="filter"]')).toHaveLength(1);
    unmount(component);
  });

  it('keeps external options, raw readings and callbacks current', () => {
    const firstMode = vi.fn(), nextMode = vi.fn(), onFilterChange = vi.fn();
    const props = proxy({
      view: modeReading(base(), 'currentFilter', { status: 'known', value: 99 }),
      presentation: 'independent' as const, finiteAppearance: appearance,
      rendererContext: createFiniteRendererContext(), onModeChange: firstMode, onFilterChange,
    });
    const component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    const filter = target.querySelector('[data-testid="external-Filter"]')!;
    expect(filter.getAttribute('data-reading')).toBe('99');
    expect(filter.querySelector('[aria-checked="true"]')).toBeNull();
    expect([...filter.querySelectorAll('button')].map(button => button.textContent)).toEqual(['FIL1', 'FIL2', 'FIL3']);
    (filter.querySelector('[data-testid="external-Filter-2"]') as HTMLButtonElement).click();
    expect(onFilterChange).toHaveBeenCalledExactlyOnceWith(2);

    props.view = { ...props.view, modeFilter: { ...props.view.modeFilter!,
      currentMode: { ...props.view.modeFilter!.currentMode, reading: { status: 'known', value: 'AM' } },
      modeChoices: ['AM', 'FM'],
    } as ModeFilterViewModel }; props.onModeChange = nextMode; flushSync();
    const mode = target.querySelector('[data-testid="external-Mode"]')!;
    expect([...mode.querySelectorAll('button')].map(button => button.textContent)).toEqual(['AM', 'FM']);
    (mode.querySelector('[data-testid="external-Mode-AM"]') as HTMLButtonElement).click();
    expect(firstMode).not.toHaveBeenCalled(); expect(nextMode).toHaveBeenCalledExactlyOnceWith('AM');
    unmount(component);
  });

  it('revokes null, detached, A-B-A and host-teardown renderer callbacks', () => {
    const onFilterChange = vi.fn(), a = createFiniteRendererContext(), b = createFiniteRendererContext();
    const props = proxy({ view: base(), presentation: 'grouped' as 'grouped' | 'independent',
      finiteAppearance: appearance, rendererContext: null as ReturnType<typeof createFiniteRendererContext> | null,
      onFilterChange });
    const component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    expect(target.querySelector('[data-testid="external-Filter"]')).toBeNull();
    props.rendererContext = a; flushSync(); const groupedA = retainedInvocations.get('Filter')!;
    props.presentation = 'independent'; flushSync(); const independentA = retainedInvocations.get('Filter')!;
    groupedA(2); expect(onFilterChange).not.toHaveBeenCalled();
    independentA(2); expect(onFilterChange).toHaveBeenCalledExactlyOnceWith(2);
    props.rendererContext = b; flushSync(); const activeB = retainedInvocations.get('Filter')!;
    independentA(3); expect(onFilterChange).toHaveBeenCalledTimes(1);
    props.rendererContext = a; flushSync(); const activeA2 = retainedInvocations.get('Filter')!;
    activeB(3); expect(onFilterChange).toHaveBeenCalledTimes(1);
    unmount(component); activeA2(3); expect(onFilterChange).toHaveBeenCalledTimes(1);
  });

  it('uses exact structural gates and retains unavailable readings without selected truth', () => {
    const callbacks = { onModeChange: vi.fn(), onFilterChange: vi.fn() };
    const absentBase = base();
    const absent = { ...absentBase, modeFilter: { ...absentBase.modeFilter!,
      currentMode: { ...absentBase.modeFilter!.currentMode,
        availability: { structural: false, operational: true } },
      currentFilter: { ...absentBase.modeFilter!.currentFilter,
        availability: { structural: false, operational: true } },
    } };
    const props = proxy({ view: absent, presentation: 'independent' as const, ...callbacks });
    let component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    expect(target.querySelectorAll('[data-testid^="filter-"]')).toHaveLength(0);
    unmount(component);

    let retained = modeReading(base(), 'currentMode', { status: 'known', value: 'USB' }, false);
    retained = modeReading(retained, 'currentFilter', { status: 'known', value: 1 }, false);
    props.view = retained;
    component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    for (const id of ['filter-mode-USB', 'filter-select-1']) {
      const button = target.querySelector(`[data-testid="${id}"]`) as HTMLButtonElement;
      expect(button.disabled).toBe(true); expect(button.getAttribute('aria-pressed')).toBe('false'); button.click();
    }
    expect(callbacks.onModeChange).not.toHaveBeenCalled(); expect(callbacks.onFilterChange).not.toHaveBeenCalled();
    unmount(component);

    Object.assign(props, { finiteAppearance: appearance, rendererContext: createFiniteRendererContext() });
    component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    for (const [label, exact] of [['Mode', 'USB'], ['Filter', '1']] as const) {
      const group = target.querySelector(`[data-testid="external-${label}"]`)!;
      expect(group.getAttribute('data-reading')).toBe(exact);
      expect(group.querySelector('[aria-checked="true"]')).toBeNull();
      expect(group.querySelectorAll('button:disabled')).toHaveLength(group.querySelectorAll('button').length);
    }
    retainedInvocations.get('Mode')?.('USB'); retainedInvocations.get('Filter')?.(1);
    expect(callbacks.onModeChange).not.toHaveBeenCalled(); expect(callbacks.onFilterChange).not.toHaveBeenCalled();
    unmount(component);
  });
});
