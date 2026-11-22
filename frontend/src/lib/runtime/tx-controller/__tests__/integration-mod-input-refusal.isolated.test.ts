import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

// MOR-4660: USB TX MOD-input refusal delivery and retry, browser side.
//
// Shared producer replies (the owner's shared-replies rule): the refusal
// documents replayed below are the exact documents the backend producers
// emit from the same `TxModInputRefusal("WLAN").as_wire()` — the HTTP 409
// body pinned through the real managed-transmit handler by
// `tests/test_usb_tx_mod_input.py::test_web_queue_refusal_and_retry`, and
// the WS `response` frame pinned through the real command ingress by
// `tests/test_usb_tx_mod_input.py::test_ws_ingress_refusal_reply_matches_http`.
//
// Real: the REAL `WsChannel` singleton in `$lib/transport/ws-client`
// (driven only at the socket boundary by the shared MockWebSocket fake and
// observed through `onMessage`), the REAL managed-transmit store, the REAL
// ManagedTransmitClient, the REAL `createManagedBrowserDependencies()`
// factory and a REAL ManagedTxController. Mocked: only the media seam
// (`tx-adapter`) and the network boundary (`fetch`).

const HTTP_REFUSAL = {
  error: 'tx_mod_input',
  message: "The radio takes its microphone from WLAN. Set the radio's MOD input to USB.",
  source: 'WLAN',
} as const;
const WS_REFUSAL_RESPONSE = {
  type: 'response', id: 'voice-1', ok: false, ...HTTP_REFUSAL,
} as const;
const RU_REFUSAL = 'Трансивер получает звук для передачи со входа WLAN. Установите MOD input трансивера в USB.';

const snapshotRx = {
  schemaVersion: 1, sampledAt: '2026-09-04T00:00:00.000Z',
  managedTransmit: {
    status: 'available', intent: { kind: 'rx' }, releaseRequired: false, lastError: null,
    lastActuation: null, abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
  },
  txObservation: { observedPtt: 'off' },
};
const snapshotTx = {
  ...snapshotRx, sampledAt: '2026-09-04T00:00:01.000Z',
  managedTransmit: { ...snapshotRx.managedTransmit, intent: { kind: 'transmit' } },
};

const h = vi.hoisted(() => ({
  postCount: 0,
  fetchMock: null as ReturnType<typeof vi.fn> | null,
}));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({
  getTxAudioControl: () => ({
    startManagedTx: async (): Promise<string | null> => null,
    stopLocalAudio: () => {},
    onTxAudioDied: () => () => {},
  }),
}));

type WsClientModule = typeof import('$lib/transport/ws-client');
type StoreModule = typeof import('$lib/stores/managed-transmit.svelte');
type BrowserDepsModule = typeof import('../browser-dependencies');
type ControllerModule = typeof import('../managed-controller');
type I18nModule = typeof import('$lib/i18n');

const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

async function loadStack() {
  const wsClient = (await import('$lib/transport/ws-client')) as WsClientModule;
  const store = (await import('$lib/stores/managed-transmit.svelte')) as StoreModule;
  const browserDeps = (await import('../browser-dependencies')) as BrowserDepsModule;
  const controllerModule = (await import('../managed-controller')) as ControllerModule;
  const i18n = (await import('$lib/i18n')) as I18nModule;
  return { wsClient, store, browserDeps, controllerModule, i18n };
}

describe('MOR-4660 MOD-input refusal — shared replies through the real browser stack', () => {
  let originalWebSocket: typeof WebSocket;
  let originalFetch: typeof fetch;

  beforeEach(() => {
    instances.length = 0;
    h.postCount = 0;
    originalWebSocket = globalThis.WebSocket;
    originalFetch = globalThis.fetch;
    // @ts-expect-error install the mock as the global WebSocket constructor
    globalThis.WebSocket = MockWebSocket;
    vi.resetModules();
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    });
    h.fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        h.postCount += 1;
        return h.postCount === 1
          ? new Response(JSON.stringify(HTTP_REFUSAL), { status: 409 })
          : new Response('', { status: 202 });
      }
      if (String(url).startsWith('/api/v1/managed-transmit')) {
        return new Response(JSON.stringify(h.postCount >= 2 ? snapshotTx : snapshotRx), { status: 200 });
      }
      return new Response('{}', { status: 404 });
    });
    vi.stubGlobal('fetch', h.fetchMock);
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    globalThis.fetch = originalFetch;
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('replays the shared WS refusal response through the real channel as a localized notification', async () => {
    const { wsClient, i18n } = await loadStack();
    i18n.setLocale('ru-RU');
    const notes: Record<string, unknown>[] = [];
    wsClient.onMessage((message) => {
      const note = message as unknown as Record<string, unknown>;
      if (note.type === 'notification') notes.push(note);
    });
    wsClient.connect('ws://test/api/v1/ws');
    const socket = instances[0];
    socket.simulateOpen();

    socket.simulateMessage(JSON.stringify(WS_REFUSAL_RESPONSE));

    // The channel localized the producer's document: without the
    // `usbTxModInputMessage(raw)` branch the toast would carry the
    // producer's English `message` verbatim.
    expect(notes).toEqual([
      expect.objectContaining({ level: 'error', category: 'command', message: RU_REFUSAL }),
    ]);
    wsClient.disconnectAll();
  });

  it('delivers the localized latch refusal and admits the retry after the source correction', async () => {
    const { wsClient, store, browserDeps, controllerModule, i18n } = await loadStack();
    i18n.setLocale('ru-RU');
    const notes: Record<string, unknown>[] = [];
    wsClient.onMessage((message) => {
      const note = message as unknown as Record<string, unknown>;
      if (note.type === 'notification') notes.push(note);
    });
    const factory = browserDeps.createManagedBrowserDependencies();
    const controller = new controllerModule.ManagedTxController(factory.dependencies);
    await controller.refresh();
    expect(store.managedTransmitIsStale()).toBe(false);

    // First latch attempt: the radio's MOD input is MIC, so the admission
    // refusal (HTTP 409, shared document) comes back.
    controller.transmitOn();
    await flush();
    expect(h.postCount).toBe(1);
    // The refusal surfaces through the real onLatchRefusal branch and the
    // typed two-argument emitLocalNotification: the already-localized
    // reason is displayed verbatim (no `code` to re-translate).
    expect(notes).toEqual([
      expect.objectContaining({ level: 'error', category: 'command', message: RU_REFUSAL }),
    ]);
    expect(notes[0]).not.toHaveProperty('code');
    // The refusal changed nothing server-side and emits no authority event:
    // the controller re-read the canonical snapshot after invalidating.
    await flush();
    expect(store.managedTransmitIsStale()).toBe(false);

    // The operator sets the radio's MOD input to USB and latches again —
    // no reconnect, no unrelated event. A stale projection would make
    // transmitOn() return before any request.
    controller.transmitOn();
    await flush();
    expect(h.postCount).toBe(2);
    expect(controller.snapshot()).toMatchObject({ intent: 'latched', fresh: true });
    factory.dispose();
  });
});
