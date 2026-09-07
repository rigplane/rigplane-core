import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ControlInstrumentRenderHarness from './ControlInstrumentRenderHarness.svelte';
import FiniteControlRendererFixture from './support/FiniteControlRendererFixture.svelte';
import type { InstrumentField } from '../control-instrument-behavior';
import {
  createAbsoluteChoiceRendererSeat, createFiniteRendererContext,
} from '../control-instrument-renderer.svelte';

const usable = <T>(value: T): InstrumentField<T> => ({
  availability: { structural: true, operational: true }, reading: { status: 'known', value },
});
const unknown = <T>(): InstrumentField<T> => ({
  availability: { structural: true, operational: true }, reading: { status: 'unknown' },
});

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

function render(toggle: InstrumentField<boolean>, choice: InstrumentField<string>, choices = ['A', 'B'] as const) {
  const onToggle = vi.fn();
  const onChoice = vi.fn();
  const component = mount(ControlInstrumentRenderHarness, { target, props: { toggle, choice, choices, onToggle, onChoice } });
  flushSync();
  return {
    checkbox: () => target.querySelector<HTMLButtonElement>('[data-testid="alternate-toggle"]')!,
    choiceGroup: () => target.querySelector<HTMLElement>('[data-testid="alternate-choices"]')!,
    radio: (value: string) => target.querySelector<HTMLButtonElement>(`[data-testid="alternate-choice-${value}"]`)!,
    onToggle, onChoice, dispose: () => unmount(component),
  };
}

describe('control-instrument renderer independence', () => {
  it('renders confirmed state through checkbox and CSS-grid radio semantics', () => {
    const r = render(usable(true), usable('B'));
    expect(r.checkbox().getAttribute('aria-checked')).toBe('true');
    expect(r.choiceGroup().getAttribute('role')).toBe('radiogroup');
    expect(r.choiceGroup().getAttribute('aria-label')).toBe('Alternative choices');
    expect(getComputedStyle(r.choiceGroup()).display).toBe('grid');
    expect(r.radio('A').getAttribute('aria-checked')).toBe('false');
    expect(r.radio('B').getAttribute('aria-checked')).toBe('true');
    expect(r.radio('A').closest('[role="radiogroup"]')).toBe(r.choiceGroup());
    expect(r.radio('B').closest('[role="radiogroup"]')).toBe(r.choiceGroup());
    r.dispose();
  });

  it('renders per-option reasons as unique non-live descriptions without changing admission', () => {
    const invoke = vi.fn();
    const context = createFiniteRendererContext();
    const seat = createAbsoluteChoiceRendererSeat(() => ({
      context,
      reading: { status: 'unknown' as const },
      available: true,
      label: 'Active receiver',
      options: [
        { value: 'MAIN', label: 'MAIN', disabled: true, disabledReason: 'MAIN unavailable' },
        { value: 'SUB', label: 'SUB', disabledReason: 'SUB remains available' },
      ],
      invoke,
    }));
    const firstLease = seat.attachRenderer();
    const secondLease = seat.attachRenderer();
    const first = mount(FiniteControlRendererFixture, { target, props: { lease: firstLease } });
    const secondTarget = document.createElement('div');
    target.appendChild(secondTarget);
    const second = mount(FiniteControlRendererFixture, {
      target: secondTarget, props: { lease: secondLease },
    });
    flushSync();

    const main = target.querySelector<HTMLButtonElement>('[data-testid="external-Active receiver-MAIN"]')!;
    const sub = target.querySelector<HTMLButtonElement>('[data-testid="external-Active receiver-SUB"]')!;
    const reasonId = main.getAttribute('aria-describedby');
    const describedIds = [...target.querySelectorAll<HTMLButtonElement>('[aria-describedby]')]
      .map(button => button.getAttribute('aria-describedby'));
    expect(main.disabled).toBe(true);
    expect(main.title).toBe('MAIN unavailable');
    expect(reasonId).not.toBeNull();
    expect(target.querySelector(`#${reasonId}`)?.textContent).toBe('MAIN unavailable');
    expect(new Set(describedIds).size).toBe(describedIds.length);
    expect(target.querySelector('[role="status"], [aria-live]')).toBeNull();

    main.click();
    sub.click();
    expect(invoke).toHaveBeenCalledExactlyOnceWith('SUB');

    unmount(first);
    unmount(second);
    firstLease.dispose();
    secondLease.dispose();
  });

  it('renders unknown checkbox as mixed and leaves an out-of-list observed choice unselected', () => {
    const r = render(unknown<boolean>(), usable('outside'));
    expect(r.checkbox().getAttribute('aria-checked')).toBe('mixed');
    expect(r.radio('A').getAttribute('aria-checked')).toBe('false');
    expect(r.radio('B').getAttribute('aria-checked')).toBe('false');
    r.dispose();
  });

  it('sends toggle and explicit radio values through the same bindings', () => {
    const r = render(usable(false), usable('A'));
    r.checkbox().click();
    r.radio('B').click();
    flushSync();
    expect(r.onToggle).toHaveBeenCalledExactlyOnceWith(true);
    expect(r.onChoice).toHaveBeenCalledExactlyOnceWith('B');
    r.dispose();
  });
});
