import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its test-only effect harness.
import { effect_root } from 'svelte/internal/client';
import { fromStore, writable } from 'svelte/store';
import type { FrequencyRenderer } from '../../../../component-kit-api/src/index';

const selectedFrequency = vi.hoisted(() => ({ current: undefined as unknown }));
vi.mock('../../../component-kits/activation', () => ({
  getSelectedFrequencyReadout: () => selectedFrequency.current,
}));

import FrequencyRendererSeat from '../FrequencyRendererSeat.svelte';
import { createFrequencyInstrumentBinding } from '../frequency-instrument.svelte';
import type { FrequencyInteraction } from '../frequency-interaction.svelte';
import AlternateFrequencyReadoutHarness, {
  clearRetainedInteractions,
  retainedInteractions,
} from './AlternateFrequencyReadoutHarness.svelte';

const components: ReturnType<typeof mount>[] = [];
const roots: (() => void)[] = [];

beforeEach(() => {
  selectedFrequency.current = AlternateFrequencyReadoutHarness as FrequencyRenderer;
  clearRetainedInteractions();
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  roots.forEach((dispose) => dispose());
  components.length = 0;
  roots.length = 0;
  selectedFrequency.current = undefined;
  clearRetainedInteractions();
  document.body.innerHTML = '';
});

function mountSeat(initialContext: object | null) {
  const context = writable<object | null>(initialContext);
  const live = fromStore(context);
  const onFreqChange = vi.fn();
  let binding!: ReturnType<typeof createFrequencyInstrumentBinding>;
  roots.push(effect_root(() => {
    binding = createFrequencyInstrumentBinding({
      confirmedHz: 14_250_000,
      disabled: false,
      get context() { return live.current; },
      receiver: 'main',
      minFreq: 0,
      maxFreq: 999_000_000,
      onFreqChange,
    });
  }));
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(FrequencyRendererSeat, {
    target,
    props: {
      binding,
      presentation: 'interactive',
      compact: true,
      active: true,
      receiver: 'main',
      vfoFreqHook: false,
    },
  }));
  flushSync();
  const digit = binding.model.digits.find((candidate) => candidate.multiplier === 1)!;
  return { binding, context, digit, onFreqChange, target };
}

function expectRejected(
  interaction: FrequencyInteraction,
  digit: ReturnType<typeof mountSeat>['digit'],
  onFreqChange: ReturnType<typeof vi.fn>,
): void {
  const click = new MouseEvent('click', { cancelable: true });
  const wheel = new WheelEvent('wheel', { deltaY: -1, cancelable: true });
  const key = new KeyboardEvent('keydown', { key: 'ArrowUp', cancelable: true });
  interaction.handleDigitClick(digit, click);
  interaction.handleDigitEnter(digit);
  interaction.handleWheel(digit, wheel);
  interaction.handleKeyDown(key);
  expect(click.cancelBubble).toBe(false);
  expect(wheel.defaultPrevented).toBe(false);
  expect(key.defaultPrevented).toBe(false);
  expect(interaction).toMatchObject({
    inert: true,
    selectedDigitIndex: null,
    hoveredDigitIndex: null,
  });
  expect(onFreqChange).not.toHaveBeenCalled();
}

describe('FrequencyRendererSeat', () => {
  it('keeps an initially null external renderer inert until valid authority arrives', async () => {
    const { context, digit, onFreqChange, target } = mountSeat(null);
    const renderer = target.querySelector('[data-alternate-frequency-readout]');
    const unavailable = retainedInteractions()[0];

    expect(renderer).not.toBeNull();
    expect(retainedInteractions()).toHaveLength(1);
    expectRejected(unavailable, digit, onFreqChange);
    await Promise.resolve();
    flushSync();
    expect(retainedInteractions()).toHaveLength(1);

    context.set({});
    flushSync();
    const recovered = retainedInteractions().at(-1)!;
    expect(target.querySelector('[data-alternate-frequency-readout]')).toBe(renderer);
    expect(recovered).not.toBe(unavailable);
    recovered.handleDigitClick(digit, new MouseEvent('click'));
    recovered.handleKeyDown(new KeyboardEvent('keydown', { key: 'ArrowUp', cancelable: true }));
    expect(onFreqChange).toHaveBeenCalledExactlyOnceWith(14_250_001);

    unmount(components.pop()!);
    await Promise.resolve();
    expect(recovered.inert).toBe(true);
  });

  it('settles selected and hovered state across known, null, and valid contexts', async () => {
    const { context, digit, onFreqChange, target } = mountSeat({});
    const renderer = target.querySelector('[data-alternate-frequency-readout]');
    const attached = retainedInteractions()[0];
    attached.handleDigitClick(digit, new MouseEvent('click'));
    attached.handleDigitEnter(digit);
    expect(attached.selectedDigitIndex).toBe(digit.digitIndex);
    expect(attached.hoveredDigitIndex).toBe(digit.digitIndex);

    context.set(null);
    expect(() => flushSync()).not.toThrow();
    const unavailable = retainedInteractions().at(-1)!;
    expect(target.querySelector('[data-alternate-frequency-readout]')).toBe(renderer);
    expect(unavailable).not.toBe(attached);
    expectRejected(attached, digit, onFreqChange);
    expectRejected(unavailable, digit, onFreqChange);
    await Promise.resolve();
    flushSync();
    expect(retainedInteractions().at(-1)).toBe(unavailable);

    context.set({});
    flushSync();
    const recovered = retainedInteractions().at(-1)!;
    expect(target.querySelector('[data-alternate-frequency-readout]')).toBe(renderer);
    expect(recovered).not.toBe(unavailable);
    recovered.handleDigitClick(digit, new MouseEvent('click'));
    recovered.handleKeyDown(new KeyboardEvent('keydown', { key: 'ArrowUp', cancelable: true }));
    expect(onFreqChange).toHaveBeenCalledExactlyOnceWith(14_250_001);

    unmount(components.pop()!);
    await Promise.resolve();
    expect(attached.inert).toBe(true);
    expect(unavailable.inert).toBe(true);
    expect(recovered.inert).toBe(true);
  });
});
