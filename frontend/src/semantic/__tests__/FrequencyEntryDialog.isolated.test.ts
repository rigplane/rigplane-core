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

  it('announces command lifecycle feedback', () => {
    const props = proxy({
      open: true, targetLabel: 'VFO B', status: 'Waiting for readback', children: content,
    });
    const component = mount(FrequencyEntryDialog, { target, props });
    flushSync();
    expect(target.querySelector('[role="status"]')?.textContent).toBe('Waiting for readback');
    props.status = 'Frequency confirmed.';
    flushSync();
    expect(target.querySelector('[role="status"]')?.textContent).toBe('Frequency confirmed.');
    unmount(component);
  });

  it('takes its own heading and close label (MOR-4220: the Memory dialog)', () => {
    const component = mount(FrequencyEntryDialog, {
      target, props: { open: true, title: 'Memory', closeLabel: 'Close', children: content },
    });
    flushSync();
    const dialog = target.querySelector<HTMLElement>('[role="dialog"]')!;
    const heading = dialog.querySelector('h2')!;
    expect(heading.textContent).toBe('Memory');
    expect(dialog.getAttribute('aria-labelledby')).toBe(heading.id);
    expect(dialog.querySelector('button.close')?.getAttribute('aria-label')).toBe('Close');
    unmount(component);
  });

  it('closes on Escape and pulls Tab back inside after focus fell out of it (MOR-4220)', async () => {
    const onclose = vi.fn();
    const component = mount(FrequencyEntryDialog, {
      target, props: { open: true, targetLabel: 'VFO A', onclose, children: content },
    });
    flushSync();
    await Promise.resolve();
    (document.activeElement as HTMLElement).blur();
    expect(document.activeElement).toBe(document.body);

    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(target.querySelector('[data-dialog-input]'));
    expect(onclose).not.toHaveBeenCalled();

    // The closing Escape never reaches a global shortcut listener.
    const shortcut = vi.fn();
    window.addEventListener('keydown', shortcut);
    (document.activeElement as HTMLElement).blur();
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    expect(onclose).toHaveBeenCalledOnce();
    expect(shortcut).not.toHaveBeenCalled();
    // A key the dialog itself handled is not handled twice.
    target.querySelector<HTMLElement>('[data-dialog-input]')!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    );
    expect(onclose).toHaveBeenCalledTimes(2);
    expect(shortcut).not.toHaveBeenCalled();
    window.removeEventListener('keydown', shortcut);
    unmount(component);
  });

  it('keeps the frequency-entry heading and close label by default', () => {
    const component = mount(FrequencyEntryDialog, {
      target, props: { open: true, targetLabel: 'VFO A', children: content },
    });
    flushSync();
    expect(target.querySelector('h2')?.textContent).toBe('Set frequency — VFO A');
    expect(target.querySelector('button.close')?.getAttribute('aria-label')).toBe('Close frequency entry');
    unmount(component);
  });
});
