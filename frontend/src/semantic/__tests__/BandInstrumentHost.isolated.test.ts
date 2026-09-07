import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import {
  createFiniteRendererContext, type FiniteControlAppearance,
} from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import { topologyFixtures, withBand } from '../fixtures/topologies';
import type { BandViewModel, RadioViewModel } from '../radio-view-model';
import BandInstrumentHostFixture from './fixtures/BandInstrumentHostFixture.svelte';

const base = (): RadioViewModel => withBand(topologyFixtures['1/single']);
const appearance = {
  action: FiniteControlRendererFixture as FiniteControlAppearance<string>['action'],
  toggle: FiniteControlRendererFixture as FiniteControlAppearance<string>['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance<string>['choice'],
} satisfies FiniteControlAppearance<string>;
const withChoice = (
  view: RadioViewModel, name: string, over: Partial<BandViewModel['bandChoices'][number]>,
): RadioViewModel => ({
  ...view,
  band: {
    ...view.band!,
    bandChoices: view.band!.bandChoices.map(choice => choice.name === name
      ? { ...choice, ...over } : choice),
  },
});

let target: HTMLDivElement;
beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  resetRetainedInvocations();
});
afterEach(() => target.remove());

describe('BandInstrumentHost', () => {
  it.each(['grouped', 'independent'] as const)(
    'renders its choice handle exactly once in the %s placement',
    (presentation) => {
      const component = mount(BandInstrumentHostFixture, {
        target, props: { view: base(), presentation },
      });
      flushSync();
      expect(target.querySelectorAll('[data-testid="band-choices"]')).toHaveLength(1);
      expect(target.querySelectorAll('[data-testid="band-choice-20m"]')).toHaveLength(1);
      if (presentation === 'independent') {
        expect(target.querySelector('[data-slot="choice"] [data-testid="band-choices"]')).not.toBeNull();
      }
      unmount(component);
    },
  );

  it('publishes full current labels and re-resolves the named choice on invocation', () => {
    const onSelectBand = vi.fn();
    const props = proxy({
      view: base(), finiteAppearance: appearance,
      rendererContext: createFiniteRendererContext(), onSelectBand,
    });
    const component = mount(BandInstrumentHostFixture, { target, props });
    flushSync();
    Object.assign(props, {
      view: withChoice(base(), '20m', {
        defaultHz: 14225000, bsrCode: 7,
        defaultHzTxPermit: { status: 'denied', reason: 'outside-configured-ranges' },
      }),
    });
    flushSync();
    const option = target.querySelector('[data-testid="external-Band-20m"]');
    expect(option?.textContent).toContain('20m');
    expect(option?.textContent).toContain('14.225 MHz');
    expect(option?.textContent).toContain('denied');
    retainedInvocations.get('Band')?.('20m');
    expect(onSelectBand).toHaveBeenCalledExactlyOnceWith('20m');
    unmount(component);
  });

  it('keeps denied and unknown TX point samples selectable because band choice only tunes', () => {
    const onSelectBand = vi.fn();
    const view = withChoice(withChoice(base(), '20m', {
      defaultHzTxPermit: { status: 'denied', reason: 'outside-configured-ranges' },
    }), '40m', { defaultHzTxPermit: { status: 'unknown', reason: 'ranges-unconfigured' } });
    const component = mount(BandInstrumentHostFixture, { target, props: { view, onSelectBand } });
    flushSync();
    for (const name of ['20m', '40m']) {
      const button = target.querySelector<HTMLButtonElement>(`[data-testid="band-choice-${name}"]`)!;
      expect(button.disabled).toBe(false);
      button.click();
    }
    expect(onSelectBand.mock.calls).toEqual([['20m'], ['40m']]);
    unmount(component);
  });

  it('revokes retained external callbacks across null and A-B-A context replacement', () => {
    const a = createFiniteRendererContext(), b = createFiniteRendererContext();
    const onSelectBand = vi.fn();
    const props = proxy({ view: base(), finiteAppearance: appearance, rendererContext: a, onSelectBand });
    const component = mount(BandInstrumentHostFixture, { target, props });
    flushSync();
    const retainedA = retainedInvocations.get('Band')!;
    Object.assign(props, { rendererContext: null });
    flushSync();
    retainedA('20m');
    Object.assign(props, { rendererContext: b });
    flushSync();
    retainedA('20m');
    Object.assign(props, { rendererContext: a });
    flushSync();
    retainedA('20m');
    expect(onSelectBand).not.toHaveBeenCalled();
    const liveA = retainedInvocations.get('Band')!;
    liveA('20m');
    expect(onSelectBand).toHaveBeenCalledExactlyOnceWith('20m');
    unmount(component);
    liveA('40m');
    expect(onSelectBand).toHaveBeenCalledTimes(1);
  });

  it('disables and refuses band choice while the active receiver is unknown', () => {
    const onSelectBand = vi.fn();
    const view = { ...base(), activeReceiver: { status: 'unknown' as const } };
    const component = mount(BandInstrumentHostFixture, { target, props: { view, onSelectBand } });
    flushSync();
    const button = target.querySelector<HTMLButtonElement>('[data-testid="band-choice-20m"]')!;
    expect(button.disabled).toBe(true);
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(onSelectBand).not.toHaveBeenCalled();
    unmount(component);
  });
});
