/**
 * MOR-1059 — the App-global feedback / power-health / authoritative-TX host.
 *
 * These tests pin the composition contract, not the styling:
 *   1. the host renders its surfaces with no layout mounted at all;
 *   2. it survives presentation replacement (one instance, one global
 *      feedback subscription, no lost fault/TX feedback);
 *   3. TX indication reads the App-owned TX controller and nothing else;
 *   4. layouts no longer own the moved surfaces;
 *   5. teardown unsubscribes exactly once and stays inert afterwards.
 */
import { readFileSync } from 'node:fs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

type TxSnapshot = {
  radioTx: 'on' | 'off' | 'unknown';
  txRisk: 'none' | 'uncertain' | 'confirmed-on';
  fault: string | null;
};

const h = vi.hoisted(() => ({
  tx: { radioTx: 'unknown', txRisk: 'none', fault: null } as TxSnapshot,
  txListeners: new Set<(s: TxSnapshot) => void>(),
  stopWatchingTx: vi.fn(),
  onMessage: vi.fn(),
  offMessage: vi.fn(),
  powerOn: vi.fn(),
  radioPowerOn: null as boolean | null,
  ptt: false,
  notifyRuntime: () => {},
  runtime: undefined as unknown,
  provide: vi.fn(),
  registerBarrier: vi.fn(),
  bootstrap: vi.fn(),
  initBattery: vi.fn(),
  resolveSkin: vi.fn(),
}));

vi.mock('$lib/runtime', async () => {
  const { createSubscriber } = await import('svelte/reactivity');
  let update = () => {};
  const subscribe = createSubscriber((notify) => { update = notify; return () => {}; });
  h.notifyRuntime = () => update();
  h.runtime = {
    // `ptt` is deliberately readable here: it is the non-authoritative echo
    // the TX indication must NOT be wired to.
    get state() { subscribe(); return { stateRevision: 1, freshnessRevision: 1, observationSeq: 1, ptt: h.ptt }; },
    get caps() { subscribe(); return { tx: true, capabilities: ['tx'] }; },
    get radioPowerOn() { subscribe(); return h.radioPowerOn; },
    get system() { return { powerOn: h.powerOn }; },
    bootstrap: h.bootstrap,
    setPollingMultiplier: vi.fn(),
  };
  return { runtime: h.runtime };
});
vi.mock('../lib/runtime/frontend-runtime', async () => {
  const mod = await import('$lib/runtime');
  return { runtime: mod.runtime };
});
vi.mock('../lib/transport/ws-client', () => ({ onMessage: h.onMessage }));
vi.mock('$lib/i18n', () => ({
  t: (key: string) => key,
  messageFromReasonCode: (code: string) => code,
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  provideAppTxControllerHost: h.provide,
  getAppTxController: () => ({
    snapshot: () => h.tx,
    subscribe: (listener: (s: TxSnapshot) => void) => {
      h.txListeners.add(listener);
      return h.stopWatchingTx;
    },
  }),
}));
vi.mock('$lib/runtime/system-controller', () => ({
  systemController: { registerPreDisconnectBarrier: h.registerBarrier },
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({ hasAnyScope: () => false }));
vi.mock('$lib/stores/layout.svelte', () => ({ getLayoutMode: () => 'standard' }));
vi.mock('../skins/registry', () => ({ resolveSkinId: h.resolveSkin }));
vi.mock('../lib/utils/battery', () => ({ initBatteryMonitor: h.initBattery }));
vi.mock('../lib/media/media-session', () => ({ initMediaSession: vi.fn(), destroyMediaSession: vi.fn() }));
vi.mock('../components-v2/layout/RadioLayout.svelte', async () => {
  const stub = await import('./LayoutStub.svelte');
  return { default: stub.default };
});
vi.mock('../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('./LayoutStub.svelte');
  return { default: stub.default };
});

import App from '../App.svelte';
import AppGlobalHost from '../AppGlobalHost.svelte';

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

/** Push a new authoritative TX snapshot through the controller subscription. */
function emitTx(next: Partial<TxSnapshot>): void {
  h.tx = { ...h.tx, ...next };
  for (const listener of h.txListeners) listener(h.tx);
  flushSync();
}

function mountAt(component: typeof App | typeof AppGlobalHost) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const instance = mount(component, { target });
  flushSync();
  return instance;
}

const hostEl = () => document.querySelector('[data-testid="app-global-host"]');
const txEl = () => document.querySelector('[data-testid="global-tx-indication"]');
const faultEl = () => document.querySelector('[data-testid="global-tx-fault"]');
const powerEl = () => document.querySelector('[data-testid="global-power-off"]');

