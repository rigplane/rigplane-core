/**
 * MOR-4798 product seam: a radio that advertises NO scope source at all (the
 * FTX-1 sends no spectrum scope over CAT) must say so plainly on the panorama
 * panel — never the generic "reconnecting" overlay, and never a per-model
 * branch. A scope-capable radio (IC-7300) must not show the note.
 *
 * Mounts the real SpectrumPanel against the real capability stores with the
 * real served capability documents (FTX-1: `scope: false`, no `scopeSources`,
 * no `scopeDefaultSourceId`; IC-7300: hardware scope). Only the browser's
 * WebSocket/canvas are stand-ins. If the panel stops reading the no-source
 * fact from capability data, the note leaves the screen and this fails.
 */
import { webglFixture } from '$lib/renderers/__tests__/webgl-fixture';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { FTX1_CAPABILITIES } from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';
import { IC7300_CAPABILITIES } from '$lib/runtime/adapters/__tests__/fixtures/ic7300-profile';
import { setCapabilities, clearCapabilities } from '$lib/stores/capabilities.svelte';
import { radio } from '$lib/stores/radio.svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import SpectrumPanel from '../SpectrumPanel.svelte';

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

const NO_SCOPE_TEXT = 'This radio does not send a spectrum scope over its CAT connection.';

describe('SpectrumPanel no-scope-source note (MOR-4798)', () => {
  let target: HTMLElement;
  let panel: ReturnType<typeof mount> | undefined;

  const note = () => target.querySelector('[data-testid="scope-no-source-overlay"]');
  const generic = () => target.querySelector('.scope-disconnected-overlay:not([data-testid])');

  beforeEach(() => {
    appTxHost.controller = new ManagedAppTxHarness().controller;
    target = document.createElement('div');
    document.body.appendChild(target);
  });

  afterEach(async () => {
    if (panel) await unmount(panel);
    panel = undefined;
    appTxHost.controller = null;
    document.body.innerHTML = '';
    radio.current = null;
    clearCapabilities();
  });

  it('says plainly that a scopeless radio sends no scope instead of "reconnecting"', () => {
    setCapabilities({ ...FTX1_CAPABILITIES });
    panel = mount(SpectrumPanel, { target, props: {} });
    flushSync();
    expect(note()?.textContent).toBe(NO_SCOPE_TEXT);
    expect(generic()).toBeNull();
  });

  it('shows no such note for a scope-capable radio', () => {
    setCapabilities({ ...IC7300_CAPABILITIES });
    panel = mount(SpectrumPanel, { target, props: {} });
    flushSync();
    expect(note()).toBeNull();
  });

  it('shows the disconnected overlay for scope: true with no source fields, not no-source', () => {
    const caps = { ...IC7300_CAPABILITIES, scope: true };
    delete caps.scopeSources;
    delete caps.scopeDefaultSourceId;
    delete caps.scopeSource;
    setCapabilities(caps);
    panel = mount(SpectrumPanel, { target, props: {} });
    flushSync();
    expect(note()).toBeNull();
    expect(generic()).not.toBeNull();
  });
});
