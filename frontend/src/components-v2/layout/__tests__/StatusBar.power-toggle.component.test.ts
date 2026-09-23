/**
 * MOR-1673 + in-page confirmation (owner ruling 2026-09-23) — StatusBar
 * power-toggle capability gating, truthful unknown-state rendering, and
 * the ConfirmDialog flow that replaced `window.confirm`/`window.alert`
 * (observed in one RigPlane Pro build: the native confirm did not
 * appear and DISCONNECT did nothing; the page no longer relies on
 * native dialogs).
 * Mounts the real StatusBar.svelte with controllable `getRadioPowerOn` /
 * `hasCapability` mocks and pins:
 *   1. a radio without `power_control` renders no power control at all —
 *      no button and no placeholder label;
 *   2. capability + unknown state: disabled, neutral (`power-unknown`,
 *      never `is-on`), the plain control word POWER (never UNKNOWN),
 *      truthful tooltip, and no dialog or dispatch even when the
 *      disabled attribute is bypassed (defence in depth on the handler
 *      itself);
 *   3./4. known ON / OFF: click opens the in-page dialog and dispatches
 *      powerOff / powerOn only after explicit confirm — never before,
 *      never via the native dialog;
 *   5. a failing power action reports the error inside the open dialog
 *      (replacing the old `alert`), and Close dismisses it;
 *   6. re-entrancy: OK is disabled while the action is pending (a
 *      second click — even with disabled bypassed — never re-runs it),
 *      and a result from a cancelled request can never close or
 *      annotate a newer confirm.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';

const power = vi.hoisted(() => ({
  radioPowerOn: null as boolean | null,
  powerControl: false,
}));

const sys = vi.hoisted(() => ({
  powerOn: vi.fn(async () => {}),
  powerOff: vi.fn(async () => {}),
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getRadioLinkState: vi.fn(() => 'connected'),
  getConnectionStatus: vi.fn(() => 'connected'),
  isAudioConnected: vi.fn(() => false),
  getWsConnected: vi.fn(() => true),
  getRadioPowerOn: vi.fn(() => power.radioPowerOn),
  getRigConnected: vi.fn(() => true),
  getRadioReady: vi.fn(() => true),
  getRadioHealth: vi.fn(() => null),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasAnyScope: vi.fn(() => false),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false),
  hasCapability: vi.fn((name: string) => name === 'power_control' && power.powerControl),
}));

vi.mock('$lib/stores/layout.svelte', () => ({
  getLayoutMode: vi.fn(() => 'standard'),
  setLayoutMode: vi.fn(),
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  getActiveFrequencyHz: vi.fn(() => null),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    defaultScopeStatus: {
      source: null,
      available: false,
      resourceSelected: false,
      demand: 0,
      lifecycle: 'inactive',
      transport: 'disconnected',
      frameSeen: false,
    },
    system: {
      disconnect: vi.fn(),
      connect: vi.fn(),
      powerOn: sys.powerOn,
      powerOff: sys.powerOff,
      identifyFrequency: vi.fn(async () => null),
    },
  },
}));

import StatusBar from '../StatusBar.svelte';

describe('StatusBar power toggle (MOR-1673, in-page confirm)', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;
  let confirmSpy: ReturnType<typeof vi.fn>;
  let alertSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    power.radioPowerOn = null;
    power.powerControl = false;
    sys.powerOn.mockClear();
    sys.powerOff.mockClear();
    confirmSpy = vi.fn(() => true);
    alertSpy = vi.fn();
    vi.stubGlobal('confirm', confirmSpy);
    vi.stubGlobal('alert', alertSpy);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
  });

  function render(): HTMLElement {
    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(StatusBar, { target }) as object;
    flushSync();
    return target;
  }

  function powerButton(host: HTMLElement): HTMLButtonElement {
    const btn = host.querySelector<HTMLButtonElement>('.power-toggle-btn');
    expect(btn, 'expected the power toggle button to render').not.toBeNull();
    return btn!;
  }

  function dialog(host: HTMLElement): Element | null {
    return host.querySelector('[role="alertdialog"]');
  }

  function dialogButton(host: HTMLElement, testid: string): HTMLButtonElement {
    const btn = host.querySelector<HTMLButtonElement>(`[data-testid="${testid}"]`);
    expect(btn, `expected the dialog button ${testid} to render`).not.toBeNull();
    return btn!;
  }

  it('unsupported radio (no power_control): renders no power control at all', () => {
    power.powerControl = false;
    power.radioPowerOn = null;
    const host = render();

    expect(host.querySelector('.power-toggle-btn')).toBeNull();
    expect(host.querySelector('.status-controls')?.textContent ?? '').not.toContain('UNKNOWN');
  });

  it('capability + unknown state: disabled, neutral, plain POWER label, and no dialog or dispatch even with disabled bypassed', async () => {
    power.powerControl = true;
    power.radioPowerOn = null;
    const host = render();
    const btn = powerButton(host);

    expect(btn.disabled).toBe(true);
    expect(btn.classList.contains('power-unknown')).toBe(true);
    expect(btn.classList.contains('is-on')).toBe(false);
    // Owner rule 2026-09-21: an unread value is an unlit label in place —
    // the plain control word, never a placeholder. Literal assertions on
    // the rendered text, not on t(key).
    expect(btn.textContent).toContain('POWER');
    expect(btn.textContent ?? '').not.toContain('UNKNOWN');
    expect(btn.title).toBe('Radio power state unknown');
    expect(btn.title).not.toMatch(/click/i);

    // Defence in depth: strip the disabled attribute and click anyway —
    // the handler itself must refuse (no dialog, no dispatch).
    btn.removeAttribute('disabled');
    btn.click();
    await tick();
    expect(dialog(host)).toBeNull();
    await vi.waitFor(() => expect(confirmSpy).not.toHaveBeenCalled());
    expect(sys.powerOn).not.toHaveBeenCalled();
    expect(sys.powerOff).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('known ON: click opens the dialog, powerOff runs only after confirm', async () => {
    power.powerControl = true;
    power.radioPowerOn = true;
    const host = render();
    const btn = powerButton(host);
    btn.focus();
    btn.click();
    await tick();

    const dlg = dialog(host);
    expect(dlg, 'expected the in-page confirm dialog to open').not.toBeNull();
    expect(dlg!.textContent).toContain('Turn OFF the radio?');
    expect(sys.powerOff).not.toHaveBeenCalled();

    // Focus contract: the dialog takes focus with Cancel as the safe default.
    expect(document.activeElement).toBe(dialogButton(host, 'confirm-dialog-cancel'));

    dialogButton(host, 'confirm-dialog-confirm').click();
    await vi.waitFor(() => expect(sys.powerOff).toHaveBeenCalledTimes(1));
    expect(sys.powerOn).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();

    await tick();
    expect(dialog(host)).toBeNull();
    // Focus returns to the control that opened the dialog.
    expect(document.activeElement).toBe(btn);
  });

  it('known OFF: click opens the dialog, powerOn runs only after confirm', async () => {
    power.powerControl = true;
    power.radioPowerOn = false;
    const host = render();
    powerButton(host).click();
    await tick();

    expect(dialog(host)?.textContent).toContain('Turn ON the radio?');
    expect(sys.powerOn).not.toHaveBeenCalled();

    dialogButton(host, 'confirm-dialog-confirm').click();
    await vi.waitFor(() => expect(sys.powerOn).toHaveBeenCalledTimes(1));
    expect(sys.powerOff).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    await tick();
    expect(dialog(host)).toBeNull();
  });

  it('cancel: no dispatch ever, dialog closes', async () => {
    power.powerControl = true;
    power.radioPowerOn = true;
    const host = render();
    powerButton(host).click();
    await tick();
    expect(dialog(host)).not.toBeNull();

    dialogButton(host, 'confirm-dialog-cancel').click();
    await tick();
    expect(dialog(host)).toBeNull();
    expect(sys.powerOff).not.toHaveBeenCalled();
    expect(sys.powerOn).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('failing powerOff: error shows inside the open dialog (no alert), Close dismisses it', async () => {
    power.powerControl = true;
    power.radioPowerOn = true;
    sys.powerOff.mockRejectedValueOnce(new Error('boom'));
    const host = render();
    powerButton(host).click();
    await tick();
    dialogButton(host, 'confirm-dialog-confirm').click();

    await vi.waitFor(() =>
      expect(host.querySelector('[data-testid="confirm-dialog-error"]')?.textContent).toBe(
        'Failed to turn off radio: boom',
      ),
    );
    // The dialog stays open with the error in place of the old native
    // alert dialog.
    expect(dialog(host)).not.toBeNull();
    expect(alertSpy).not.toHaveBeenCalled();
    // In the error state only Close is offered — no confirm/cancel pair.
    expect(host.querySelector('[data-testid="confirm-dialog-confirm"]')).toBeNull();
    expect(host.querySelector('[data-testid="confirm-dialog-cancel"]')).toBeNull();

    dialogButton(host, 'confirm-dialog-close').click();
    await tick();
    expect(dialog(host)).toBeNull();
    expect(sys.powerOff).toHaveBeenCalledTimes(1);
  });

  it('re-entrancy: OK clicked twice while powerOn is pending runs the action exactly once', async () => {
    power.powerControl = true;
    power.radioPowerOn = false;
    let release!: () => void;
    sys.powerOn.mockImplementationOnce(
      () => new Promise<void>((resolve) => { release = resolve; }),
    );
    const host = render();
    powerButton(host).click();
    await tick();

    const ok = dialogButton(host, 'confirm-dialog-confirm');
    ok.click();
    await tick();
    expect(sys.powerOn).toHaveBeenCalledTimes(1);
    // While the action is pending, OK is disabled and visibly busy
    // (SendReportDialog's pattern: dimmed + working label).
    expect(ok.disabled).toBe(true);
    expect(ok.textContent).toContain('Working');

    // A second click — even with disabled bypassed — must not re-run.
    ok.removeAttribute('disabled');
    ok.click();
    await tick();
    expect(sys.powerOn).toHaveBeenCalledTimes(1);
    expect(dialog(host)).not.toBeNull();

    release();
    await vi.waitFor(() => expect(dialog(host)).toBeNull());
    expect(sys.powerOn).toHaveBeenCalledTimes(1);
  });

  it('cancel while pending, then a new confirm: the old result is discarded', async () => {
    power.powerControl = true;
    power.radioPowerOn = false;
    let release!: () => void;
    sys.powerOn.mockImplementationOnce(
      () => new Promise<void>((resolve) => { release = resolve; }),
    );
    const host = render();
    powerButton(host).click();
    await tick();
    dialogButton(host, 'confirm-dialog-confirm').click();
    await tick();

    // Cancel while the action is pending: the dialog closes and nothing
    // new starts (the in-flight powerOn cannot be un-dispatched).
    dialogButton(host, 'confirm-dialog-cancel').click();
    await tick();
    expect(dialog(host)).toBeNull();
    expect(sys.powerOn).toHaveBeenCalledTimes(1);

    // A fresh confirm opens while the old action is still pending; its
    // OK is busy-disabled until the old action settles.
    powerButton(host).click();
    await tick();
    expect(dialog(host)).not.toBeNull();
    const ok = dialogButton(host, 'confirm-dialog-confirm');
    expect(ok.disabled).toBe(true);

    // The old action's late success must not close the new dialog.
    release();
    await vi.waitFor(() => expect(ok.disabled).toBe(false));
    expect(dialog(host)).not.toBeNull();

    // The new confirm still works on its own terms.
    ok.click();
    await vi.waitFor(() => expect(sys.powerOn).toHaveBeenCalledTimes(2));
    await tick();
    expect(dialog(host)).toBeNull();
  });
});
