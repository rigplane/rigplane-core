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
import type { FilterPassbandViewModel, ModeFilterViewModel, RadioViewModel } from '../radio-view-model';
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

function reading(
  view: RadioViewModel, group: 'modeFilter' | 'filterPassband', name: string,
  next: { status: 'unknown' } | { status: 'known'; value: string | number }, operational = true,
): RadioViewModel {
  const current = view[group] as unknown as Record<string, unknown>;
  const field = current[name] as { availability: { structural: boolean; operational: boolean } };
  return { ...view, [group]: { ...current, [name]: {
    ...field, availability: { ...field.availability, operational }, reading: next,
  } } } as RadioViewModel;
}

describe('FilterInstrumentHost finite ownership', () => {
  it('renders Standard MOD IN from confirmed truth while pending stays distinct', () => {
    const onModInputChange = vi.fn();
    const props = proxy({ view: base(), presentation: 'standard' as const,
      pendingModInput: 3 as number | null, onModInputChange });
    const component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    const select = target.querySelector<HTMLSelectElement>('[data-testid="mod-input-select"]')!;
    expect([...select.options].map(option => option.text)).toEqual(['MIC', 'USB']);
    expect(select.value).toBe('0');
    expect(select.dataset.pendingValue).toBe('3');
    expect(select.closest('[data-mod-input-status]')?.getAttribute('data-mod-input-status')).toBe('pending');
    select.value = '3'; select.dispatchEvent(new Event('change', { bubbles: true }));
    expect(onModInputChange).toHaveBeenCalledExactlyOnceWith(3);
    props.pendingModInput = null;
    props.view = reading(base(), 'filterPassband', 'modInputSource', { status: 'unknown' }, false);
    flushSync();
    const unknown = target.querySelector<HTMLSelectElement>('[data-testid="mod-input-select"]')!;
    expect(unknown.disabled).toBe(true);
    expect(unknown.value).toBe('');
    expect([...unknown.options].map(option => option.text)).toEqual(['—', 'MIC', 'USB']);
    expect(unknown.closest('[data-mod-input-status]')?.getAttribute('data-mod-input-status')).toBe('unknown');
    unmount(component);
  });
  it('renders exact native choices in grouped and independent arrangements with pending separate from truth', () => {
    const onModeChange = vi.fn(), onFilterChange = vi.fn();
    const onFilterShapeChange = vi.fn(), onDataModeChange = vi.fn();
    const props = proxy({ view: base(), presentation: 'grouped' as 'grouped' | 'independent',
      pendingFilter: 2, pendingDataMode: 1,
      onModeChange, onFilterChange, onFilterShapeChange, onDataModeChange });
    const component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    expect(target.querySelectorAll('[data-testid="grouped-filter-composition"] button')).toHaveLength(12);
    const labels = (id: string) => [...target.querySelectorAll(`[data-testid="${id}"] button`)]
      .map(button => button.textContent);
    expect(labels('filter-mode')).toEqual(['USB', 'LSB', 'CW', 'RTTY', 'FM']);
    expect(labels('filter-select')).toEqual(['FIL1', 'FIL2', 'FIL3']);
    expect(labels('filter-shape')).toEqual(['SHARP', 'SOFT']);
    expect(labels('filter-data-mode')).toEqual(['OFF', 'D1']);
    expect(target.querySelector('[data-testid="filter-mode-USB"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(target.querySelector('[data-testid="filter-select-1"]')?.getAttribute('aria-pressed')).toBe('true');
    expect((target.querySelector('[data-testid="filter-select-2"]') as HTMLElement).dataset.pending).toBe('true');
    expect(target.querySelector('[data-testid="filter-data-mode-0"]')?.getAttribute('aria-pressed')).toBe('true');
    expect((target.querySelector('[data-testid="filter-data-mode-1"]') as HTMLElement).dataset.pending).toBe('true');
    expect(target.querySelector('[data-testid="filter-mode"]')?.getAttribute('aria-describedby')).toBeNull();
    expect(target.querySelector('[data-testid="filter-shape"]')?.getAttribute('aria-describedby')).toBeNull();
    expect(target.querySelector('[data-testid="filter-select"]')?.getAttribute('aria-describedby')).not.toBeNull();
    expect(target.querySelector('[data-testid="filter-data-mode"]')?.getAttribute('aria-describedby')).not.toBeNull();
    for (const id of ['filter-mode-LSB', 'filter-select-2', 'filter-shape-1', 'filter-data-mode-1'])
      (target.querySelector(`[data-testid="${id}"]`) as HTMLButtonElement).click();
    expect(onModeChange).toHaveBeenCalledExactlyOnceWith('LSB');
    expect(onFilterChange).toHaveBeenCalledExactlyOnceWith(2);
    expect(onFilterShapeChange).toHaveBeenCalledExactlyOnceWith(1);
    expect(onDataModeChange).toHaveBeenCalledExactlyOnceWith(1);
    props.presentation = 'independent'; flushSync();
    expect(target.querySelector('[data-testid="grouped-filter-composition"]')).toBeNull();
    expect(target.querySelectorAll('[data-testid="independent-filter-composition"] button')).toHaveLength(12);
    unmount(component);
  });

  it('keeps external options and callbacks current without coercing DATA2 or unknown truth', () => {
    const firstMode = vi.fn(), nextMode = vi.fn(), onDataModeChange = vi.fn();
    let view = reading(base(), 'filterPassband', 'dataMode', { status: 'known', value: 2 });
    view = reading(view, 'filterPassband', 'filterShape', { status: 'unknown' });
    const props = proxy({ view, presentation: 'independent' as const, finiteAppearance: appearance,
      rendererContext: createFiniteRendererContext(), onModeChange: firstMode, onDataModeChange });
    const component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    const data = target.querySelector('[data-testid="external-DATA mode"]')!;
    expect(data.getAttribute('data-reading')).toBe('2');
    expect(data.querySelector('[aria-checked="true"]')).toBeNull();
    expect([...data.querySelectorAll('button')].map(button => button.textContent)).toEqual(['OFF', 'D1']);
    (data.querySelector('[data-testid="external-DATA mode-0"]') as HTMLButtonElement).click();
    (data.querySelector('[data-testid="external-DATA mode-1"]') as HTMLButtonElement).click();
    expect(onDataModeChange.mock.calls).toEqual([[0], [1]]);
    expect(target.querySelector('[data-testid="external-Filter shape"]')?.querySelectorAll('button:disabled')).toHaveLength(2);

    props.view = { ...props.view, modeFilter: { ...props.view.modeFilter!,
      currentMode: { ...props.view.modeFilter!.currentMode, reading: { status: 'known', value: 'AM' } },
      modeChoices: ['AM', 'FM'],
    } as ModeFilterViewModel }; props.onModeChange = nextMode; flushSync();
    const mode = target.querySelector('[data-testid="external-Mode"]')!;
    expect([...mode.querySelectorAll('button')].map(button => button.textContent)).toEqual(['AM', 'FM']);
    (mode.querySelector('[data-testid="external-Mode-AM"]') as HTMLButtonElement).click();
    expect(firstMode).not.toHaveBeenCalled(); expect(nextMode).toHaveBeenCalledExactlyOnceWith('AM');
    props.view = { ...props.view, filterPassband: { ...props.view.filterPassband!,
      dataModeChoices: [{ value: 0, label: null }],
    } as FilterPassbandViewModel }; flushSync();
    expect((target.querySelector('[data-testid="external-DATA mode-0"]') as HTMLButtonElement).disabled).toBe(true);
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

  it('uses each exact structural gate and keeps present unavailable fields inert', () => {
    const unavailable = { structural: false, operational: true };
    let view = base();
    view = { ...view, modeFilter: { ...view.modeFilter!, currentMode: { ...view.modeFilter!.currentMode,
      availability: unavailable }, currentFilter: { ...view.modeFilter!.currentFilter, availability: unavailable } },
      filterPassband: { ...view.filterPassband!, filterShapeControlStructural: false,
        dataMode: { ...view.filterPassband!.dataMode, availability: unavailable } } };
    const props = proxy({ view, presentation: 'independent' as const });
    const component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    expect(target.querySelectorAll('[data-testid^="filter-"]')).toHaveLength(0);

    const shapeFactAbsent = base();
    props.view = { ...shapeFactAbsent, filterPassband: { ...shapeFactAbsent.filterPassband!,
      filterShape: { ...shapeFactAbsent.filterPassband!.filterShape,
        availability: { structural: false, operational: true } } } }; flushSync();
    expect(target.querySelector('[data-testid="filter-shape"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-testid="filter-shape"] button:not(:disabled)')).toHaveLength(0);

    view = reading(base(), 'modeFilter', 'currentMode', { status: 'unknown' });
    view = reading(view, 'modeFilter', 'currentFilter', { status: 'known', value: 1 }, false);
    view = reading(view, 'filterPassband', 'filterShape', { status: 'unknown' });
    view = reading(view, 'filterPassband', 'dataMode', { status: 'known', value: 0 }, false);
    props.view = view; flushSync();
    const groups = ['filter-mode', 'filter-select', 'filter-shape', 'filter-data-mode'];
    for (const id of groups) {
      const group = target.querySelector(`[data-testid="${id}"]`)!;
      expect(group.getAttribute('data-disabled-reason')).toBe('field-not-observed');
      expect(group.querySelectorAll('button:not(:disabled)')).toHaveLength(0);
    }
    unmount(component);
  });

  it('retains all four known readings without selected truth while unavailable', () => {
    const onModeChange = vi.fn(), onFilterChange = vi.fn();
    const onFilterShapeChange = vi.fn(), onDataModeChange = vi.fn();
    const callbacks = { onModeChange, onFilterChange, onFilterShapeChange, onDataModeChange };
    let view = reading(base(), 'modeFilter', 'currentMode', { status: 'known', value: 'USB' }, false);
    view = reading(view, 'modeFilter', 'currentFilter', { status: 'known', value: 1 }, false);
    view = reading(view, 'filterPassband', 'filterShape', { status: 'known', value: 1 }, false);
    view = reading(view, 'filterPassband', 'dataMode', { status: 'known', value: 0 }, false);
    const props = proxy({ view, presentation: 'independent' as const, ...callbacks });
    let component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();

    for (const id of ['filter-mode-USB', 'filter-select-1', 'filter-shape-1', 'filter-data-mode-0']) {
      const button = target.querySelector(`[data-testid="${id}"]`) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      expect(button.getAttribute('aria-pressed')).toBe('false');
      button.click();
    }
    expect(target.querySelector('[data-testid="filter-data-mode"] output')?.textContent).toBe('0');
    for (const callback of Object.values(callbacks)) expect(callback).not.toHaveBeenCalled();
    unmount(component);

    Object.assign(props, { finiteAppearance: appearance,
      rendererContext: createFiniteRendererContext() });
    component = mount(FilterInstrumentHostFixture, { target, props }); flushSync();
    const retained = [['Mode', 'USB'], ['Filter', '1'], ['Filter shape', '1'], ['DATA mode', '0']] as const;
    for (const [label, exactReading] of retained) {
      const group = target.querySelector(`[data-testid="external-${label}"]`)!;
      expect(group.getAttribute('data-reading')).toBe(exactReading);
      expect(group.querySelector('[aria-checked="true"]')).toBeNull();
      expect(group.querySelectorAll('button:disabled')).toHaveLength(group.querySelectorAll('button').length);
    }
    retainedInvocations.get('Mode')?.('USB'); retainedInvocations.get('Filter')?.(1);
    retainedInvocations.get('Filter shape')?.(1); retainedInvocations.get('DATA mode')?.(0);
    for (const callback of Object.values(callbacks)) expect(callback).not.toHaveBeenCalled();

    props.view = base(); flushSync();
    for (const label of ['Mode', 'Filter', 'Filter shape', 'DATA mode'])
      expect(target.querySelector(`[data-testid="external-${label}"] [aria-checked="true"]`)).not.toBeNull();
    unmount(component);
  });
});
