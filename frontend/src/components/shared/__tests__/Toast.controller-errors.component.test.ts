/**
 * MOR-4250 — controller error notifications render from the i18n catalogs.
 *
 * Customer bug: the English UI showed "Backend error: Error: Удалённое
 * управление потеряно…". Every changed producer is driven here through its
 * real code path — no `ws-client` mock and no hand-built notification
 * payload:
 *
 *   - `App.svelte` bootstrap refusal: a real mounted App runs the real
 *     `runtime.bootstrap()` → `controllerClient.prepare()` against wire
 *     replies copied from the real Python handler, and the banner renders
 *     `messageFromReasonCode(controllerToastCodeOf(err))` with no
 *     `Backend error: Error:` double prefix. Reverting the App change
 *     restores the double prefix and fails these tests.
 *   - `system-controller.ts` reconnect refusal: the real singleton's
 *     public `connect()` is called after a remote refusal, and its catch
 *     emits `CONTROLLER_TOAST_CODES[failure.code]`. Reverting to the
 *     snake_case wire code resolves `core.toast.unknown` and fails.
 *   - `ws-client.ts` controller loss: the control channel's real
 *     `controllerClient.onLoss` callback fires after the REAL server
 *     eviction frame — `{"type":"controller_heartbeat","generation":N}` is
 *     the reply `ControllerAuthority.heartbeat()` sends once the grant's
 *     generation is superseded — routed through the real message handler
 *     to `controllerClient.acknowledge()` → `lose()`. Reverting the toast
 *     code conversion resolves `core.toast.unknown` and fails.
 *
 * Wire replies mirror `server.py: _handle_http_controller` and
 * `controller_authority.py: status/acquire` exactly: GET returns the
 * status document, POST 201 the grant (`ttl_ms` 6000, `heartbeat_ms`
 * 2000), and a refusal is `ControllerError`'s JSON body
 * (`{"error": "<code>"}`, HTTP 409 for `controller_busy`).
 *
 * Presentation seams (skins, layouts, media session, audio manager) are
 * stubbed exactly like `src/__tests__/first-browser-auth.component.test.ts`
 * so a real App can mount in jsdom; `AppGlobalHost` is stubbed to the real
 * `Toast.svelte`, so the Toast surface sits inside the mounted App where
 * production mounts it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import stateFixture from '$lib/runtime/adapters/__tests__/fixtures/ic7300-state.json';
import capsFixture from '$lib/runtime/adapters/__tests__/fixtures/ic7300-capabilities.json';

vi.mock('../../../skins/registry', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../skins/registry')>(),
  loadSkin: async () => (await import('../../../__tests__/LayoutStub.svelte')).default,
  presentationHostMode: () => 'self-contained',
  presentationResourcePlan: () => [],
}));
// The real Toast surface, mounted where App mounts its global host —
// the changed notification path ends in this component's DOM.
vi.mock('../../../AppGlobalHost.svelte', async () => ({
  default: (await import('../Toast.svelte')).default,
}));
vi.mock('../../../lib/local-extensions/LocalExtensionsHost.svelte', async () => ({
  default: (await import('../../../__tests__/LayoutStub.svelte')).default,
}));
vi.mock('../../../components-v2/wiring/SemanticRadioSurfaces.svelte', async () => ({
  default: (await import('../../../__tests__/LayoutStub.svelte')).default,
}));
vi.mock('../../../lib/runtime/tx-controller/managed-app-host', () => ({
  provideManagedAppTxHost: () => ({ refreshAuthority() {}, dispose() {}, release() {} }),
}));
vi.mock('../../../lib/media/media-session', () => ({ initMediaSession() {}, destroyMediaSession() {} }));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    onChange: () => () => {}, onTxAudioDied: () => () => {}, rxEnabled: false,
    setOperatorNotifier: vi.fn(), setControllerBinding: vi.fn(), destroy: vi.fn(),
  },
}));

import App from '../../../App.svelte';
import Toast from '../Toast.svelte';
import { setLocale } from '$lib/i18n';
import { _resetLocale } from '$lib/i18n/store.svelte';
import {
  CONTROLLER_TOAST_CODES,
  type ControllerWireCode,
} from '$lib/controller-toast-codes';
import { ControllerClientError } from '$lib/transport/controller-client';
import { systemController } from '$lib/runtime/system-controller';
import { disconnectAll, emitLocalNotification } from '$lib/transport/ws-client';

const CONTROLLER_BUSY_EN = 'Remote control is held by another client. Connect once it is released.';
const CONTROLLER_BUSY_RU = 'Управление занято другим клиентом. Подключитесь после освобождения.';
const CONTROLLER_LOST_EN = 'Remote control was lost. Reconnect.';
const CONTROLLER_LOST_RU = 'Удалённое управление потеряно. Подключитесь заново.';
const UNKNOWN = 'Something went wrong. Try again later.';

// Same jsdom Web Animations stub as Toast.link-loss.component.test.ts:
// Svelte's `fly` transition needs `element.animate(...)` with an
// `onfinish` completion.
if (typeof (Element.prototype as any).animate !== 'function') {
  (Element.prototype as any).animate = function animate(
    _keyframes: unknown,
    options?: number | { duration?: number },
  ): Animation {
    const duration = typeof options === 'number' ? options : (options?.duration ?? 0);
    const fake = {
      playState: 'running' as AnimationPlayState,
      currentTime: 0,
      onfinish: null as (() => void) | null,
      oncancel: null as (() => void) | null,
      cancel() {
        fake.playState = 'idle';
      },
      finish() {
        fake.playState = 'finished';
        fake.onfinish?.();
      },
      addEventListener() {},
      removeEventListener() {},
    };
    setTimeout(() => {
      if (fake.playState === 'running') {
        fake.playState = 'finished';
        fake.onfinish?.();
      }
    }, duration);
    return fake as unknown as Animation;
  };
}

// ── Wire replies, copied from the real Python handler ──────────────
// `controller_authority.py: status()` / `acquire()` and `server.py:
// _handle_http_controller` (a refusal is `{"error": code}` with
// ControllerError's HTTP status — 409 for controller_busy).

const GRANT_GENERATION = 7;
const controllerStatus = () => ({
  protocol_version: 1,
  mode: 'remote',
  state: 'held',
  generation: GRANT_GENERATION,
  ttl_ms: 6000,
  heartbeat_ms: 2000,
  remaining_ms: 0,
});
const controllerGrant = () => ({
  controller_key: '1f'.repeat(32),
  generation: GRANT_GENERATION,
  ttl_ms: 6000,
  heartbeat_ms: 2000,
});

/** What POST /api/v1/controller answers in the current test. */
let controllerPost: 'refuse-busy' | 'grant' = 'refuse-busy';

