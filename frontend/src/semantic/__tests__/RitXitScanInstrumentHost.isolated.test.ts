import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { FiniteControlAppearance } from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import { createFiniteRendererContext } from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import type { RadioViewModel, RitXitViewModel } from '../radio-view-model';
import { topologyFixtures, withRitXit, withScan } from '../fixtures/topologies';
import RitXitScanInstrumentHostFixture from './fixtures/RitXitScanInstrumentHostFixture.svelte';

const base = (): RadioViewModel => withScan(withRitXit(topologyFixtures['1/single']));
const appearance = {
  action: FiniteControlRendererFixture as FiniteControlAppearance<string | number>['action'],
  toggle: FiniteControlRendererFixture as FiniteControlAppearance<string | number>['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance<string | number>['choice'],
} satisfies FiniteControlAppearance<string | number>;

let target: HTMLDivElement;
beforeEach(() => {
  target = document.createElement('div'); document.body.appendChild(target); resetRetainedInvocations();
});
afterEach(() => target.remove());

function withRx(view: RadioViewModel, over: Partial<RitXitViewModel>): RadioViewModel {
  return { ...view, ritXit: { ...view.ritXit!, ...over } };
}

describe('RitXitScanInstrumentHost finite ownership', () => {
  it('keeps native RIT, XIT, offset and CLEAR in order across both arrangements', () => {
    const onRitToggle = vi.fn(), onXitToggle = vi.fn(), onClear = vi.fn();
    const props = proxy({ view: base(), presentation: 'grouped' as 'grouped' | 'independent',
      onRitToggle, onXitToggle, onClear });
    const component = mount(RitXitScanInstrumentHostFixture, { target, props }); flushSync();
    const order = () => [...target.querySelectorAll('[data-testid="ritxit"] button, [data-testid="ritxit"] label')]
      .map(element => element.getAttribute('data-testid'));
    expect(order()).toEqual(['ritxit-rit-toggle', 'ritxit-xit-toggle', 'ritxit-offset', 'ritxit-clear']);
    (target.querySelector('[data-testid="ritxit-rit-toggle"]') as HTMLButtonElement).click();
    (target.querySelector('[data-testid="ritxit-xit-toggle"]') as HTMLButtonElement).click();
    (target.querySelector('[data-testid="ritxit-clear"]') as HTMLButtonElement).click();
    expect(onRitToggle).toHaveBeenCalledExactlyOnceWith();
    expect(onXitToggle).toHaveBeenCalledExactlyOnceWith();
    expect(onClear).toHaveBeenCalledExactlyOnceWith();
    props.presentation = 'independent'; flushSync();
    expect(order()).toEqual(['ritxit-rit-toggle', 'ritxit-xit-toggle', 'ritxit-offset', 'ritxit-clear']);
    for (const slot of ['rit', 'xit', 'offset', 'clear']) {
      expect(target.querySelectorAll(`[data-slot="${slot}"]`)).toHaveLength(1);
    }
    unmount(component);
  });

  it('keeps external truth, availability and current callbacks honest', () => {
    const firstRit = vi.fn(), nextRit = vi.fn(), onClear = vi.fn();
    const props = proxy({ view: base(), finiteAppearance: appearance,
      rendererContext: createFiniteRendererContext(), onRitToggle: firstRit, onClear });
    const component = mount(RitXitScanInstrumentHostFixture, { target, props }); flushSync();
    expect(target.querySelector('[data-testid="external-RIT"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(target.querySelector('[data-testid="external-XIT"]')?.getAttribute('aria-pressed')).toBe('false');
    props.view = withRx(props.view, {
      ritActive: { ...props.view.ritXit!.ritActive, reading: { status: 'unknown' } },
      xitActive: { ...props.view.ritXit!.xitActive, reading: { status: 'unknown' } },
    });
    props.onRitToggle = nextRit; flushSync();
    expect(target.querySelector('[data-testid="external-RIT"]')?.hasAttribute('aria-pressed')).toBe(false);
    expect((target.querySelector('[data-testid="external-RIT"]') as HTMLButtonElement).disabled).toBe(true);
    (target.querySelector('[data-testid="external-CLEAR"]') as HTMLButtonElement).click();
    retainedInvocations.get('RIT')?.();
    expect(firstRit).not.toHaveBeenCalled(); expect(nextRit).not.toHaveBeenCalled();
    expect(onClear).toHaveBeenCalledExactlyOnceWith();
    props.view = withRx(props.view, {
      ritActive: { ...props.view.ritXit!.ritActive, reading: { status: 'known', value: false } },
    }); flushSync();
    retainedInvocations.get('RIT')?.();
    expect(firstRit).not.toHaveBeenCalled(); expect(nextRit).toHaveBeenCalledExactlyOnceWith();
    props.view = { ...props.view, activeReceiver: { status: 'unknown' } }; flushSync();
    expect((target.querySelector('[data-testid="external-CLEAR"]') as HTMLButtonElement).disabled).toBe(true);
    retainedInvocations.get('CLEAR')?.(); expect(onClear).toHaveBeenCalledTimes(1);
    unmount(component);
  });

  it('revokes detached, A-B-A and host-teardown renderer callbacks', () => {
    const onRitToggle = vi.fn(), onClear = vi.fn();
    const a = createFiniteRendererContext(), b = createFiniteRendererContext();
    const props = proxy({ view: base(), presentation: 'grouped' as 'grouped' | 'independent',
      finiteAppearance: appearance, rendererContext: null as typeof a | null, onRitToggle, onClear });
    const component = mount(RitXitScanInstrumentHostFixture, { target, props }); flushSync();
    expect(target.querySelector('[data-testid="external-RIT"]')).toBeNull();
    props.rendererContext = a; flushSync();
    const groupedA = retainedInvocations.get('RIT')!;
    props.presentation = 'independent'; flushSync(); const independentA = retainedInvocations.get('RIT')!;
    groupedA(); expect(onRitToggle).not.toHaveBeenCalled();
    independentA(); expect(onRitToggle).toHaveBeenCalledTimes(1);
    props.rendererContext = b; flushSync(); const activeB = retainedInvocations.get('RIT')!;
    independentA(); expect(onRitToggle).toHaveBeenCalledTimes(1);
    props.rendererContext = a; flushSync(); const activeA2 = retainedInvocations.get('RIT')!;
    activeB(); expect(onRitToggle).toHaveBeenCalledTimes(1);
    const clearA2 = retainedInvocations.get('CLEAR')!;
    unmount(component); activeA2(); clearA2();
    expect(onRitToggle).toHaveBeenCalledTimes(1); expect(onClear).not.toHaveBeenCalled();
  });
});