beforeEach(() => {
  vi.clearAllMocks();
  h.tx = { radioTx: 'unknown', txRisk: 'none', fault: null };
  h.txListeners.clear();
  h.radioPowerOn = null;
  h.ptt = false;
  document.body.innerHTML = '';
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
  h.onMessage.mockReturnValue(h.offMessage);
  h.bootstrap.mockResolvedValue(vi.fn());
  h.initBattery.mockResolvedValue(vi.fn());
  h.resolveSkin.mockImplementation(({ isMobile }: { isMobile: boolean }) => (isMobile ? 'mobile' : 'desktop-v2'));
  h.provide.mockReturnValue({ refreshAuthority: vi.fn(), release: vi.fn(), dispose: vi.fn() });
});

describe('AppGlobalHost — standalone, with no layout mounted', () => {
  it('renders global feedback, power/health and TX surfaces without any presentation', () => {
    h.tx = { radioTx: 'on', txRisk: 'confirmed-on', fault: 'backend-dekeyed' };
    h.radioPowerOn = false;
    const instance = mountAt(AppGlobalHost);

    // No layout is mounted at all in this test — nothing but the host.
    expect(document.querySelector('.layout-stub')).toBeNull();
    expect(document.querySelector('.toast-container')).not.toBeNull();
    expect(h.onMessage).toHaveBeenCalledTimes(1);
    expect(powerEl()).not.toBeNull();
    expect(txEl()?.getAttribute('data-tx')).toBe('on');
    expect(faultEl()?.getAttribute('data-fault')).toBe('backend-dekeyed');

    unmount(instance);
  });

  it('offers the power-on action from the host, not from a layout status bar', async () => {
    h.radioPowerOn = false;
    h.powerOn.mockResolvedValue(undefined);
    const instance = mountAt(AppGlobalHost);

    powerEl()?.querySelector<HTMLButtonElement>('.power-on-btn')?.click();
    await settle();
    expect(h.powerOn).toHaveBeenCalledTimes(1);

    unmount(instance);
  });

  it('hides the power overlay while power is on or unknown', () => {
    const instance = mountAt(AppGlobalHost);
    expect(powerEl()).toBeNull();          // unknown — fail-quiet, no false alarm
    h.radioPowerOn = true;
    h.notifyRuntime();
    flushSync();
    expect(powerEl()).toBeNull();
    h.radioPowerOn = false;
    h.notifyRuntime();
    flushSync();
    expect(powerEl()).not.toBeNull();
    unmount(instance);
  });
});

describe('AppGlobalHost — authoritative TX source', () => {
  // MUTATION KILLED: rewiring `data-tx` to the command echo
  // (`runtime.state.ptt`) or to any layout-local derivation. The echo says
  // "not transmitting" here while the App-owned controller says the key is
  // down; the operator lamp must follow the controller.
  it('shows TX from the controller even when the state echo claims RX', () => {
    h.ptt = false;
    h.tx = { radioTx: 'on', txRisk: 'none', fault: null };
    const instance = mountAt(AppGlobalHost);
    expect(txEl()?.getAttribute('data-tx')).toBe('on');
    unmount(instance);
  });

  // MUTATION KILLED: the inverse rewire — an echo that claims TX while the
  // controller is idle must not light the authoritative lamp.
  it('stays dark when the state echo claims TX but the controller is idle', () => {
    h.ptt = true;
    h.tx = { radioTx: 'off', txRisk: 'none', fault: null };
    const instance = mountAt(AppGlobalHost);
    expect(txEl()).toBeNull();
    unmount(instance);
  });

  // MUTATION KILLED: collapsing the indication to `radioTx === 'on'` only.
  // `txRisk: 'uncertain'` means the browser may own the key without a
  // confirmed readback — the lamp must fail closed, not stay dark.
  it('fails closed while TX risk is uncertain', () => {
    h.tx = { radioTx: 'unknown', txRisk: 'uncertain', fault: null };
    const instance = mountAt(AppGlobalHost);
    expect(txEl()?.getAttribute('data-tx')).toBe('uncertain');
    unmount(instance);
  });

  // MUTATION KILLED: reading `snapshot()` once at init and never
  // subscribing — later authoritative transitions would never reach the lamp.
  it('tracks live controller transitions through the subscription', () => {
    const instance = mountAt(AppGlobalHost);
    expect(txEl()).toBeNull();
    emitTx({ txRisk: 'uncertain' });
    expect(txEl()?.getAttribute('data-tx')).toBe('uncertain');
    emitTx({ radioTx: 'on', txRisk: 'confirmed-on' });
    expect(txEl()?.getAttribute('data-tx')).toBe('on');
    emitTx({ radioTx: 'off', txRisk: 'none', fault: 'release-not-confirmed' });
    expect(txEl()).toBeNull();
    expect(faultEl()?.getAttribute('data-fault')).toBe('release-not-confirmed');
    unmount(instance);
  });
});

