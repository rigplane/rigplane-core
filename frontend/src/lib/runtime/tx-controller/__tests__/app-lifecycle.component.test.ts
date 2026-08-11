import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
const h = vi.hoisted(() => ({
  order: [] as string[],
  provide: vi.fn(),
  registerBarrier: vi.fn(),
  bootstrap: vi.fn(),
  bootstrapCleanup: vi.fn(),
  initBattery: vi.fn(),
  batteryCleanup: vi.fn(),
  initMedia: vi.fn(),
  destroyMedia: vi.fn(),
  resolveSkin: vi.fn(),
  radio: null as { stateRevision: number; freshnessRevision: number; observationSeq: number; ptt: boolean } | null,
  caps: null as { tx: boolean; capabilities: string[] } | null,
  notifyRuntime: () => {},
  barrier: undefined as (() => Promise<void>) | undefined,
  host: undefined as {
    refreshAuthority: ReturnType<typeof vi.fn>;
    release: ReturnType<typeof vi.fn>;
    dispose: ReturnType<typeof vi.fn>;
  } | undefined,
  inFlight: null as Promise<void> | null,
}));
vi.mock('../../../../components-v2/layout/RadioLayout.svelte', async () => {
  const stub = await import('../../../../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('../../../../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('../../../../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('$lib/stores/capabilities.svelte', () => ({ hasAnyScope: () => false }));
vi.mock('$lib/stores/layout.svelte', () => ({ getLayoutMode: () => 'standard' }));
// MOR-1060: App loads the presentation lazily; serve the same stub through
// the loader so the mounted tree is unchanged for these TX lifecycle tests.
vi.mock('../../../../skins/registry', () => ({
  resolveSkinId: h.resolveSkin,
  loadSkin: async () => (await import('../../../../components-v2/layout/__tests__/SpectrumPanelStub.svelte')).default,
  presentationResourcePlan: () => [],
}));
vi.mock('../../../../lib/utils/battery', () => ({ initBatteryMonitor: h.initBattery }));
vi.mock('../../../../lib/media/media-session', () => ({ initMediaSession: h.initMedia, destroyMediaSession: h.destroyMedia }));
vi.mock('../../../../lib/runtime/frontend-runtime', async () => {
  const { createSubscriber } = await import('svelte/reactivity');
  let update = () => {};
  const subscribe = createSubscriber((notify) => { update = notify; return () => {}; });
  h.notifyRuntime = () => update();
  return {
    runtime: {
      get state() { subscribe(); return h.radio; },
      get caps() { subscribe(); return h.caps; },
      bootstrap: h.bootstrap,
    },
    // MOR-1060 swap-bridge surface; inert here (nothing is demanded).
    presentationResources: {
      snapshot: () => ({ demand: 0 }),
      acquire: () => ({}),
      release: () => true,
    },
  };
});
vi.mock('$lib/runtime/system-controller', () => ({ systemController: { registerPreDisconnectBarrier: h.registerBarrier } }));
vi.mock('$lib/i18n', () => ({ t: (key: string) => key }));
vi.mock('../app-host', () => ({ provideAppTxControllerHost: h.provide }));
// The App-global status host (MOR-1059) is stubbed here: these tests own the
// TX controller lifecycle, and the host has its own focused suite.
vi.mock('../../../../AppGlobalHost.svelte', async () => {
  const stub = await import('../../../../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});
import App from '../../../../App.svelte';
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
function mountApp() {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(App, { target });
  flushSync();
  return component;
}
beforeEach(() => {
  vi.clearAllMocks();
  h.order.length = 0;
  h.barrier = undefined;
  h.host = undefined;
  h.inFlight = null;
  h.radio = { stateRevision: 1, freshnessRevision: 1, observationSeq: 1, ptt: false };
  h.caps = { tx: true, capabilities: ['tx'] };
  document.body.innerHTML = '';
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  h.resolveSkin.mockImplementation(({ isMobile }: { isMobile: boolean }) => isMobile ? 'mobile' : 'desktop-v2');
  h.bootstrapCleanup.mockImplementation(() => { h.order.push('runtime'); });
  h.bootstrap.mockResolvedValue(h.bootstrapCleanup);
  h.batteryCleanup.mockImplementation(() => { h.order.push('battery'); });
  h.initBattery.mockResolvedValue(h.batteryCleanup);
  h.destroyMedia.mockImplementation(() => { h.order.push('media'); });
  h.registerBarrier.mockImplementation((barrier: () => Promise<void>) => {
    h.barrier = barrier;
    return () => { if (h.barrier === barrier) h.barrier = undefined; };
  });
  h.provide.mockImplementation((bindings) => {
    const release = vi.fn(() => {
      if (h.inFlight) return h.inFlight;
      h.order.push('off');
      let bounded!: Promise<void>;
      bounded = Promise.resolve().finally(() => {
        if (h.inFlight === bounded) h.inFlight = null;
      });
      return (h.inFlight = bounded);
    });
    const offBarrier = bindings.registerPreDisconnectBarrier(release);
    const offLifecycle = bindings.lifecycleReleaseSource(() => { void release(); });
    const host = {
      refreshAuthority: vi.fn(() => { h.order.push('refresh'); }),
      release,
      dispose: vi.fn(() => {
        void release();
        offBarrier();
        offLifecycle();
        h.order.push('host:dispose');
      }),
    };
    h.host = host;
    return host;
  });
});
describe('App TX lifecycle', () => {
  it('keeps one owner while presentation changes and coalesces every release route', async () => {
    const component = mountApp();
    expect(h.host!.refreshAuthority).not.toHaveBeenCalled();
    await settle();
    expect(h.provide).toHaveBeenCalledOnce();
    expect(h.host!.refreshAuthority).toHaveBeenCalledOnce();
    h.radio!.ptt = true;
    h.radio!.observationSeq++;
    h.caps!.capabilities.push('voice_tx');
    h.notifyRuntime();
    await settle();
    expect(h.host!.refreshAuthority).toHaveBeenCalledTimes(2);
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    window.dispatchEvent(new Event('resize'));
    flushSync();
    expect(h.provide).toHaveBeenCalledOnce();

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('pagehide'));
    const shutdown = h.barrier!();
    expect(h.host!.release).toHaveBeenCalledTimes(3);
    expect(h.order.filter((entry) => entry === 'off')).toHaveLength(1);
    await shutdown;
    unmount(component);
  });
  it('attempts release before local and late runtime cleanup, then detaches lifecycle paths', async () => {
    let resolveBootstrap!: (cleanup: () => void) => void;
    h.bootstrap.mockImplementationOnce(() => new Promise((resolve) => { resolveBootstrap = resolve; }));
    const component = mountApp();
    await settle();
    h.order.length = 0;
    unmount(component);
    expect(h.host!.dispose).toHaveBeenCalledOnce();
    expect(h.order.indexOf('off')).toBeLessThan(h.order.indexOf('media'));
    const calls = h.host!.release.mock.calls.length;
    window.dispatchEvent(new Event('pagehide'));
    expect(h.host!.release).toHaveBeenCalledTimes(calls);
    expect(h.barrier).toBeUndefined();
    resolveBootstrap(h.bootstrapCleanup);
    await settle();
    expect(h.order.indexOf('off')).toBeLessThan(h.order.indexOf('runtime'));
    expect(h.bootstrapCleanup).toHaveBeenCalledOnce();
    expect(h.host!.refreshAuthority).not.toHaveBeenCalled();
    h.radio!.ptt = false;
    h.caps!.capabilities.push('mod_input_routing');
    h.notifyRuntime();
    await settle();
    expect(h.host!.refreshAuthority).not.toHaveBeenCalled();
    expect(h.bootstrapCleanup).toHaveBeenCalledOnce();
  });
});

// MOR-1168 — a deferred runtime.bootstrap() rejection must not drive
// backendError/retry effects or arm a reload timer once App has unmounted.
// jsdom's `location.reload` is non-configurable, so these assert on the
// global setTimeout/clearTimeout calls (the retry-timer arm/clear) instead
// of ever letting the real reload callback run.
describe('App bootstrap rejection lifecycle (MOR-1168)', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function pendingReject() {
    let rejectBootstrap!: (err: unknown) => void;
    h.bootstrap.mockImplementationOnce(
      () => new Promise((_resolve, reject) => { rejectBootstrap = reject; }),
    );
    return () => rejectBootstrap;
  }

  it('makes a rejection that arrives after unmount inert: no retry timer armed', async () => {
    const getReject = pendingReject();
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');
    const component = mountApp();
    await settle();
    unmount(component);
    expect(h.host!.dispose).toHaveBeenCalledOnce();
    setTimeoutSpy.mockClear();

    getReject()(new Error('late failure after unmount'));
    await settle();
    flushSync();

    // Removing the `!mounted` guard in the catch handler would arm a
    // retry setTimeout (and eventually call location.reload()) here.
    expect(setTimeoutSpy).not.toHaveBeenCalled();
  });

  it('preserves bounded retry/error behavior for a rejection while still mounted', async () => {
    const getReject = pendingReject();
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');
    const component = mountApp();
    await settle();
    setTimeoutSpy.mockClear();

    getReject()(new Error('mounted failure'));
    await settle();
    flushSync();

    expect(document.body.textContent).toContain('core.app.backendError');
    expect(document.querySelector('.retry-indicator')).not.toBeNull();
    // A mounted rejection still arms the bounded reload-retry timer.
    expect(setTimeoutSpy).toHaveBeenCalledTimes(1);
    expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), 3000);
    unmount(component);
  });

  it('clears an already-armed retry timer exactly once when unmount races the pending reload', async () => {
    const getReject = pendingReject();
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');
    const clearTimeoutSpy = vi.spyOn(globalThis, 'clearTimeout');
    const component = mountApp();
    await settle();
    setTimeoutSpy.mockClear();

    getReject()(new Error('mounted failure, then unmount before retry fires'));
    await settle();
    flushSync();
    expect(setTimeoutSpy).toHaveBeenCalledTimes(1);
    const armedTimer = setTimeoutSpy.mock.results[0]!.value;
    clearTimeoutSpy.mockClear();

    unmount(component);

    // Pre-existing cleanup (untouched by this fix) must still clear an
    // already-armed retry timer exactly once when unmount races it.
    expect(clearTimeoutSpy).toHaveBeenCalledOnce();
    expect(clearTimeoutSpy).toHaveBeenCalledWith(armedTimer);
  });
});

// MOR-1409 A10 — causal RED for the retirement of the battery-to-polling
// hook. On exact base, mount subscribes to the battery monitor solely to
// feed the (already-inert since A09b) polling-cadence multiplier; this pins
// its removal.
describe('App battery-monitor subscription removal (A10)', () => {
  it('mounts without subscribing to the battery monitor', async () => {
    const component = mountApp();
    await settle();
    expect(h.initBattery).not.toHaveBeenCalled();
    unmount(component);
  });
});
