/**
 * MOR-1673 — StatusBar power-toggle capability gating and truthful
 * unknown-state rendering. Mounts the real StatusBar.svelte with
 * controllable `getRadioPowerOn` / `hasCapability` mocks and pins:
 *   1. a radio without `power_control` keeps the button rendered but
 *      disabled (layout stability, owner decision 2026-09-15): known-state
 *      label/styling kept, truthful unsupported tooltip, and no confirm or
 *      dispatch even when the disabled attribute is bypassed;
 *   2. capability + unknown state: disabled, neutral (`power-unknown`,
 *      never `is-on`), truthful label/tooltip, and no confirm or
 *      dispatch even when the disabled attribute is bypassed (defence
 *      in depth on the handler itself);
 *   3./4. known ON / OFF keep today's confirm → powerOff / powerOn flow.
 * The unsupported + unknown combination follows the unsupported tooltip
 * with the unknown label/neutrality.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

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

describe('StatusBar power toggle (MOR-1673)', () => {
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

  it('unsupported radio: button rendered but disabled, known-state look kept, truthful tooltip, no dispatch', async () => {
    power.powerControl = false;
    // Deliberately ON: the known-state OFF label and is-on styling must
    // stay exactly as on main — only the capability differs.
    power.radioPowerOn = true;
    const host = render();
    const btn = powerButton(host);

    expect(btn.disabled).toBe(true);
    expect(btn.classList.contains('is-on')).toBe(true);
    expect(btn.classList.contains('power-unknown')).toBe(false);
    expect(btn.textContent).toContain('OFF');
    expect(btn.title).toBe('This radio does not support power control');

    // Defence in depth: strip the disabled attribute and click anyway —
    // the handler itself must refuse (no confirm, no dispatch).
    btn.removeAttribute('disabled');
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(confirmSpy).not.toHaveBeenCalled());
    expect(sys.powerOn).not.toHaveBeenCalled();
    expect(sys.powerOff).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('unsupported radio + unknown state: unsupported tooltip wins, unknown label and neutrality kept', async () => {
    power.powerControl = false;
    power.radioPowerOn = null;
    const host = render();
    const btn = powerButton(host);

    expect(btn.disabled).toBe(true);
    expect(btn.classList.contains('power-unknown')).toBe(true);
    expect(btn.classList.contains('is-on')).toBe(false);
    expect(btn.textContent).toContain('UNKNOWN');
    expect(btn.title).toBe('This radio does not support power control');

    btn.removeAttribute('disabled');
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(confirmSpy).not.toHaveBeenCalled());
    expect(sys.powerOn).not.toHaveBeenCalled();
    expect(sys.powerOff).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('capability + unknown state: disabled, neutral, truthful text, and no dispatch even with disabled bypassed', async () => {
    power.powerControl = true;
    power.radioPowerOn = null;
    const host = render();
    const btn = powerButton(host);

    expect(btn.disabled).toBe(true);
    expect(btn.classList.contains('power-unknown')).toBe(true);
    expect(btn.classList.contains('is-on')).toBe(false);
    expect(btn.textContent).toContain('UNKNOWN');
    expect(btn.title).toBe('Radio power state unknown');
    expect(btn.title).not.toMatch(/click/i);

    // Defence in depth: strip the disabled attribute and click anyway —
    // the handler itself must refuse (no confirm, no dispatch).
    btn.removeAttribute('disabled');
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(confirmSpy).not.toHaveBeenCalled());
    expect(sys.powerOn).not.toHaveBeenCalled();
    expect(sys.powerOff).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('known ON: click → confirm(turn off) → powerOff, not powerOn', async () => {
    power.powerControl = true;
    power.radioPowerOn = true;
    const host = render();
    powerButton(host).click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy).toHaveBeenCalledWith('Turn OFF the radio?');
    await vi.waitFor(() => expect(sys.powerOff).toHaveBeenCalledTimes(1));
    expect(sys.powerOn).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('known OFF: click → confirm(turn on) → powerOn, not powerOff', async () => {
    power.powerControl = true;
    power.radioPowerOn = false;
    const host = render();
    powerButton(host).click();

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy).toHaveBeenCalledWith('Turn ON the radio?');
    await vi.waitFor(() => expect(sys.powerOn).toHaveBeenCalledTimes(1));
    expect(sys.powerOff).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });
});