describe('App composition — one host above the presentation boundary', () => {
  it('hosts the global surfaces outside the layout subtree', async () => {
    const instance = mountAt(App);
    await settle();
    flushSync();

    const layout = document.querySelector('.layout-stub');
    expect(layout).not.toBeNull();
    expect(hostEl()).not.toBeNull();
    // MUTATION KILLED: mounting the host inside the layout again.
    expect(layout!.contains(hostEl())).toBe(false);
    expect(document.querySelectorAll('.toast-container')).toHaveLength(1);
    expect(h.onMessage).toHaveBeenCalledTimes(1);

    unmount(instance);
  });

  it('survives repeated presentation replacement without remount or resubscription', async () => {
    h.tx = { radioTx: 'on', txRisk: 'confirmed-on', fault: 'on-timeout' };
    const instance = mountAt(App);
    await settle();
    flushSync();

    const hostBefore = hostEl();
    const layoutBefore = document.querySelector('.layout-stub');
    expect(layoutBefore?.getAttribute('data-skin')).toBe('desktop-v2');

    const resize = (width: number) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
      window.dispatchEvent(new Event('resize'));
      flushSync();
    };

    // A -> B -> A. Each hop genuinely destroys and recreates the layout.
    resize(390);
    const layoutMobile = document.querySelector('.layout-stub');
    expect(layoutMobile?.getAttribute('data-skin')).toBe('mobile');
    expect(layoutMobile).not.toBe(layoutBefore);
    resize(1200);
    const layoutBack = document.querySelector('.layout-stub');
    expect(layoutBack?.getAttribute('data-skin')).toBe('desktop-v2');
    expect(layoutBack).not.toBe(layoutMobile);

    // The host is untouched by all of it: same node, same single
    // subscription, same pending authoritative TX/fault feedback.
    expect(hostEl()).toBe(hostBefore);
    expect(document.querySelectorAll('.toast-container')).toHaveLength(1);
    expect(h.onMessage).toHaveBeenCalledTimes(1);
    expect(h.offMessage).not.toHaveBeenCalled();
    expect(txEl()?.getAttribute('data-tx')).toBe('on');
    expect(faultEl()?.getAttribute('data-fault')).toBe('on-timeout');

    unmount(instance);
  });

  it('releases the host exactly once on App teardown and stays inert afterwards', async () => {
    const instance = mountAt(App);
    await settle();
    flushSync();
    expect(h.txListeners.size).toBe(1);

    unmount(instance);

    expect(h.stopWatchingTx).toHaveBeenCalledTimes(1);
    expect(h.offMessage).toHaveBeenCalledTimes(1);
    expect(hostEl()).toBeNull();

    // A late controller emission after teardown must not resurrect any DOM.
    h.tx = { radioTx: 'on', txRisk: 'confirmed-on', fault: 'backend-dekeyed' };
    for (const listener of h.txListeners) listener(h.tx);
    flushSync();
    expect(txEl()).toBeNull();
    expect(faultEl()).toBeNull();
  });
});

describe('Layouts no longer own the App-global surfaces', () => {
  const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), 'utf8');

  // MUTATION KILLED: re-introducing a layout-local Toast or power overlay,
  // which is how two disagreeing global surfaces get on screen at once.
  it.each([
    ['../components-v2/layout/RadioLayout.svelte'],
    ['../components-v2/layout/LcdLayout.svelte'],
    ['../components-v2/layout/MobileRadioLayout.svelte'],
  ])('%s hosts no Toast and no power-off overlay', (rel) => {
    const source = read(rel);
    expect(source).not.toMatch(/shared\/Toast\.svelte/);
    expect(source).not.toMatch(/<Toast\b/);
    expect(source).not.toMatch(/power-off-overlay/);
  });

  it('keeps the App composition root as the only mount point for the host', () => {
    // A mount is an import plus an element; a prose reference is neither.
    const mountsHost = (source: string) =>
      /import\s+AppGlobalHost\b/.test(source) && /<AppGlobalHost\b/.test(source);
    expect(mountsHost(read('../App.svelte'))).toBe(true);
    const hostMounts = [
      '../components-v2/layout/RadioLayout.svelte',
      '../components-v2/layout/LcdLayout.svelte',
      '../components-v2/layout/MobileRadioLayout.svelte',
    ].filter((rel) => mountsHost(read(rel)));
    expect(hostMounts).toEqual([]);
  });
});
