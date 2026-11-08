// Golden pairing: test_web_server_coverage.py::test_http_power_endpoint_matches_the_golden_documents.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const s = await import('./SpectrumPanelStub.svelte');
  return { default: s.default };
});
vi.mock('../../display/FrequencyDisplay.svelte', async () => {
  const s = await import('./fixtures/FrequencyDisplayEcho.svelte');
  return { default: s.default };
});
vi.mock('../../meters/LinearSMeter.svelte', async () => {
  const s = await import('./fixtures/LinearSMeterEcho.svelte');
  return { default: s.default };
});
vi.mock('../../controls/BandSelector.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/FilterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/RxAudioPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/TxPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/DspPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/AgcPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/RfFrontEnd.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/RitXitPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/AntennaPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/ScanPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/CwPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/DockMeterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/EssentialsPanel.svelte', () => ({ default: function S() { return {}; } }));

// -- Typed doubles against the producer modules --
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    onChange: () => () => {}, getAppliedAudioConfig: () => null,
    setOperatorNotifier: vi.fn(),
  } satisfies Partial<(typeof import('$lib/audio/audio-manager'))['audioManager']>,
}));
vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({
    volume: 50, muted: false, rxEnabled: false, txEnabled: false,
    micEnabled: false, bridgeRunning: false,
    rxError: false, txError: false, txCodecFallback: false,
  })),
  getRxAudioTargetSnapshot: vi.fn(() => Object.freeze({ muted: false, rxEnabled: false })),
  subscribeRxAudioTarget: vi.fn((handler: (target: { muted: boolean; rxEnabled: boolean }) => void) => {
    handler(Object.freeze({ muted: false, rxEnabled: false }));
    return () => {};
  }),
} satisfies { [K in keyof typeof import('$lib/stores/audio.svelte')]?: (typeof import('$lib/stores/audio.svelte'))[K] }));
vi.mock('$lib/utils/tx-permit', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/utils/tx-permit')>(),
  getTxPermit: vi.fn<(typeof import('$lib/utils/tx-permit'))['getTxPermit']>(() => 'allowed'),
}));

const txHost = vi.hoisted(() => ({ current: undefined as unknown as ManagedAppTxController }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import type { Capabilities } from '$lib/types/capabilities';
import { FTX1_CAPABILITIES } from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';
import radioPowerGolden from '$lib/transport/__tests__/fixtures/radio-power.json';
import { setCapabilities, clearCapabilities } from '$lib/stores/capabilities.svelte';
import { setRadioPowerOn } from '$lib/stores/connection.svelte';
import { resetRadioState } from '$lib/stores/radio.svelte';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { mobileLayout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY,
} from '../../../presentation/workspace/resolution';

let tx: ManagedAppTxHarness;
let components: ReturnType<typeof mount>[] = [];

/** The fixture radio plus the one varied fact: its capability list. */
function capsWith(extra: string[] = []): Capabilities {
  return {
    ...FTX1_CAPABILITIES,
    capabilities: [...(FTX1_CAPABILITIES.capabilities ?? []), ...extra],
  };
}

function setViewport(landscape: boolean) {
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: landscape ? 844 : 390 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: landscape ? 390 : 844 });
}

/** The App mount: the phone layout with the surface plan App resolves for
 *  the active mobile manifest provided through the workspace context (the
 *  MOR-2895 seam — without it `zoned()` cannot answer as in production). */
function mountMobileWithAppPlan(): HTMLElement {
  const plan = resolveSurfacePlan(mobileLayout, readWorkspace({ version: 1 }).workspace);
  const t = document.createElement('div');
  document.body.appendChild(t);
  components.push(mount(MobileRadioLayout, {
    target: t,
    context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
  }));
  flushSync();
  return t;
}

import AppGlobalHost from '../../../AppGlobalHost.svelte';
import LocalExtensionsHost from '$lib/local-extensions/LocalExtensionsHost.svelte';
import { runtime } from '$lib/runtime';
import { getHostUi } from '$lib/local-extensions/host-ui';
import { setRadioHealth } from '$lib/stores/connection.svelte';
import stateGoldens from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import * as client from '$lib/transport/ws-client';

