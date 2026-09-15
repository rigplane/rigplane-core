import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { FiniteControlAppearance } from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import { createFiniteRendererContext } from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import type { DspFiniteChoiceValue } from '../dsp-instruments';
import type { DspViewModel, RadioViewModel } from '../radio-view-model';
import { topologyFixtures, withDsp } from '../fixtures/topologies';
import DspInstrumentHostFixture from './fixtures/DspInstrumentHostFixture.svelte';

const base = (): RadioViewModel => withDsp(topologyFixtures['1/single']);
const appearance = {
  action: FiniteControlRendererFixture as FiniteControlAppearance<DspFiniteChoiceValue>['action'],
  toggle: FiniteControlRendererFixture as FiniteControlAppearance<DspFiniteChoiceValue>['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance<DspFiniteChoiceValue>['choice'],
} satisfies FiniteControlAppearance<DspFiniteChoiceValue>;

let target: HTMLDivElement;
beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  resetRetainedInvocations();
});
afterEach(() => target.remove());

function field(view: RadioViewModel, name: keyof DspViewModel, reading: unknown): RadioViewModel {
  return {
    ...view,
    dsp: { ...view.dsp!, [name]: { ...view.dsp![name], reading } } as DspViewModel,
  };
}

describe('DspInstrumentHost finite ownership', () => {
  it('keeps all four native handles honest across grouped and independent arrangements', () => {
    const onToggle = vi.fn();
    const props = proxy({
      view: base(), presentation: 'grouped' as 'grouped' | 'independent',
      pendingNr: false, onToggle,
    });
    const component = mount(DspInstrumentHostFixture, { target, props });
    flushSync();
    const nr = target.querySelector<HTMLButtonElement>('[data-testid="dsp-nrActive"]')!;
    expect(target.querySelectorAll('[data-testid="dsp-surface"] button')).toHaveLength(8);
    expect(nr.getAttribute('aria-pressed')).toBe('true');
    expect(nr.dataset.pendingStatus).toBe('pending');
    nr.click();
    expect(onToggle).toHaveBeenCalledExactlyOnceWith('nrActive', false);

    props.presentation = 'independent';
    flushSync();
    expect(target.querySelector('[data-testid="dsp-surface"]')).toBeNull();
    expect(target.querySelectorAll('[data-testid="independent-dsp-composition"] button')).toHaveLength(8);
    expect(target.querySelector('[data-testid="dsp-nrActive"]')?.getAttribute('aria-pressed')).toBe('true');
    unmount(component);
  });

  it('revokes real A-B-A external callbacks while same-context facts stay live', () => {
    const onToggle = vi.fn();
    const a = createFiniteRendererContext(), b = createFiniteRendererContext();
    const props = proxy({
      view: base(), presentation: 'independent' as const, finiteAppearance: appearance,
      rendererContext: a, onToggle,
    });
    const component = mount(DspInstrumentHostFixture, { target, props });
    flushSync();
    const staleA = retainedInvocations.get('NR')!;
    expect(target.querySelector('[data-testid="external-NR"]')?.getAttribute('aria-pressed')).toBe('true');

    props.rendererContext = b;
    flushSync();
    const activeB = retainedInvocations.get('NR')!;
    staleA();
    expect(onToggle).not.toHaveBeenCalled();
    props.view = field(props.view, 'nrActive', { status: 'known', value: false });
    flushSync();
    expect(target.querySelector('[data-testid="external-NR"]')?.getAttribute('aria-pressed')).toBe('false');
    activeB();
    expect(onToggle).toHaveBeenCalledExactlyOnceWith('nrActive', true);

    props.rendererContext = a;
    flushSync();
    activeB();
    expect(onToggle).toHaveBeenCalledTimes(1);
    const activeA2 = retainedInvocations.get('NR')!;
    unmount(component);
    activeA2();
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('keeps a selected external appearance inert without renderer authority', () => {
    const component = mount(DspInstrumentHostFixture, { target, props: {
      view: base(), presentation: 'independent', finiteAppearance: appearance, rendererContext: null,
    } });
    flushSync();
    expect(target.querySelectorAll('[data-testid^="external-"]')).toHaveLength(0);
    expect(target.querySelectorAll('[data-testid^="dsp-"]')).toHaveLength(0);
    unmount(component);
  });

  it('does not select an offered choice for out-of-list or unknown truth', () => {
    const onAgcModeChange = vi.fn(), onNotchModeChange = vi.fn();
    let view = field(base(), 'agcMode', { status: 'known', value: 99 });
    view = field(view, 'notchMode', { status: 'unknown' });
    const component = mount(DspInstrumentHostFixture, { target, props: {
      view, presentation: 'independent', finiteAppearance: appearance,
      rendererContext: createFiniteRendererContext(), onAgcModeChange, onNotchModeChange,
    } });
    flushSync();
    const agc = target.querySelector('[data-testid="external-AGC mode"]')!;
    expect(agc.getAttribute('data-reading')).toBe('99');
    expect(agc.querySelector('[aria-checked="true"]')).toBeNull();
    const notch = target.querySelector('[data-testid="external-Notch mode"]')!;
    expect(notch.getAttribute('data-reading')).toBe('unknown');
    expect(notch.querySelectorAll('button:disabled')).toHaveLength(3);
    (agc.querySelector('button') as HTMLButtonElement).click();
    expect(onAgcModeChange).toHaveBeenCalledExactlyOnceWith(1);
    expect(onNotchModeChange).not.toHaveBeenCalled();
    unmount(component);
  });
});
