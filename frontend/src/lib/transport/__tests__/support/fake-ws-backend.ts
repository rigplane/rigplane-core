// ─── Shared fake WebSocket backend for ws-client tests ───────────────────────
//
// Used by both the unit suite (``ws-client.isolated.test.ts``, which mocks the store
// modules) and the integration suite (``ws-client-store.integration.test.ts``,
// which drives the real store). Previously each file kept its own copy of
// this class; this module is the single source of truth (superset of both
// copies' behavior — includes ``simulateClose``/``simulateError``, which the
// integration suite's copy didn't need but the unit suite does).
//
// ``instances`` is exported alongside the class because both suites index
// into it directly (e.g. ``instances[0].simulateOpen()``) after installing
// ``MockWebSocket`` as ``globalThis.WebSocket``.

export class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readyState = MockWebSocket.CONNECTING;
  binaryType = 'blob';
  url: string;
  sent: string[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  // Test helpers
  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  simulateMessage(data: string | ArrayBuffer) {
    this.onmessage?.({ data } as MessageEvent);
  }

  simulateClose() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  simulateError() {
    this.onerror?.();
  }
}

export const instances: MockWebSocket[] = [];
