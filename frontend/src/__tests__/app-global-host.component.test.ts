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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { ManagedAppTxHarness, type ManagedAppTxServerSnapshot } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

let txHarness: ManagedAppTxHarness;

const h = vi.hoisted(() => ({
  onMessage: vi.fn(),
  offMessage: vi.fn(),
  powerOn: vi.fn(),
  radioPowerOn: null as boolean | null,
  radioHealth: null as { radioLink: string; likelyCause?: string } | null,
  powerOnCommand: true,
  ptt: false,
  notifyRuntime: () => {},
  runtime: undefined as unknown,
  provide: vi.fn(),
  registerBarrier: vi.fn(),
  bootstrap: vi.fn(),
  resolveSkin: vi.fn(),
  loadSkin: vi.fn(),
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
    get caps() { subscribe(); return { tx: true, capabilities: ['tx'], powerOnCommand: h.powerOnCommand }; },
    get radioPowerOn() { subscribe(); return h.radioPowerOn; },
    get radioHealth() { subscribe(); return h.radioHealth; },
    get system() { return { powerOn: h.powerOn }; },
    // MOR-1312 slice 12B (rebase fix): `SemanticRadioSurfaces`'s scope-display
    // snapshot (the FIFTH adapter argument) reads these two directly; this
    // fixture declares no scope capability.
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get scope() { return { hardwareScopeConnected: false }; },
    bootstrap: h.bootstrap,
    onTxAudioDied: () => () => {},
  };
  return { runtime: h.runtime };
});
vi.mock('../lib/runtime/frontend-runtime', async () => {
  const mod = await import('$lib/runtime');
  return {
    runtime: mod.runtime,
    // MOR-1060: App bridges presentation resource demand across a swap. The
    // demand model has its own suite; here it only has to exist and stay
    // inert (nothing is demanded, so nothing is bridged).
    presentationResources: {
      snapshot: () => ({ demand: 0 }),
      acquire: () => ({}),
      release: () => true,
    },
  };
});
vi.mock('../lib/transport/ws-client', () => ({
  onMessage: h.onMessage,
  onCommandDelivery: () => () => {},
}));
vi.mock('$lib/i18n', () => ({
  // Interpolate {detail} like the real catalog so failure-text tests
  // can assert content, not just the key. MOR-2671: resolve the RF-risk
  // sentence the same way, so the accessible-name pins assert the real
  // catalog string, and empty param calls keep returning the key.
  t: (key: string, params?: Record<string, string>) =>
    params?.detail !== undefined
      ? `${key}: ${params.detail}`
      : key === 'core.rxTx.rf.unconfirmed'
        ? 'Transmit not confirmed'
        : key,
  messageFromReasonCode: (code: string) => code,
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  provideManagedAppTxHost: h.provide,
  getManagedAppTxController: () => txHarness.controller,
}));
vi.mock('$lib/runtime/system-controller', () => ({
  systemController: { registerPreDisconnectBarrier: h.registerBarrier },
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({ hasAnyScope: () => false }));
vi.mock('$lib/stores/layout.svelte', () => ({ getLayoutMode: () => 'standard' }));
vi.mock('../skins/registry', () => ({
  resolveSkinId: h.resolveSkin,
  loadSkin: h.loadSkin,
  getPresentationRecord: (id: unknown) => ({ id, kind: 'built-in-self-contained', resources: [] }),
}));
vi.mock('../lib/media/media-session', () => ({ initMediaSession: vi.fn(), destroyMediaSession: vi.fn() }));
vi.mock('../components-v2/wiring/SemanticRadioSurfaces.svelte', async () => ({
  default: (await import('./LayoutStub.svelte')).default,
}));
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
import LayoutStub from './LayoutStub.svelte';

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

/**
 * MOR-1060: the presentation reaches App through the lazy loader, so each
 * skin id gets its own component identity wrapping the shared stub — a swap
 * is still a genuine destroy/recreate of the presentation subtree.
 */
type ClientComponent = (anchor: unknown, props: Record<string, unknown>) => void;
const presentationStubs = new Map<string, ClientComponent>();
function presentationStub(skinId: string): ClientComponent {
  let stub = presentationStubs.get(skinId);
  if (!stub) {
    stub = (anchor, props) => (LayoutStub as unknown as ClientComponent)(anchor, { ...props, skinId });
    presentationStubs.set(skinId, stub);
  }
  return stub;
}

function emitTx(next: ManagedAppTxServerSnapshot): void {
  txHarness.emitServerSnapshot(next);
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
const powerEl = () => document.querySelector<HTMLElement>('[data-testid="global-power-off"]');

  beforeEach(() => {
  vi.clearAllMocks();
  txHarness = new ManagedAppTxHarness({ stale: true });
  h.radioPowerOn = null;
  h.radioHealth = null;
  h.powerOnCommand = true;
  h.ptt = false;
  document.body.innerHTML = '';
  // MOR-1240: a failed earlier test may leave the edge property behind.
  document.documentElement.style.removeProperty('--rp-status-bar-bottom');
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
  h.onMessage.mockReturnValue(h.offMessage);
  h.bootstrap.mockResolvedValue(vi.fn());
  h.resolveSkin.mockImplementation(({ isMobile }: { isMobile: boolean }) => (isMobile ? 'mobile' : 'desktop-v2'));
  h.loadSkin.mockImplementation(async (skinId: string) => presentationStub(skinId));
  h.provide.mockReturnValue({ refreshAuthority: vi.fn(), release: vi.fn(), dispose: vi.fn() });
});

describe('AppGlobalHost — standalone, with no layout mounted', () => {
  it('renders global feedback, power/health and TX surfaces without any presentation', () => {
    txHarness.emitServerSnapshot({ observedPtt: 'on', lastError: 'backend-dekeyed' });
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

  it('reports a failed power-on inside the page, never via native alert', async () => {
    h.radioPowerOn = false;
    h.powerOn.mockRejectedValueOnce(new Error('boom'));
    const alertSpy = vi.fn();
    vi.stubGlobal('alert', alertSpy);
    const instance = mountAt(AppGlobalHost);

    powerEl()?.querySelector<HTMLButtonElement>('.power-on-btn')?.click();
    await settle();
    expect(h.powerOn).toHaveBeenCalledTimes(1);
    expect(alertSpy).not.toHaveBeenCalled();

    // The failure text renders in-page via ConfirmDialog's error state.
    await vi.waitFor(() =>
      expect(document.querySelector('[data-testid="confirm-dialog-error"]')?.textContent).toBe(
        'core.overlay.poweredOff.failedPowerOn: Error: boom',
      ),
    );
    expect(document.querySelector('[data-testid="confirm-dialog-confirm"]')).toBeNull();
    document.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-close"]')?.click();
    await settle();
    expect(document.querySelector('[data-testid="confirm-dialog-error"]')).toBeNull();

    unmount(instance);
    vi.unstubAllGlobals();
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

  // MOR-2841 (round 3): a radio that answers nothing at startup gets the
  // existing powered-off overlay — no second overlay, no fabricated
  // powerOn=false. The signal is the server's own verdict,
  // radioHealth.likelyCause 'radio_powered_off_likely' — held from the
  // gate's silent release until the radio's first observation, even while
  // the live link state says 'connected' (the watchdog reopens the silent
  // port within seconds, which the round-2 radioLink condition never saw).
  it('shows the power-off overlay with Power ON while the radio does not answer', () => {
    h.radioHealth = { radioLink: 'connected', likelyCause: 'radio_powered_off_likely' };
    const instance = mountAt(AppGlobalHost);

    const overlay = powerEl();
    expect(overlay).not.toBeNull();
    expect(overlay?.getAttribute('data-state')).toBe('not-answering');
    expect(overlay?.querySelector('.power-off-label')?.textContent)
      .toBe('core.overlay.poweredOff.notAnsweringLabel');
    expect(overlay?.querySelector('.power-on-btn')).not.toBeNull();

    unmount(instance);
  });

  // The overlay follows the state out: once the radio answers, the server
  // clears the cause and powerOn is observed — normal RX, no overlay.
  it('drops the not-answering overlay once the radio answers', () => {
    h.radioHealth = { radioLink: 'connected', likelyCause: 'radio_powered_off_likely' };
    const instance = mountAt(AppGlobalHost);
    expect(powerEl()).not.toBeNull();

    h.radioPowerOn = true;
    h.radioHealth = { radioLink: 'connected', likelyCause: 'unknown' };
    h.notifyRuntime();
    flushSync();
    expect(powerEl()).toBeNull();

    unmount(instance);
  });

  // A known-off radio without a CI-V power-on command (X6100/X6200 shape)
  // gets the plain "cannot power on from here" sentence, not a dead button.
  it('replaces the Power ON action with a plain sentence when the profile has no power_on', () => {
    h.powerOnCommand = false;
    h.radioHealth = { radioLink: 'connected', likelyCause: 'radio_powered_off_likely' };
    const instance = mountAt(AppGlobalHost);

    const overlay = powerEl();
    expect(overlay?.querySelector('.power-on-btn')).toBeNull();
    expect(overlay?.querySelector('.power-off-hint')?.textContent)
      .toBe('core.overlay.poweredOff.noRemotePowerOn');

    unmount(instance);
  });

  // Normal RX must stay overlay-free even while the not-answering cause
  // lingers: powerOn was observed true, so the radio had answered — the
  // cause alone, whatever the link state, must not bring the overlay back.
  it('does not show the not-answering overlay when power is observed on, whatever the link state', () => {
    h.radioPowerOn = true;
    h.radioHealth = { radioLink: 'reconnecting', likelyCause: 'radio_powered_off_likely' };
    const instance = mountAt(AppGlobalHost);
    expect(powerEl()).toBeNull();
    unmount(instance);
  });

  // MOR-2876: the server started while the radio's serial port could not be
  // opened (radioHealth.likelyCause 'radio_not_connected'). The same overlay
  // says so and offers no Power ON, even where the profile declares one:
  // there is no port to send it through.
  it('shows the not-connected overlay without Power ON while the serial port cannot be opened', () => {
    h.radioHealth = { radioLink: 'reconnecting', likelyCause: 'radio_not_connected' };
    const instance = mountAt(AppGlobalHost);

    let overlay = powerEl();
    expect(overlay?.getAttribute('data-state')).toBe('not-connected');
    expect(overlay?.getAttribute('aria-label')).toBe('core.overlay.poweredOff.notConnectedLabel');
    expect(overlay?.querySelector('.power-off-label')?.textContent)
      .toBe('core.overlay.poweredOff.notConnectedLabel');
    expect(h.powerOnCommand).toBe(true);
    expect(overlay?.querySelector('.power-on-btn')).toBeNull();
    expect(overlay?.querySelector('.power-off-hint')).toBeNull();

    // The port opens but the radio stays silent: the server's verdict
    // becomes MOR-2841's, and so does the overlay, Power ON included.
    h.radioHealth = { radioLink: 'connected', likelyCause: 'radio_powered_off_likely' };
    h.notifyRuntime();
    flushSync();
    overlay = powerEl();
    expect(overlay?.getAttribute('data-state')).toBe('not-answering');
    expect(overlay?.querySelector('.power-on-btn')).not.toBeNull();

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
    txHarness.emitServerSnapshot({ observedPtt: 'on' });
    const instance = mountAt(AppGlobalHost);
    expect(txEl()?.getAttribute('data-tx')).toBe('on');
    unmount(instance);
  });

  // MUTATION KILLED: the inverse rewire — an echo that claims TX while the
  // controller is idle must not light the authoritative lamp.
  it('stays dark when the state echo claims TX but the controller is idle', () => {
    h.ptt = true;
    txHarness.emitServerSnapshot({ observedPtt: 'off' });
    const instance = mountAt(AppGlobalHost);
    expect(txEl()).toBeNull();
    unmount(instance);
  });

  // MUTATION KILLED: collapsing the indication to `radioTx === 'on'` only.
  // `txRisk: 'uncertain'` means the browser may own the key without a
  // confirmed readback — the lamp must fail closed, not stay dark.
  // MOR-2671: it reads `TX` — never `TX?` — hollow-defined, with the
  // unconfirmed accessible sentence.
  it('fails closed while TX risk is uncertain', () => {
    txHarness.emitServerSnapshot({ observedPtt: 'unknown', releaseRequired: true });
    const instance = mountAt(AppGlobalHost);
    expect(txEl()?.getAttribute('data-tx')).toBe('uncertain');
    expect(txEl()?.textContent?.trim()).toBe('TX');
    expect(txEl()?.getAttribute('aria-label')).toBe('Transmit not confirmed');
    expect(txEl()?.getAttribute('aria-label')).not.toContain('?');
    expect(txEl()?.querySelector('.global-tx-lamp')?.classList.contains('hollow')).toBe(true);
    unmount(instance);
  });

  // MOR-2671: the confirmed lamp stays filled and named by its text alone.
  it('draws the confirmed TX lamp filled, with no unconfirmed accessible name', () => {
    txHarness.emitServerSnapshot({ observedPtt: 'on' });
    const instance = mountAt(AppGlobalHost);
    expect(txEl()?.getAttribute('data-tx')).toBe('on');
    expect(txEl()?.textContent?.trim()).toBe('TX');
    expect(txEl()?.hasAttribute('aria-label')).toBe(false);
    expect(txEl()?.querySelector('.global-tx-lamp')?.classList.contains('hollow')).toBe(false);
    unmount(instance);
  });

  // MUTATION KILLED: reading `snapshot()` once at init and never
  // subscribing — later authoritative transitions would never reach the lamp.
  it('tracks live controller transitions through the subscription', () => {
    const instance = mountAt(AppGlobalHost);
    expect(txEl()).toBeNull();
    emitTx({ observedPtt: 'unknown', releaseRequired: true });
    expect(txEl()?.getAttribute('data-tx')).toBe('uncertain');
    emitTx({ observedPtt: 'on' });
    expect(txEl()?.getAttribute('data-tx')).toBe('on');
    emitTx({ observedPtt: 'off', lastError: 'release-not-confirmed' });
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
    txHarness.emitServerSnapshot({ observedPtt: 'on', lastError: 'on-timeout' });
    const instance = mountAt(App);
    await settle();
    flushSync();

    const hostBefore = hostEl();
    const layoutBefore = document.querySelector('.layout-stub');
    expect(layoutBefore?.getAttribute('data-skin')).toBe('desktop-v2');
    expect(txEl()).toBeNull();

    // MOR-1060 made the swap asynchronous (the next presentation is loaded
    // lazily), so each hop settles the loader before asserting. The
    // assertions themselves are unchanged.
    const resize = async (width: number) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
      window.dispatchEvent(new Event('resize'));
      flushSync();
      await settle();
      flushSync();
    };

    // A -> B -> A. Each hop genuinely destroys and recreates the layout.
    await resize(390);
    const layoutMobile = document.querySelector('.layout-stub');
    expect(layoutMobile?.getAttribute('data-skin')).toBe('mobile');
    expect(layoutMobile).not.toBe(layoutBefore);
    expect(txEl()?.getAttribute('data-tx')).toBe('on');
    await resize(1200);
    const layoutBack = document.querySelector('.layout-stub');
    expect(layoutBack?.getAttribute('data-skin')).toBe('desktop-v2');
    expect(layoutBack).not.toBe(layoutMobile);

    // The host is untouched by all of it: same node, same single
    // subscription, same pending authoritative TX/fault feedback.
    expect(hostEl()).toBe(hostBefore);
    expect(document.querySelectorAll('.toast-container')).toHaveLength(1);
    expect(h.onMessage).toHaveBeenCalledTimes(1);
    expect(h.offMessage).not.toHaveBeenCalled();
    expect(txEl()).toBeNull();
    expect(faultEl()?.getAttribute('data-fault')).toBe('on-timeout');

    unmount(instance);
  });

  it('releases the host exactly once on App teardown and stays inert afterwards', async () => {
    const instance = mountAt(App);
    await settle();
    flushSync();
    expect(txHarness.listenerCount()).toBe(1);

    unmount(instance);

    expect(txHarness.listenerCount()).toBe(0);
    expect(h.offMessage).toHaveBeenCalledTimes(1);
    expect(hostEl()).toBeNull();

    // A late controller emission after teardown must not resurrect any DOM.
    txHarness.emitServerSnapshot({ observedPtt: 'on', lastError: 'backend-dekeyed' });
    flushSync();
    expect(txEl()).toBeNull();
    expect(faultEl()).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// MOR-1240 — while the radio is powered off, the desktop status bar stays
// usable; a layout without a status bar keeps the full-screen overlay.
// The dependency is inverted (PR #3852 review): the host measures nothing —
// StatusBar publishes `--rp-status-bar-bottom` while mounted and the overlay
// only consumes it in CSS. The publish side is pinned in
// StatusBar.bottom-edge-publish.component.test.ts; jsdom does no layout, so
// what is assertable here is the CSS contract and the fallback.
// ---------------------------------------------------------------------------
describe('MOR-1240 — the powered-off overlay leaves the desktop status bar usable', () => {
  const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), 'utf8');
  const BOTTOM_EDGE = '--rp-status-bar-bottom';
  const bottomEdge = () => document.documentElement.style.getPropertyValue(BOTTOM_EDGE);

  function mountHost() {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const instance = mount(AppGlobalHost, { target });
    flushSync();
    return instance;
  }

  it('starts the overlay at the bar edge StatusBar publishes, through CSS alone', () => {
    h.radioPowerOn = false;
    // A mounted StatusBar (see the publish suite) is the only writer.
    document.documentElement.style.setProperty(BOTTOM_EDGE, '34px');
    const instance = mountHost();

    expect(powerEl()?.style.top).toBe(`var(${BOTTOM_EDGE}, 0px)`);

    document.documentElement.style.removeProperty(BOTTOM_EDGE);
    unmount(instance);
  });

  it('keeps the full-screen overlay when no StatusBar is mounted', () => {
    h.radioPowerOn = false;
    const instance = mountHost();

    expect(powerEl()).not.toBeNull();
    // No bar publishes, so the variable is unset anywhere: the overlay's
    // top collapses to the 0px fallback — full-screen.
    expect(bottomEdge()).toBe('');
    expect(powerEl()?.style.top).toBe(`var(${BOTTOM_EDGE}, 0px)`);

    unmount(instance);
  });

  it('keeps its Power ON action from the host', async () => {
    h.radioPowerOn = false;
    h.powerOn.mockResolvedValue(undefined);
    const instance = mountHost();

    powerEl()?.querySelector<HTMLButtonElement>('.power-on-btn')?.click();
    await settle();
    expect(h.powerOn).toHaveBeenCalledTimes(1);

    unmount(instance);
  });

  // The CSS contract the overlay relies on: StatusBar (and only StatusBar)
  // writes the document-level edge property, and the phone layout composes
  // no StatusBar at all — so it can never grow a top cut by accident.
  it('pins the edge contract: StatusBar publishes it; AppGlobalHost only consumes it in CSS; the phone layout mounts no bar', () => {
    const statusBar = read('../components-v2/layout/StatusBar.svelte');
    expect(statusBar).toMatch(/--rp-status-bar-bottom/);
    expect(statusBar).toMatch(/setProperty\(\s*BOTTOM_EDGE_PROPERTY/);
    expect(statusBar).not.toMatch(/data-status-bar/);

    const host = read('../AppGlobalHost.svelte');
    expect(host).toContain(`var(${BOTTOM_EDGE}, 0px)`);
    // The host must not measure: no DOM lookup for a bar, no observers.
    expect(host).not.toMatch(/querySelector[^)]*status-bar/);
    expect(host).not.toMatch(/ResizeObserver/);

    expect(read('../components-v2/layout/MobileRadioLayout.svelte'))
      .not.toMatch(/StatusBar\.svelte/);
  });

  it('App follows the bar: full-screen on the phone layout, which mounts no bar', async () => {
    h.radioPowerOn = false;
    const instance = mountAt(App);
    await settle();
    flushSync();

    const resize = async (width: number) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
      window.dispatchEvent(new Event('resize'));
      flushSync();
      await settle();
      flushSync();
    };

    // The desktop stub in this harness mounts no bar: nothing ever
    // publishes the edge, so the overlay stays full-screen via the CSS
    // fallback across a layout swap.
    expect(bottomEdge()).toBe('');
    await resize(390);
    expect(document.querySelector('.layout-stub')?.getAttribute('data-skin')).toBe('mobile');
    expect(document.querySelector('.status-bar')).toBeNull();
    expect(bottomEdge()).toBe('');

    unmount(instance);
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
