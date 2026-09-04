import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const h = vi.hoisted(() => ({
  getManagedAppTxController: vi.fn(),
  subscribe: vi.fn(() => () => {}),
  setTot: vi.fn(async () => {}),
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getRadioLinkState: vi.fn(() => 'connected'),
  getConnectionStatus: vi.fn(() => 'connected'),
  isAudioConnected: vi.fn(() => false),
  getWsConnected: vi.fn(() => true),
  getRadioPowerOn: vi.fn(() => true),
  getRigConnected: vi.fn(() => true),
  getRadioReady: vi.fn(() => true),
  getRadioHealth: vi.fn(() => null),
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasAnyScope: vi.fn(() => false), hasAudio: vi.fn(() => false), hasSpectrum: vi.fn(() => false),
}));
vi.mock('$lib/stores/layout.svelte', () => ({ getLayoutMode: vi.fn(() => 'standard'), setLayoutMode: vi.fn() }));
vi.mock('$lib/runtime/adapters/panel-adapters', () => ({ getActiveFrequencyHz: vi.fn(() => null) }));
vi.mock('$lib/runtime', () => ({
  runtime: {
    defaultScopeStatus: {
      source: null, available: false, resourceSelected: false, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    },
    system: {
      disconnect: vi.fn(), connect: vi.fn(), powerOn: vi.fn(async () => {}),
      powerOff: vi.fn(async () => {}), identifyFrequency: vi.fn(async () => null),
    },
  },
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: h.getManagedAppTxController,
}));

import StatusBar from '../StatusBar.svelte';

const appRootFacade = Object.freeze({
  snapshot: () => Object.freeze({
    fresh: true, configuredSeconds: 180, remainingMs: null,
    radioTx: 'off', txRisk: 'receiving', releaseRequired: false,
  }),
  subscribe: h.subscribe,
  pttOn: vi.fn(), pttOff: vi.fn(), transmitOn: vi.fn(), forceOff: vi.fn(), setTot: h.setTot,
});

describe('StatusBar managed TOT presentation', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;

  beforeEach(() => {
    h.getManagedAppTxController.mockReset().mockReturnValue(appRootFacade);
    h.subscribe.mockClear();
    h.setTot.mockClear();
  });

  afterEach(() => {
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
  });

  function render(showManagedTotControl = false): HTMLElement {
    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(StatusBar, { target, props: { showManagedTotControl } }) as object;
    flushSync();
    return target;
  }

  it('mounts no managed-TOT consumer by default', () => {
    const host = render();
    expect(host.querySelectorAll('[data-testid="managed-tot-control"]')).toHaveLength(0);
    expect(h.getManagedAppTxController).not.toHaveBeenCalled();
    expect(h.subscribe).not.toHaveBeenCalled();
  });

  it('mounts exactly one consumer through the existing App-root facade when enabled', () => {
    const host = render(true);
    expect(host.querySelectorAll('[data-testid="managed-tot-control"]')).toHaveLength(1);
    expect(h.getManagedAppTxController).toHaveBeenCalledTimes(1);
    expect(h.subscribe).toHaveBeenCalledTimes(1);
    expect(h.setTot).not.toHaveBeenCalled();
  });
});