const fetchMock = vi.fn();
const caps = { ...capsFixture, stateContractVersion: 1, providerGeneration: 0 };

function response(status: number, body: unknown = {}) {
  return { status, ok: status === 200, json: async () => body };
}

let app: ReturnType<typeof mount> | null = null;
let toastHost: HTMLDivElement | null = null;
let toastApp: ReturnType<typeof mount> | null = null;

async function settle() {
  // Microtask drain for the async chains, then real event-loop turns: the
  // first bootstrap's refusal path settles a one-time deferred step on a
  // macrotask (a pure-microtask drain leaves the banner unset), and the
  // 15ms rounds stay far below App's 3s retry-reload timer.
  for (let i = 0; i < 25; i++) { await Promise.resolve(); flushSync(); }
  for (let i = 0; i < 6; i++) {
    await new Promise((resolve) => setTimeout(resolve, 15));
    for (let j = 0; j < 10; j++) { await Promise.resolve(); flushSync(); }
  }
}

function startApp() {
  app = mount(App, { target: document.body });
  flushSync();
}

/** Mount the real Toast standalone (surfaces without a full App). */
function startToast() {
  toastHost = document.createElement('div');
  document.body.appendChild(toastHost);
  toastApp = mount(Toast, { target: toastHost });
  flushSync();
}

function errorMessages(): string[] {
  return Array.from(document.querySelectorAll('.toast.error .toast-msg')).map(
    (el) => el.textContent?.trim() ?? '',
  );
}

function bannerText(): string {
  return document.querySelector('.error-overlay .error-msg')?.textContent?.trim() ?? '';
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  document.body.innerHTML = '';
  instances.length = 0;
  controllerPost = 'refuse-busy';
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.stubGlobal('fetch', fetchMock);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  _resetLocale();
  setLocale('en-US');
  fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
    expect(url.startsWith('/api/v1/')).toBe(true);
    expect(init.redirect).toBe('error');
    if (url === '/api/v1/controller') {
      if (init.method === 'POST') {
        return controllerPost === 'grant'
          ? response(201, controllerGrant())
          : response(409, { error: 'controller_busy' });
      }
      if (init.method === 'DELETE') return response(200, controllerStatus());
      return response(200, controllerStatus());
    }
    return response(200, url.endsWith('/capabilities') ? caps : {});
  });
});

afterEach(async () => {
  if (app) { await unmount(app); app = null; }
  if (toastApp) { await unmount(toastApp); toastApp = null; }
  toastHost?.remove();
  toastHost = null;
  disconnectAll();
  await settle();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  _resetLocale();
});