let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;
function pointer(el: Element, type: string, y = 40) {
  el.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 1, clientY: y }));
}
function tap(el: HTMLElement) {
  pointer(el, 'pointerdown'); pointer(el, 'pointerup'); el.click(); flushSync();
}
function button(selector: string): HTMLButtonElement {
  const el = document.querySelector<HTMLButtonElement>(selector);
  expect(el, selector).not.toBeNull();
  return el!;
}
function mountHosts() {
  const target = document.createElement('div'); document.body.append(target);
  components.push(mount(AppGlobalHost, { target }), mount(LocalExtensionsHost, { target }));
  flushSync();
  return target;
}
function setup() {
  setCapabilities(capsWith(['power_control'])); setRadioPowerOn(true);
  const phone = mountMobileWithAppPlan(); mountHosts();
  tap(button('.m-settings-btn'));
  return phone;
}
function assertRequest(which: 'turnOn' | 'turnOff') {
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe(radioPowerGolden.endpoint);
  expect(init?.method).toBe('POST');
  expect(JSON.parse(String(init?.body))).toEqual(radioPowerGolden[which].requestBody);
  expect(Object.keys(init?.headers ?? {})).toEqual(radioPowerGolden.requestHeaderNames);
  expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json');
}

beforeEach(() => {
  components = []; setViewport(false);
  tx = new ManagedAppTxHarness(); txHost.current = tx.controller;
  resetRadioState(); resetCommandLifecycle(); setCapabilities(capsWith());
  setRadioPowerOn(null); setRadioHealth(null); localStorage.clear();
  fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(radioPowerGolden.turnOff.reply), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(async () => {
  for (const component of components) await unmount(component);
  expect(tx.listenerCount()).toBe(0);
  vi.restoreAllMocks(); vi.unstubAllGlobals(); clearCapabilities();
  document.body.innerHTML = ''; localStorage.clear();
});

describe('MOR-4299 mobile radio power', () => {
  it('observes confirmed OFF then ON through the shared browser state documents', async () => {
    vi.stubGlobal('WebSocket', MockWebSocket); instances.length = 0;
    client.connect('ws://test/api/v1/ws'); const socket = instances[0]; socket.simulateOpen();
    try {
      socket.simulateMessage(JSON.stringify({ type: 'state_update', data: stateGoldens['IC-7610'].allObserved }));
      socket.simulateMessage(JSON.stringify({ type: 'state_update', data: {
        ...radioPowerGolden.turnOn.envelope, revision: 100000, transportSeq: 100000,
        stateRevision: 100000, observationSeq: 100000,
      } }));
      setCapabilities({ ...capsWith(['power_control']), powerOnCommand: true });
      mountMobileWithAppPlan(); mountHosts(); tap(button('.m-settings-btn'));
      for (const which of ['turnOff', 'turnOn'] as const) {
        fetchMock.mockClear();
        fetchMock.mockResolvedValue(new Response(JSON.stringify(radioPowerGolden[which].reply)));
        tap(button(which === 'turnOff' ? '.power-toggle-btn' : '.power-on-btn'));
        expect(fetchMock).not.toHaveBeenCalled();
        tap(button('[data-testid="confirm-dialog-confirm"]'));
        await vi.waitFor(() => assertRequest(which));
        await tick();
        socket.simulateMessage(JSON.stringify({ type: 'state_update', data: radioPowerGolden[which].envelope }));
        flushSync();
        expect(runtime.radioPowerOn, which).toBe(which === 'turnOn');
        expect(document.querySelector('[data-testid="global-power-off"]') !== null, which).toBe(which === 'turnOff');
      }
    } finally { client.disconnect(); }
  });

  it('offers no silent Power ON action to a remote controller', () => {
    vi.spyOn(runtime, 'remoteController', 'get').mockReturnValue(true);
    setCapabilities({ ...capsWith(), powerOnCommand: true }); setRadioPowerOn(false);
    mountHosts();
    expect(document.querySelector('.power-on-btn')).toBeNull();
    expect(document.querySelector('.power-off-hint')?.textContent).toContain('cannot be powered on');
  });

  it('forgets an unrecognized sheet press released outside before pointer reentry', () => {
    setup(); const sheet = document.querySelector('.m-sheet')!;
    const capture = vi.fn(); Object.defineProperty(sheet, 'setPointerCapture', { value: capture });
    pointer(sheet, 'pointerdown', 40);
    pointer(sheet, 'pointerleave', 41);
    pointer(document.querySelector('.m-sheet-backdrop')!, 'pointerup', 45);
    pointer(sheet, 'pointerenter', 80); pointer(sheet, 'pointermove', 100);
    expect(capture, 'released press cannot start a phantom drag').not.toHaveBeenCalled();
  });
  it('keeps one power control at the top of SETUP and confirmation outside the sheet across rotation', async () => {
    const phone = setup();
    const power = button('.m-sheet-content .power-toggle-btn');
    expect(document.querySelectorAll('.power-toggle-btn')).toHaveLength(1);
    expect(phone.querySelector('.m-vfo-row .power-toggle-btn')).toBeNull();
    expect(power.parentElement?.firstElementChild).toBe(power);
    const sheet = power.closest('.m-sheet')!;
    const capture = vi.fn(); Object.defineProperty(sheet, 'setPointerCapture', { value: capture });
    tap(power); await tick();
    expect(capture).not.toHaveBeenCalled();
    const dialog = document.querySelector('[role="alertdialog"]')!;
    expect(dialog).not.toBeNull(); expect(dialog.textContent).toContain('Turn OFF the radio?');
    expect(dialog.closest('.m-sheet, .m-sheet-backdrop, .mobile-radio-layout')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    setViewport(true); window.dispatchEvent(new Event('resize')); flushSync();
    expect(document.querySelector('[role="alertdialog"]')).toBe(dialog);
    tap(button('[data-testid="confirm-dialog-confirm"]'));
    await vi.waitFor(() => assertRequest('turnOff'));
  });

  it('cancels without a request and confirms overlay ON once using the same global dialog', async () => {
    setCapabilities({ ...capsWith(), powerOnCommand: true }); setRadioPowerOn(false);
    fetchMock.mockResolvedValue(new Response(JSON.stringify(radioPowerGolden.turnOn.reply)));
    mountHosts();
    tap(button('.power-on-btn'));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(document.querySelector('[role="alertdialog"]')?.textContent).toContain('Turn ON the radio?');
    tap(button('[data-testid="confirm-dialog-cancel"]'));
    expect(fetchMock).not.toHaveBeenCalled();
    tap(button('.power-on-btn')); const confirm = button('[data-testid="confirm-dialog-confirm"]');
    tap(confirm); confirm.removeAttribute('disabled'); confirm.click();
    await vi.waitFor(() => assertRequest('turnOn'));
  });

  it('preserves startup likely-off unknown state, but rechecks overlay capability before confirming', async () => {
    setCapabilities({ ...capsWith(), powerOnCommand: true });
    setRadioHealth({ serverReachable: true, radioLink: 'unknown', readiness: 'stalled', likelyCause: 'radio_powered_off_likely', lastError: null, sinceMs: 0 });
    mountHosts(); tap(button('.power-on-btn'));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(getHostUi()?.setRadioPower(true)).toBe(false);
    setCapabilities({ ...capsWith(), powerOnCommand: false }); flushSync();
    tap(button('[data-testid="confirm-dialog-confirm"]')); await tick();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('reports an overlay ON failure in the shared confirmation', async () => {
    setCapabilities({ ...capsWith(), powerOnCommand: true }); setRadioPowerOn(false);
    fetchMock.mockResolvedValue(new Response('boom', { status: 500 }));
    mountHosts(); tap(button('.power-on-btn'));
    expect(fetchMock).not.toHaveBeenCalled();
    tap(button('[data-testid="confirm-dialog-confirm"]'));
    await vi.waitFor(() => expect(document.querySelector('[data-testid="confirm-dialog-error"]')?.textContent).toContain('boom'));
    tap(button('[data-testid="confirm-dialog-close"]'));
    expect(document.querySelector('[role="alertdialog"]')).toBeNull();
  });

  it('renders no phone power control for unsupported or remote radios and disables unknown state', async () => {
    const phone = mountMobileWithAppPlan(); mountHosts(); tap(button('.m-settings-btn'));
    expect(phone.querySelector('.power-toggle-btn')).toBeNull();
    setCapabilities(capsWith(['power_control'])); flushSync();
    expect(button('.power-toggle-btn').disabled).toBe(true);
    vi.spyOn(runtime, 'remoteController', 'get').mockReturnValue(true);
    await unmount(components.shift()!);
    setRadioPowerOn(true);
    const remotePhone = mountMobileWithAppPlan(); tap(button('.m-settings-btn'));
    expect(remotePhone.querySelector('.power-toggle-btn')).toBeNull();
    expect(getHostUi()?.setRadioPower(false)).toBe(false);
  });

  it('captures only an actual downward drag, including one starting at viewport y=0, and dismisses', async () => {
    vi.useFakeTimers();
    try {
      setup(); const sheet = document.querySelector('.m-sheet')!;
      const capture = vi.fn(); Object.defineProperty(sheet, 'setPointerCapture', { value: capture });
      Object.defineProperty(sheet, 'offsetHeight', { value: 200 });
      const handle = sheet.querySelector('.m-sheet-handle')!;
      pointer(handle, 'pointerdown', 0); expect(capture).not.toHaveBeenCalled();
      pointer(handle, 'pointermove', 3); expect(capture).not.toHaveBeenCalled();
      pointer(handle, 'pointermove', 80); expect(capture).toHaveBeenCalledTimes(1);
      pointer(sheet, 'pointerup', 80); await vi.advanceTimersByTimeAsync(250); flushSync();
      expect(document.querySelector('.m-sheet')).toBeNull();
    } finally { vi.useRealTimers(); }
  });
});
