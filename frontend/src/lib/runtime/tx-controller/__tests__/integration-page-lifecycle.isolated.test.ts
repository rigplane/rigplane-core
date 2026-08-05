import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

// ─── Page-lifecycle release matrix — pagehide/visibilitychange TX release
// through the REAL stack (MOR-1089 U6) ───────────────────────────────────────
//
// U4/U5 wire the real `WsChannel` + real `createBrowserTxControllerDependencies`
// + real `TxController` by hand, in a `setup()` helper, explicitly BECAUSE
// `provideAppTxControllerHost` cannot run outside a live Svelte component (it
// calls `getContext`/`setContext`). This file is the one place that pays that
// cost: it mounts the REAL `App.svelte`, whose script body is the ONLY
// production call site of `provideAppTxControllerHost` — and, critically, the
// ONLY place `window.addEventListener('pagehide', ...)` /
// `document.addEventListener('visibilitychange', ...)` are ever registered
// (see `lifecycleReleaseSource` in `src/App.svelte`). That wiring is not
// extracted anywhere reusable, so proving it end-to-end means mounting the
// real component that contains it — a hand-rolled stand-in would test our
// own reproduction of the wiring, not the production code path.
//
// Real: `App.svelte`, `app-host.ts` (`provideAppTxControllerHost` — NOT
// mocked), `browser-dependencies.ts`, `controller.ts`/`model.ts`
// (`TxController`), the real `WsChannel` singleton in `$lib/transport/ws-client`
// (driven only at the socket boundary by the shared `MockWebSocket` fake —
// U0), and `$lib/stores/connection.svelte` (harmless real `$state` setters
// under jsdom, same as U4/U5).
//
// Mocked: the same facts seam U2/U4/U5 mock (`radio.svelte`, `capabilities.svelte`,
// `tx-adapter`) so the test controls what `browser-dependencies.ts` reads: PLUS
// whatever `App.svelte` needs to mount at all in jsdom without pulling in the
// full `RadioLayoutV2` component tree (audio, scope, every panel) — the exact
// same substitutions `app-lifecycle.component.test.ts` makes (RadioLayout →
// stub, LocalExtensionsHost → stub, layout/skins/battery/media-session/i18n →
// trivial fakes, `frontend-runtime` → a controllable reactive fake, so the
// test can force `App.svelte`'s own `$effect` to call `txHost.refreshAuthority()`
// on demand). `system-controller` is mocked too: its `registerPreDisconnectBarrier`
// binding is a SEPARATE lifecycle path (already covered by
// `app-lifecycle.component.test.ts`'s barrier case) that this file isn't
// about, and it holds real singleton state (audio manager, media session,
// polling) this file has no reason to touch.
//
// One departure from the app-lifecycle.component.test.ts pattern:
// `RadioLayoutV2` is replaced not by a mute stub but by `TxControllerProbe.svelte`
// (support/TxControllerProbe.svelte), which calls the real `getAppTxController()`
// from inside the real component tree (Svelte context only resolves from a
// live component) and stashes the real facade so this file can drive
// `.start()`/`.release()` on it directly — the same object a real panel
// (TxPanel.svelte) would retrieve, not a hand-built substitute.
//
// Unlike U4/U5, this file does NOT `vi.resetModules()` between tests. U4/U5
// need it because `ws-client`'s module-level singletons (`_ctrl`) persist
// across `it()` blocks and each of their tests hand-builds a fresh
// `TxController`/authority-projector pair anyway (so a stale singleton epoch
// is harmless — only the *listener registrations* need to not leak). Here
// mounting a real `App.svelte` puts a second, framework-level singleton in
// play: `svelte` itself. `vi.resetModules()` would force a freshly-reloaded
// `App.svelte` to be compiled against a freshly-reloaded `svelte` runtime,
// while any statically-imported `mount`/`unmount` reference (or a
// differently-timed dynamic reimport) risks resolving to a DIFFERENT
// instance of `svelte`'s internal effect-scheduling state — verified
// empirically while building this file: the first `it()` passes either way,
// every subsequent one fails Svelte context resolution
// (`getAppTxController()` throwing "App TxController host is not provided")
// once `vi.resetModules()` is in the loop. Every test here instead: (a) fully
// unmounts its `App` instance (which runs `browser-dependencies.ts`'s own
// `dispose()`, clearing every tracked real timer and listener), and (b)
// explicitly closes the real control-channel singleton in `afterEach` so the
// next test's `wsClient.connect(...)` opens a genuinely fresh `MockWebSocket`
// instead of a no-op against an already-open channel.

