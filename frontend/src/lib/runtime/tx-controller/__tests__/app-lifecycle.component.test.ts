import { beforeEach, describe, expect, it, vi } from 'vitest';
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
vi.mock('../../../../skins/registry', () => ({ resolveSkinId: h.resolveSkin }));
vi.mock('../../../../lib/utils/battery', () => ({ initBatteryMonitor: h.initBattery }));
vi.mock('../../../../lib/media/media-session', () => ({ initMediaSession: h.initMedia, destroyMediaSession: h.destroyMedia }));
vi.mock('../../../../lib/runtime/frontend-runtime', async () => {
  const { createSubscriber } = await import('svelte/reactivity');
  let update = () => {};
  const subscribe = createSubscriber((notify) => { update = notify; return () => {}; });
  h.notifyRuntime = () => update();
  return { runtime: {
    get state() { subscribe(); return h.radio; },
    get caps() { subscribe(); return h.caps; },
    bootstrap: h.bootstrap, setPollingMultiplier: vi.fn(),
  } };
});
vi.mock('$lib/runtime/system-controller', () => ({ systemController: { registerPreDisconnectBarrier: h.registerBarrier } }));
vi.mock('$lib/i18n', () => ({ t: (key: string) => key }));
vi.mock('../app-host', () => ({ provideAppTxControllerHost: h.provide }));
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
