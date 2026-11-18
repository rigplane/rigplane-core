import { webglFixture } from '$lib/renderers/__tests__/webgl-fixture';
/**
 * MOR-4014 product seam: the "scope is off on the radio" note, from the state
 * documents the real server delivers to the panel on screen.
 *
 * `fixtures/scope-hidden-wire.json` holds the documents a control WebSocket
 * client (the web UI: leaf deltas, `state_delta=2`) actually receives while
 * the scope-health monitor judges the radio's scope silent and then live
 * again. They are produced by the real server and asserted byte for byte by
 * the paired Python test
 * tests/test_scope_hidden_wire_seam_mor4014.py::
 * test_scope_hidden_state_documents_are_the_shared_golden
 * (regenerate with RIGPLANE_UPDATE_SCOPE_HIDDEN_WIRE=1).
 *
 * Here those same documents go through the real ws-client, the real stores
 * and the real runtime facade into a MOUNTED SpectrumPanel: nothing between
 * the socket and the panel is replaced. Only the browser's WebSocket and
 * canvas are stand-ins. Dropping `radioHealth.scopeHidden` anywhere on that
 * path leaves the note off the screen and fails this test.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import scopeHiddenWire from '$lib/transport/__tests__/fixtures/scope-hidden-wire.json';
import { IC7300_CAPABILITIES } from '$lib/runtime/adapters/__tests__/fixtures/ic7300-profile';
import { validateCapabilities } from '$lib/types/capabilities';
import { setCapabilities, clearCapabilities } from '$lib/stores/capabilities.svelte';
import { getRadioHealth } from '$lib/stores/connection.svelte';
import { radio } from '$lib/stores/radio.svelte';
import { runtime } from '$lib/runtime/frontend-runtime';
import * as wsClient from '$lib/transport/ws-client';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import SpectrumPanel from '../SpectrumPanel.svelte';

const fetchCapabilities = vi.hoisted(() => vi.fn());
vi.mock('$lib/transport/http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/http-client')>(), fetchCapabilities }));

// TX is not the subject; the panel only needs the managed-TX provider its
// host page supplies.
const appTxHost = vi.hoisted(() => ({ controller: null as unknown }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => appTxHost.controller,
}));

// The browser stand-ins every mounted-panel test in this directory installs.
const noop = () => {};
const canvas2d: Record<string, unknown> = new Proxy({ canvas: { width: 800, height: 400 } }, {
  get: (target, key: string) => (key in target ? (target as Record<string, unknown>)[key]
    : key === 'getImageData' || key === 'createImageData'
      ? (_a: number, _b: number, w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h })
      : key === 'createLinearGradient' || key === 'createRadialGradient' ? () => ({ addColorStop: noop })
        : key === 'measureText' ? () => ({ width: 0 }) : noop),
  set: () => true,
});
HTMLCanvasElement.prototype.getContext = vi.fn(function (this: HTMLCanvasElement, type: string) {
  if (type === 'webgl2') return webglFixture(this.width, this.height).gl;
  return type === '2d' ? canvas2d : null;
}) as never;
globalThis.ResizeObserver = class {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element) {
    this.callback([{ target, contentRect: { width: 200, height: 100 } } as ResizeObserverEntry], this as never);
  }
  unobserve() {}
  disconnect() {}
} as never;
globalThis.requestAnimationFrame = vi.fn((cb: FrameRequestCallback) => setTimeout(() => cb(performance.now()), 0) as never);
globalThis.cancelAnimationFrame = vi.fn((id: number) => clearTimeout(id));
Element.prototype.scrollIntoView = vi.fn();

const HIDDEN = 'Scope is off on the radio — it is showing a menu or another screen';

describe('SpectrumPanel scope-hidden note over the real wire documents (MOR-4014)', () => {
  let target: HTMLElement;
  let panel: ReturnType<typeof mount> | undefined;
  let originalWebSocket: typeof WebSocket;

  const deliver = (role: keyof typeof scopeHiddenWire) => {
    instances[0].simulateMessage(JSON.stringify({ type: 'state_update', data: scopeHiddenWire[role] }));
    flushSync();
  };
  const note = () => target.querySelector('[data-testid="scope-hidden-overlay"]');
  const disconnected = () => target.querySelector('.scope-disconnected-overlay:not([data-testid])');

  beforeEach(() => {
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error the browser's WebSocket, replaced by the fake backend
    globalThis.WebSocket = MockWebSocket;
    instances.length = 0;
    const full = scopeHiddenWire.ordinary;
    setCapabilities(validateCapabilities({
      ...IC7300_CAPABILITIES,
      providerGeneration: full.providerGeneration,
      stateContractVersion: full.stateContractVersion,
    }));
    fetchCapabilities.mockResolvedValue(validateCapabilities({
      ...IC7300_CAPABILITIES,
      providerGeneration: full.providerGeneration,
      stateContractVersion: full.stateContractVersion,
    }));
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    appTxHost.controller = new ManagedAppTxHarness().controller;
    target = document.createElement('div');
    document.body.appendChild(target);
  });

  afterEach(async () => {
    if (panel) await unmount(panel);
    panel = undefined;
    appTxHost.controller = null;
    wsClient.disconnect();
    document.body.innerHTML = '';
    radio.current = null;
    clearCapabilities();
    globalThis.WebSocket = originalWebSocket;
  });

  it('shows the note while the server says the scope is hidden and drops it on recovery', () => {
    deliver('ordinary');
    panel = mount(SpectrumPanel, { target, props: {} });
    flushSync();
    // Nothing judged yet: the ordinary state. No frames either, so the
    // generic overlay speaks, never the radio-menu note.
    expect(getRadioHealth()?.scopeHidden).toBeUndefined();
    expect(note()).toBeNull();
    expect(disconnected()).not.toBeNull();

    deliver('shown');
    expect(runtime.radioHealth?.scopeHidden).toBe(false);
    expect(note()).toBeNull();
    expect(disconnected()).not.toBeNull();

    deliver('hidden');
    expect(runtime.radioHealth?.scopeHidden).toBe(true);
    expect(note()?.textContent).toBe(HIDDEN);
    expect(disconnected()).toBeNull();

    deliver('recovered');
    expect(runtime.radioHealth?.scopeHidden).toBe(false);
    expect(note()).toBeNull();
    expect(disconnected()).not.toBeNull();
  });
});