const h = vi.hoisted(() => ({
  radio: null as any,
  caps: null as any,
  start: vi.fn(async (): Promise<string | null> => null),
  stop: vi.fn(),
  restore: vi.fn(),
  registerBarrier: vi.fn(),
  bootstrap: vi.fn(),
  bootstrapCleanup: vi.fn(),
  initBattery: vi.fn(),
  batteryCleanup: vi.fn(),
  initMedia: vi.fn(),
  destroyMedia: vi.fn(),
  notifyRuntime: () => {},
  runtimeState: { stateRevision: 1 } as Record<string, unknown> | null,
  runtimeCaps: { tx: true } as Record<string, unknown> | null,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: () => h.radio,
  setRadioState: () => {},
  patchActiveReceiver: () => {},
  patchRadioState: () => {},
  resetRadioState: () => {},
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: () => h.caps,
  hasAnyScope: () => false,
}));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({
  getTxAudioControl: () => ({
    startTx: h.start,
    stopLocalAudio: h.stop,
    restoreModAfterConfirmedOff: h.restore,
  }),
}));
vi.mock('../../../../components-v2/layout/RadioLayout.svelte', async () => {
  const stub = await import('./support/TxControllerProbe.svelte');
  return { default: stub.default };
});
vi.mock('../../../../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('../../../../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('$lib/stores/layout.svelte', () => ({ getLayoutMode: () => 'standard' }));
// MOR-1060: App now loads the presentation lazily through the registry, so
// the probe is served by `loadSkin` rather than by the RadioLayout module
// substitution above. Same tree, same captured controller.
vi.mock('../../../../skins/registry', () => ({
  resolveSkinId: () => 'desktop-v2',
  loadSkin: async () => (await import('./support/TxControllerProbe.svelte')).default,
  presentationResourcePlan: () => [],
}));
vi.mock('../../../../lib/utils/battery', () => ({ initBatteryMonitor: h.initBattery }));
vi.mock('../../../../lib/media/media-session', () => ({
  initMediaSession: h.initMedia,
  destroyMediaSession: h.destroyMedia,
}));
vi.mock('../../../../lib/runtime/frontend-runtime', async () => {
  const { createSubscriber } = await import('svelte/reactivity');
  let update = () => {};
  const subscribe = createSubscriber((notify) => {
    update = notify;
    return () => {};
  });
  h.notifyRuntime = () => update();
  return {
    runtime: {
      get state() {
        subscribe();
        return h.runtimeState;
      },
      get caps() {
        subscribe();
        return h.runtimeCaps;
      },
      bootstrap: h.bootstrap,
      setPollingMultiplier: vi.fn(),
    },
    presentationResources: {
      snapshot: () => ({ demand: 0 }),
      acquire: () => ({}),
      release: () => true,
    },
  };
});
vi.mock('$lib/runtime/system-controller', () => ({
  systemController: { registerPreDisconnectBarrier: h.registerBarrier },
}));
vi.mock('$lib/i18n', () => ({ t: (key: string) => key }));

import App from '../../../../App.svelte';
import * as wsClient from '$lib/transport/ws-client';
import * as connection from '$lib/stores/connection.svelte';
import { capturedController, resetCapturedController } from './support/TxControllerProbe.svelte';
import type { AppTxController } from '../app-host';

const field = (at: number) => ({
  observed: true,
  freshness: 'fresh' as const,
  availability: 'available' as const,
  lastObservedMonotonic: at,
  source: { source: 'poll_response' },
});
const target = { receiver: 'SUB' as const, slot: 'B' as const, frequencyHz: 150 };

function resetFacts(): void {
  h.radio = {
    revision: 1, ptt: false, active: 'SUB', txTarget: { status: 'known', ...target },
    main: { dataMode: 0 }, sub: { dataMode: 1 }, data1ModInput: 5,
    fieldStatus: { ptt: field(1), txTarget: field(1), data1ModInput: field(1) },
  };
  h.caps = {
    tx: true, audioTx: true, capabilities: ['tx', 'mod_input_routing'],
    vfoScheme: 'main_sub', audioTxRequiredModInputSource: 5, txBands: [{ start: 100, end: 200 }],
  } as Capabilities;
}

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

/** Bump the PTT fieldStatus to a fresh, newer monotonic reading and force
 * `App.svelte`'s own `$effect` (which depends on the mocked `runtime.state`/
 * `runtime.caps`) to re-run, which calls the REAL `txHost.refreshAuthority()`
 * — the same reactivity glue a real backend push does in production. The
 * very first authority projection computed once the control session goes
 * 'connected' is deliberately swallowed as a non-fresh baseline (this is
 * `app-authority.ts`'s own anti-replay guard, not a test artifact — see
 * `integration-lifecycle-matrix.isolated.test.ts`'s `dispatchStart` for the identical
 * two-step shape), so every caller needs at least one of these before a PTT
 * observation is treated as authoritative. */
async function observePtt(value: boolean, at: number): Promise<void> {
  h.radio = { ...h.radio, ptt: value, fieldStatus: { ...h.radio.fieldStatus, ptt: field(at) } };
  h.notifyRuntime();
  await settle();
}

function sentFrames(socket: MockWebSocket): Array<{ name: string }> {
  return socket.sent.map((raw) => JSON.parse(raw));
}
function countFrames(name: string, ...sockets: MockWebSocket[]): number {
  return sockets.flatMap(sentFrames).filter((f) => f.name === name).length;
}

/** Mount the real App, bring its control session to 'connected' over a fresh
 * MockWebSocket (mirroring U4/U5's `setup()`), and return the real
 * `AppTxController` facade the mounted tree captured via context. The
 * mounted instance is stashed in `mountedComponent` (describe-scope) so
 * `afterEach` can unconditionally unmount it — including when an `it()`
 * throws before reaching its own cleanup line, which would otherwise leave
 * a live `App` instance with its real `window`/`document` listeners still
 * attached, silently contaminating the next test. (Caught while building
 * this file's teeth check: a deliberately-failed first case left exactly
 * this kind of ghost listener behind and produced a confusing, unrelated
 * failure in the next case.) */
let mountedComponent: object | null = null;
async function mountConnectedApp(): Promise<{
  component: object; controller: AppTxController; socket: MockWebSocket;
}> {
  resetFacts();
  resetCapturedController();
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(App, { target });
  mountedComponent = component;
  flushSync();
  await settle(); // let onMount's runtime.bootstrap() resolve → txAuthorityReady = true
  // …and the lazy presentation loader commit + flush, which is what mounts
  // the probe that captures the controller (MOR-1060).
  await vi.waitFor(() => {
    flushSync();
    if (!capturedController()) throw new Error('presentation not committed yet');
  });

  connection.setRadioReady(true);
  wsClient.connect('ws://test/api/v1/ws');
  const socket = instances[0];
  socket.simulateOpen(); // fires the real subscribeSession callback in app-host

  await observePtt(false, 2); // first fresh (non-baseline) PTT reading

  const controller = capturedController();
  if (!controller) throw new Error('TxControllerProbe never captured a controller');
  return { component, controller, socket };
}

describe('App page-lifecycle TX release — real App.svelte + real app-host + real WsChannel', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    mountedComponent = null;
    document.body.innerHTML = '';
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    instances.length = 0;
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error install the mock as the global WebSocket constructor
    globalThis.WebSocket = MockWebSocket;
    h.bootstrap.mockReset().mockResolvedValue(h.bootstrapCleanup);
    h.start.mockReset().mockResolvedValue(null);
    h.stop.mockClear();
    h.restore.mockClear();
    h.registerBarrier.mockReset().mockImplementation(() => () => {});
    h.initBattery.mockReset().mockResolvedValue(h.batteryCleanup);
    h.batteryCleanup.mockReset();
    h.initMedia.mockReset();
    h.destroyMedia.mockReset();
    h.runtimeState = { stateRevision: 1 };
    h.runtimeCaps = { tx: true };
  });

  afterEach(() => {
    // Unconditionally unmount whatever the test mounted — regardless of
    // whether the `it()` body reached its own cleanup line — so a failing
    // assertion never leaves a live App instance (and its real
    // window/document listeners) attached for the next test to trip over.
    if (mountedComponent) {
      try { unmount(mountedComponent); } catch { /* already unmounted */ }
      mountedComponent = null;
    }
    // Close the real singleton control channel so the next test's
    // `wsClient.connect(...)` isn't a no-op against an already-open one.
    wsClient.disconnectAll();
    globalThis.WebSocket = originalWebSocket;
  });

  it('pagehide during an owned key releases the lease — fail-closed OFF reaches the wire', async () => {
    const { controller, socket } = await mountConnectedApp();

    controller.start('probe', 'lease-pagehide', 'momentary');
    await settle();
    expect(controller.snapshot()).toMatchObject({ phase: 'key-confirm-pending', mayOwnKey: true });
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(0);

    await observePtt(true, 3);
    expect(controller.snapshot()).toMatchObject({ phase: 'active', txRisk: 'confirmed-on', mayOwnKey: true });

    window.dispatchEvent(new Event('pagehide'));

    expect(controller.snapshot()).toMatchObject({
      phase: 'releasing', mayOwnKey: true,
      pendingOff: expect.objectContaining({ leaseId: 'lease-pagehide' }),
    });
    expect(countFrames('ptt_off', socket)).toBe(1);
    expect(h.stop).toHaveBeenCalledTimes(1);

    // A second pagehide (browsers can fire it more than once, e.g. bfcache
    // probes) must coalesce, not send a duplicate OFF.
    window.dispatchEvent(new Event('pagehide'));
    expect(countFrames('ptt_off', socket)).toBe(1);

    await observePtt(false, 4);
    expect(controller.snapshot()).toMatchObject({
      phase: 'idle', fault: null, guard: null, mayOwnKey: false, pendingOff: null,
    });
    expect(h.restore).toHaveBeenCalledTimes(1);
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(1);
  });

  it('visibilitychange to hidden during an owned key releases the lease too — the production policy is fail-closed on any visibility loss, not gated to full page unload', async () => {
    const { controller, socket } = await mountConnectedApp();

    controller.start('probe', 'lease-visibility', 'momentary');
    await settle();
    await observePtt(true, 3);
    expect(controller.snapshot()).toMatchObject({ phase: 'active', mayOwnKey: true });

    // src/App.svelte's `onVisibilityLoss` calls the SAME release callback as
    // `onPageHide` whenever `document.visibilityState === 'hidden'` — this is
    // not this test inventing a policy, it is reading App.svelte's actual
    // `lifecycleReleaseSource` binding, which treats a hidden tab exactly like
    // a page unload for TX-safety purposes (no "stay keyed while backgrounded"
    // grace period).
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));

    expect(controller.snapshot()).toMatchObject({
      phase: 'releasing', mayOwnKey: true,
      pendingOff: expect.objectContaining({ leaseId: 'lease-visibility' }),
    });
    expect(countFrames('ptt_off', socket)).toBe(1);
    expect(h.stop).toHaveBeenCalledTimes(1);

    // A visibilitychange firing again while still hidden (some browsers repeat
    // it) must not re-release or double-send OFF.
    document.dispatchEvent(new Event('visibilitychange'));
    expect(countFrames('ptt_off', socket)).toBe(1);

    await observePtt(false, 4);
    expect(controller.snapshot()).toMatchObject({
      phase: 'idle', fault: null, guard: null, mayOwnKey: false, pendingOff: null,
    });
    expect(h.restore).toHaveBeenCalledTimes(1);
  });

  it('a visibilitychange to visible (not hidden) during an owned key is a no-op — release is gated on document.visibilityState === "hidden"', async () => {
    const { controller, socket } = await mountConnectedApp();

    controller.start('probe', 'lease-still-visible', 'momentary');
    await settle();
    await observePtt(true, 3);
    expect(controller.snapshot()).toMatchObject({ phase: 'active', mayOwnKey: true });

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));

    expect(controller.snapshot()).toMatchObject({ phase: 'active', mayOwnKey: true });
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(h.stop).not.toHaveBeenCalled();
    // Real timer/listener cleanup happens via `afterEach`'s unconditional
    // `unmount()` — this test deliberately leaves the lease owned.
  });

  it('pagehide with no owned lease is a no-op — no spurious OFF', async () => {
    const { controller, socket } = await mountConnectedApp();
    expect(controller.snapshot().phase).toBe('idle');

    window.dispatchEvent(new Event('pagehide'));

    expect(controller.snapshot().phase).toBe('idle');
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(countFrames('ptt_on', socket)).toBe(0);
    expect(h.stop).not.toHaveBeenCalled();
  });

  it('teardown control: unmounting detaches the pagehide/visibilitychange listeners — a pagehide after unmount is inert', async () => {
    const { component, controller } = await mountConnectedApp();
    unmount(component);

    // Must not throw and must not resurrect a disposed controller.
    expect(() => window.dispatchEvent(new Event('pagehide'))).not.toThrow();
    expect(() => controller.start('probe', 'lease-after-unmount', 'momentary')).not.toThrow();
    expect(controller.snapshot().phase).toBe('idle');
  });
});
