import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ControlInstrumentRenderHarness from './ControlInstrumentRenderHarness.svelte';
import type { InstrumentField } from '../control-instrument-behavior';

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
    radio: (value: string) => target.querySelector<HTMLButtonElement>(`[data-testid="alternate-choice-${value}"]`)!,
    onToggle, onChoice, dispose: () => unmount(component),
  };
}

describe('control-instrument renderer independence', () => {
  it('renders confirmed state through checkbox and grid radio semantics', () => {
    const r = render(usable(true), usable('B'));
    expect(r.checkbox().getAttribute('aria-checked')).toBe('true');
    expect(r.radio('A').getAttribute('aria-checked')).toBe('false');
    expect(r.radio('B').getAttribute('aria-checked')).toBe('true');
    r.dispose();
  });

  it('omits unknown checkbox state and leaves an out-of-list observed choice unselected', () => {
    const r = render(unknown<boolean>(), usable('outside'));
    expect(r.checkbox().hasAttribute('aria-checked')).toBe(false);
    expect(r.radio('A').getAttribute('aria-checked')).toBe('false');
    expect(r.radio('B').getAttribute('aria-checked')).toBe('false');
    r.dispose();
  });

  it('sends toggle and explicit grid values through the same bindings', () => {
    const r = render(usable(false), usable('A'));
    r.checkbox().click();
    r.radio('B').click();
    flushSync();
    expect(r.onToggle).toHaveBeenCalledExactlyOnceWith(true);
    expect(r.onChoice).toHaveBeenCalledExactlyOnceWith('B');
    r.dispose();
  });
});
