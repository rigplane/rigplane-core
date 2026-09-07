import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import {
  createFiniteRendererContext, type FiniteControlAppearance,
} from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import {
  createFrequencyEntryRendererSeat,
} from '../../primitives/frequency/frequency-entry-renderer.svelte';
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

const input = () => target.querySelector<HTMLInputElement>('[data-testid="band-entry-input"]')!;
const typeFrequency = (value: string) => {
  input().value = value;
  input().dispatchEvent(new Event('input', { bubbles: true }));
  flushSync();
};

describe('BandInstrumentHost', () => {
  it.each(['grouped', 'independent'] as const)(
    'renders choice then entry exactly once in the %s placement',
    (presentation) => {
      const component = mount(BandInstrumentHostFixture, {
        target, props: { view: base(), presentation },
      });
      flushSync();
      expect(target.querySelectorAll('[data-testid="band-choices"]')).toHaveLength(1);
      expect(target.querySelectorAll('[data-testid="band-choice-20m"]')).toHaveLength(1);
      expect(target.querySelectorAll('[data-testid="band-entry"]')).toHaveLength(1);
      if (presentation === 'independent') {
        expect(target.querySelector('[data-slot="choice"] [data-testid="band-choices"]')).not.toBeNull();
        expect(target.querySelector('[data-slot="entry"] [data-testid="band-entry"]')).not.toBeNull();
      }
      const choice = target.querySelector('[data-testid="band-choices"]')!;
      const entry = target.querySelector('[data-testid="band-entry"]')!;
      expect(choice.compareDocumentPosition(entry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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

  it('keeps one raw draft across placement and entry-context A-B-A replacement', () => {
    const a = createFiniteRendererContext(), b = createFiniteRendererContext();
    const onEnterFrequency = vi.fn();
    const props = proxy({
      view: base(), presentation: 'grouped' as 'grouped' | 'independent',
      entryRendererContext: a, onEnterFrequency,
    });
    const component = mount(BandInstrumentHostFixture, { target, props });
    flushSync();
    typeFrequency('7.100');
    const retainedA = input();

    Object.assign(props, { presentation: 'independent', entryRendererContext: b });
    flushSync();
    retainedA.value = '14.200';
    retainedA.dispatchEvent(new Event('input', { bubbles: true }));
    retainedA.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(input().value).toBe('7.100');
    expect(onEnterFrequency).not.toHaveBeenCalled();

    Object.assign(props, { entryRendererContext: a });
    flushSync();
    retainedA.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(input().value).toBe('7.100');
    typeFrequency('14.200');
    input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(onEnterFrequency).toHaveBeenCalledExactlyOnceWith(14200000);
    unmount(component);
  });

  it('refuses programmatic draft mutation while receiver or bounds are unknown', () => {
    const props = proxy({
      view: { ...base(), activeReceiver: { status: 'unknown' as const } },
      presentation: 'grouped' as 'grouped' | 'independent',
    });
    const component = mount(BandInstrumentHostFixture, { target, props });
    flushSync();
    typeFrequency('7.100');
    Object.assign(props, { presentation: 'independent' });
    flushSync();
    expect(input().value).toBe('');

    Object.assign(props, {
      view: { ...base(), band: { ...base().band!, tuneMinHz: null, tuneMaxHz: null } },
    });
    flushSync();
    typeFrequency('14.200');
    Object.assign(props, { presentation: 'grouped' });
    flushSync();
    expect(input().value).toBe('');
    unmount(component);
  });

  it('clears the draft only after structural Band disappearance', () => {
    const onEnterFrequency = vi.fn();
    const props = proxy({ view: base() as RadioViewModel | null, onEnterFrequency });
    const component = mount(BandInstrumentHostFixture, { target, props });
    flushSync();
    typeFrequency('7100');
    Object.assign(props, { view: { ...base(), activeReceiver: { status: 'unknown' as const } } });
    flushSync();
    expect(input().value).toBe('7100');
    const retainedInput = input();
    const retainedSet = target.querySelector<HTMLButtonElement>('[data-testid="band-entry-set"]')!;
    Object.assign(props, { view: { ...base(), band: undefined } });
    retainedInput.value = '14.200';
    retainedInput.dispatchEvent(new Event('input', { bubbles: true }));
    retainedSet.click();
    expect(onEnterFrequency).not.toHaveBeenCalled();
    flushSync();
    expect(target.querySelector('[data-testid="band-entry"]')).toBeNull();
    Object.assign(props, { view: base() });
    flushSync();
    expect(input().value).toBe('');
    unmount(component);
  });

  it('revokes a specialized lease after context replacement, dispose, and owner destroy', () => {
    let context = createFiniteRendererContext();
    const setDraft = vi.fn(), submit = vi.fn(), cancel = vi.fn(), handleKeyDown = vi.fn();
    const seat = createFrequencyEntryRendererSeat(() => ({
      context,
      view: {
        label: 'FREQ', draft: '', validation: 'empty', boundsAvailable: true,
        interpretedHz: null, inputAvailable: true, submitAvailable: false,
        hint: '', rangeText: '—',
      },
      setDraft, submit, cancel, handleKeyDown,
    }));
    const replaced = seat.attachRenderer();
    context = createFiniteRendererContext();
    expect(replaced.active).toBe(false);
    replaced.setDraft('7100'); replaced.submit(); replaced.cancel();
    expect(setDraft).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();

    const disposed = seat.attachRenderer();
    disposed.dispose();
    expect(disposed.active).toBe(false);
    const destroyed = seat.attachRenderer();
    seat.destroy();
    expect(destroyed.active).toBe(false);
  });
});