describe('App bootstrap refusal — MOR-4250 controller reason codes', () => {
  it('renders the localized catalog text, not the Backend error: Error: double prefix', async () => {
    startApp();
    await settle();

    expect(document.querySelector('.error-overlay[role="alert"]')).not.toBeNull();
    const banner = bannerText();
    expect(banner).toBe(CONTROLLER_BUSY_EN);
    // The customer bug: the old path rendered
    // `Backend error: {detail}` with `String(err)` — a double prefix
    // wrapping the raw error text.
    expect(banner).not.toContain('Backend error:');
    expect(banner).not.toContain('Error:');
    expect(banner).not.toMatch(/[\u0400-\u04FF]/);
  });

  it('renders the ru-RU catalog text when the locale is ru-RU', async () => {
    setLocale('ru-RU');
    startApp();
    await settle();

    // A hardcoded English fallback would fail here: the banner comes from
    // the ru-RU catalog entry for the controllerBusy toast code.
    expect(bannerText()).toBe(CONTROLLER_BUSY_RU);
    expect(bannerText()).not.toContain('Backend error:');
  });
});

describe('system-controller reconnect refusal — real producer path', () => {
  it('emits the localized toast from the real connect() catch', async () => {
    startToast();
    // The public production entry: a remote reconnect whose prepare() is
    // refused by the station (real wire refusal, real controller client).
    systemController.connect();
    await settle();

    expect(errorMessages()).toEqual([CONTROLLER_BUSY_EN]);
    expect(errorMessages()).not.toContain(UNKNOWN);

    // renderToast re-resolves catalog text on locale change; a fallback
    // to the English `message` field would freeze the en text.
    setLocale('ru-RU');
    flushSync();
    expect(errorMessages()).toEqual([CONTROLLER_BUSY_RU]);
  });
});

describe('controller loss through the real producers', () => {
  it('renders the localized toast from ws-client and frontend-runtime', async () => {
    controllerPost = 'grant';
    startApp();
    await settle();

    // Bootstrap succeeded: no error overlay, and the real Toast surface is
    // mounted inside the App tree via AppGlobalHost.
    expect(document.querySelector('.error-overlay')).toBeNull();
    expect(document.querySelector('.toast-container')).not.toBeNull();

    const socket = instances[0];
    expect(socket).toBeDefined();
    socket.simulateOpen();
    const state = { ...stateFixture, stateContractVersion: 1, providerGeneration: 0 };
    socket.simulateMessage(JSON.stringify({
      type: 'state_update',
      data: { type: 'full', data: state, stateContractVersion: 1, providerGeneration: 0 },
    }));
    await settle();

    // Production case for frontend-runtime's notification: the operator is
    // mid-transmission when control is lost, so the automatic teardown's
    // pre-disconnect barrier (a TX power-off) fails. Registered through the
    // real systemController API; the disconnect then rejects for real and
    // bootstrap's onLoss catch emits its own localized controller-lost
    // notice.
    systemController.registerPreDisconnectBarrier(
      async () => { throw new Error('TX power-off refused'); },
    );

    // The real server reply once another client took over: the grant's
    // generation is superseded, so the heartbeat ack carries the new one.
    // The real control-channel handler routes it to
    // controllerClient.acknowledge() → lose() → the registered onLoss
    // callbacks → emitLocalNotification with the converted toast code.
    socket.simulateMessage(JSON.stringify({
      type: 'controller_heartbeat',
      generation: GRANT_GENERATION + 1,
    }));
    await settle();

    // One localized toast per producer: ws-client's onLoss and
    // frontend-runtime's teardown catch. Reverting either conversion makes
    // its toast resolve `core.toast.unknown` and fails this assertion.
    expect(errorMessages()).toEqual([CONTROLLER_LOST_EN, CONTROLLER_LOST_EN]);
    expect(errorMessages()).not.toContain(UNKNOWN);

    setLocale('ru-RU');
    flushSync();
    expect(errorMessages()).toEqual([CONTROLLER_LOST_RU, CONTROLLER_LOST_RU]);
  });
});

describe('every controller wire code through the real notification bus', () => {
  it('renders each CONTROLLER_TOAST_CODES entry, never the fallback or unknown', () => {
    startToast();
    const EXPECTED_EN: Record<string, string> = {
      controllerBusy: CONTROLLER_BUSY_EN,
      controllerInvalid: 'The station denied access to remote control.',
      controllerLost: CONTROLLER_LOST_EN,
      controllerNotReady: 'The station is not ready for remote control yet.',
      controllerUnsupported: 'The station does not support this control protocol.',
      controllerLocalRequired: 'This action is only available on the local host.',
      remoteDisabled: 'Remote control is disabled on this host.',
    };
    for (const [wire, code] of Object.entries(CONTROLLER_TOAST_CODES)) {
      // Producer-typed: a real ControllerClientError carries the wire code
      // and its English-stable message; the bus is the real one every
      // producer uses.
      emitLocalNotification(
        'error',
        new ControllerClientError(wire as ControllerWireCode).message,
        code,
      );
      flushSync();
      const rendered = errorMessages().at(-1) ?? '';
      expect(rendered, `wire code ${wire}`).toBe(EXPECTED_EN[code]);
      expect(rendered, `wire code ${wire}`).not.toBe(UNKNOWN);
    }
  });
});
