import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import FrequencyEntryDialog from '../FrequencyEntryDialog.svelte';

const content = createRawSnippet(() => ({
  render: () => '<div><input data-dialog-input><button type="button">Set</button></div>',
}));

let target: HTMLDivElement;
let trigger: HTMLButtonElement;
beforeEach(() => {
  target = document.createElement('div');
  trigger = document.createElement('button');
  document.body.append(trigger, target);
});
afterEach(() => {
  target.remove();
  trigger.remove();
});

describe('FrequencyEntryDialog', () => {
  it('autofocuses its entry and Escape cancels, closes, and restores trigger focus', async () => {
    const onclose = vi.fn();
    const props = proxy({ open: true, targetLabel: 'VFO B', returnFocus: trigger, onclose, children: content });
    trigger.focus();
    const component = mount(FrequencyEntryDialog, { target, props });
    flushSync();
    await Promise.resolve();
    const dialog = target.querySelector<HTMLElement>('[role="dialog"]')!;
    const input = target.querySelector<HTMLInputElement>('[data-dialog-input]')!;
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(dialog.textContent).toContain('VFO B');
    expect(document.activeElement).toBe(input);

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    expect(onclose).toHaveBeenCalledOnce();
    props.open = false;
    flushSync();
    await Promise.resolve();
    expect(target.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    unmount(component);
  });

  it('cancels only a direct backdrop click', () => {
    const onclose = vi.fn();
    const component = mount(FrequencyEntryDialog, {
      target, props: { open: true, targetLabel: 'VFO A', onclose, children: content },
    });
    flushSync();
    target.querySelector<HTMLElement>('[data-testid="frequency-entry-dialog-panel"]')?.click();
    expect(onclose).not.toHaveBeenCalled();
    target.querySelector<HTMLElement>('[data-testid="frequency-entry-dialog-backdrop"]')?.click();
    expect(onclose).toHaveBeenCalledOnce();
    unmount(component);
  });
});
