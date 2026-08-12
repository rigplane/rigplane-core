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
  onclose: ((e: { code: number; reason: string; wasClean: boolean }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  // Mirrors the real WebSocket#close(code?, reason?) signature — a locally
  // initiated close (no server frame observed) is conventionally "clean".
  close(code?: number, reason?: string) {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code: code ?? 1005, reason: reason ?? '', wasClean: true });
  }

  // Test helpers
  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  simulateMessage(data: string | ArrayBuffer) {
    this.onmessage?.({ data } as MessageEvent);
  }

  simulateClose(code = 1006, reason = '', wasClean = false) {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code, reason, wasClean });
  }

  simulateError() {
    this.onerror?.();
  }
}

export const instances: MockWebSocket[] = [];
