/**
 * ConfirmDialog — the in-page replacement for the status bar's native
 * `window.confirm`/`window.alert` calls (owner ruling 2026-09-23: the
 * Pro Tauri WebView never shows native dialogs). Pins the component
 * contract: closed/open rendering and ARIA wiring, confirm/cancel via
 * button, Escape, and backdrop, the error state (error text shown, only
 * Close offered), and the focus contract (Cancel on open, back to the
 * opener on close/unmount).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';

import ConfirmDialog from '../ConfirmDialog.svelte';

type SetupProps = {
  open?: boolean;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  error?: string | null;
};

function setup(props: SetupProps = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const onConfirm = vi.fn(async () => {});
  const onCancel = vi.fn();
  const component = mount(ConfirmDialog, {
    target,
    props: {
      open: props.open ?? true,
      message: props.message ?? 'Disconnect?',
      confirmLabel: props.confirmLabel ?? 'OK',
      cancelLabel: props.cancelLabel ?? 'Cancel',
      onConfirm,
      onCancel,
      error: props.error ?? null,
    },
  });
  flushSync();
  return { target, onConfirm, onCancel, component };
}

function q(target: HTMLElement, selector: string): HTMLElement | null {
  return target.querySelector<HTMLElement>(selector);
}

describe('ConfirmDialog', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('renders nothing when open=false', () => {
    const { target } = setup({ open: false });
    expect(q(target, '[role="alertdialog"]')).toBeNull();
  });

  it('renders an alertdialog with the message and both actions wired by ids', () => {
    const { target } = setup({ message: 'Turn OFF the radio?' });
    const dlg = q(target, '[role="alertdialog"]');
    expect(dlg).not.toBeNull();
    expect(dlg!.getAttribute('aria-modal')).toBe('true');
    expect(dlg!.getAttribute('aria-labelledby')).toBe('confirm-dialog-title');
    expect(dlg!.getAttribute('aria-describedby')).toBe('confirm-dialog-message');
    expect(document.getElementById('confirm-dialog-title')).not.toBeNull();
    expect(q(target, '[data-testid="confirm-dialog-message"]')?.textContent).toBe(
      'Turn OFF the radio?',
    );
    expect(q(target, '[data-testid="confirm-dialog-confirm"]')?.textContent?.trim()).toBe('OK');
    expect(q(target, '[data-testid="confirm-dialog-cancel"]')?.textContent?.trim()).toBe('Cancel');
  });

  it('confirm button calls onConfirm once, never onCancel', () => {
    const { target, onConfirm, onCancel } = setup();
    q(target, '[data-testid="confirm-dialog-confirm"]')!.click();
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('cancel button calls onCancel once, never onConfirm', () => {
    const { target, onConfirm, onCancel } = setup();
    q(target, '[data-testid="confirm-dialog-cancel"]')!.click();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('Escape key cancels', () => {
    const { target, onConfirm, onCancel } = setup();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('backdrop click cancels; a click inside the dialog does not', () => {
    const { target, onCancel } = setup();
    q(target, '[data-testid="confirm-dialog-backdrop"]')!.click();
    expect(onCancel).toHaveBeenCalledTimes(1);

    // A click on the dialog body bubbles to the backdrop but must not
    // count as a backdrop click.
    q(target, '[data-testid="confirm-dialog-message"]')!.click();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('focus lands on Cancel when opened and returns to the opener on close/unmount', async () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    expect(document.activeElement).toBe(opener);

    const { target, component } = setup();
    await tick();
    expect(document.activeElement).toBe(q(target, '[data-testid="confirm-dialog-cancel"]'));

    unmount(component);
    await tick();
    expect(document.activeElement).toBe(opener);
  });

  it('error state: shows the error, offers only Close (focused), which cancels', async () => {
    const { target, onConfirm, onCancel } = setup({ error: 'Failed to turn off radio: boom' });
    await tick();

    expect(q(target, '[data-testid="confirm-dialog-error"]')?.getAttribute('role')).toBe('alert');
    expect(q(target, '[data-testid="confirm-dialog-error"]')?.textContent).toBe(
      'Failed to turn off radio: boom',
    );
    // Only Close is offered — the confirm/cancel pair is gone.
    expect(q(target, '[data-testid="confirm-dialog-confirm"]')).toBeNull();
    expect(q(target, '[data-testid="confirm-dialog-cancel"]')).toBeNull();
    expect(document.activeElement).toBe(q(target, '[data-testid="confirm-dialog-close"]'));

    q(target, '[data-testid="confirm-dialog-close"]')!.click();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
