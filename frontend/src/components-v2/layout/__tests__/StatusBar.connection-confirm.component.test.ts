/**
 * In-page confirmation for the connection toggle (owner ruling
 * 2026-09-23): the DISCONNECT/CONNECT status-bar button must ask inside
 * the page — the Tauri WebView in RigPlane Pro never shows native
 * `window.confirm` dialogs. Pins, per the ruling:
 *   - clicking the toggle opens the in-page dialog and does NOT call
 *     `runtime.system.disconnect`/`connect` until confirm;
 *   - confirm dispatches exactly once;
 *   - cancel never dispatches;
 *   - the native `window.confirm`/`window.alert` are never called.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';

const conn = vi.hoisted(() => ({
  status: 'connected' as string,
}));

const sys = vi.hoisted(() => ({
  disconnect: vi.fn(),
  connect: vi.fn(),
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getRadioLinkState: vi.fn(() => 'connected'),
  getConnectionStatus: vi.fn(() => conn.status),
  isAudioConnected: vi.fn(() => false),
  getWsConnected: vi.fn(() => true),
  getRadioPowerOn: vi.fn(() => null),
  getRigConnected: vi.fn(() => true),
  getRadioReady: vi.fn(() => true),
  getRadioHealth: vi.fn(() => null),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasAnyScope: vi.fn(() => false),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false),
  hasCapability: vi.fn(() => false),
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
      disconnect: sys.disconnect,
      connect: sys.connect,
      powerOn: vi.fn(async () => {}),
      powerOff: vi.fn(async () => {}),
      identifyFrequency: vi.fn(async () => null),
    },
  },
}));

import StatusBar from '../StatusBar.svelte';

describe('StatusBar connection toggle (in-page confirm)', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;
  let confirmSpy: ReturnType<typeof vi.fn>;
  let alertSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    conn.status = 'connected';
    sys.disconnect.mockReset();
    sys.connect.mockReset();
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

  /** The connection toggle, found by its exact rendered action label
   * ('Disconnect'/'Connect') — the status bar has several control-btns. */
  function connectionButton(host: HTMLElement, label: string): HTMLButtonElement {
    const btn = [...host.querySelectorAll<HTMLButtonElement>('button.control-btn')].find(
      (b) => b.querySelector('.btn-label')?.textContent?.trim() === label,
    );
    expect(btn, `expected the connection toggle labelled ${label}`).not.toBeNull();
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

  it('connected: DISCONNECT opens the in-page dialog, disconnect runs only after confirm', async () => {
    conn.status = 'connected';
    const host = render();
    const btn = connectionButton(host, 'Disconnect');
    btn.focus();
    btn.click();
    await tick();

    const dlg = dialog(host);
    expect(dlg, 'expected the in-page confirm dialog to open').not.toBeNull();
    expect(dlg!.textContent).toContain('Disconnect?');
    expect(sys.disconnect).not.toHaveBeenCalled();

    dialogButton(host, 'confirm-dialog-confirm').click();
    await vi.waitFor(() => expect(sys.disconnect).toHaveBeenCalledTimes(1));
    expect(sys.connect).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();

    await tick();
    expect(dialog(host)).toBeNull();
    // Focus returns to the button that opened the dialog.
    expect(document.activeElement).toBe(btn);
  });

  it('connected: cancel never disconnects', async () => {
    conn.status = 'connected';
    const host = render();
    connectionButton(host, 'Disconnect').click();
    await tick();
    expect(dialog(host)).not.toBeNull();

    dialogButton(host, 'confirm-dialog-cancel').click();
    await tick();
    expect(dialog(host)).toBeNull();
    expect(sys.disconnect).not.toHaveBeenCalled();
    expect(sys.connect).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('disconnected: CONNECT opens the in-page dialog, connect runs only after confirm', async () => {
    conn.status = 'disconnected';
    const host = render();
    connectionButton(host, 'Connect').click();
    await tick();

    expect(dialog(host)?.textContent).toContain('Connect?');
    expect(sys.connect).not.toHaveBeenCalled();

    dialogButton(host, 'confirm-dialog-confirm').click();
    await vi.waitFor(() => expect(sys.connect).toHaveBeenCalledTimes(1));
    expect(sys.disconnect).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
    await tick();
    expect(dialog(host)).toBeNull();
  });
});
