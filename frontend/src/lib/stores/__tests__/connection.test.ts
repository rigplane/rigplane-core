import { describe, it, expect, beforeEach, vi } from 'vitest';

describe('connection store', () => {
  let store: typeof import('../connection.svelte');

  beforeEach(async () => {
    vi.resetModules();
    store = await import('../connection.svelte');
  });

  it('starts disconnected', () => {
    expect(store.getConnectionStatus()).toBe('disconnected');
    expect(store.isConnected()).toBe(false);
  });

  it('connected when ws connected', () => {
    store.setWsConnected(true);
    expect(store.getConnectionStatus()).toBe('connected');
    expect(store.isConnected()).toBe(true);
  });

  // MOR-1419: a real WS drop must be reflected immediately, not masked by a
  // stale secondary flag (the retired `httpConnected` orphan used to leave
  // this at 'partial' after disconnect).
  it('disconnected immediately when ws disconnects after being connected', () => {
    store.setWsConnected(true);
    store.setWsConnected(false);
    expect(store.getConnectionStatus()).toBe('disconnected');
    expect(store.isConnected()).toBe(false);
  });

  it('setLastResponseTime stores the value', () => {
    store.setLastResponseTime(1234567890);
    expect(store.getLastResponseTime()).toBe(1234567890);
  });

  it('lastResponseTime starts null', () => {
    expect(store.getLastResponseTime()).toBeNull();
  });

  it('getWsConnected reflects its state', () => {
    expect(store.getWsConnected()).toBe(false);
    store.setWsConnected(true);
    expect(store.getWsConnected()).toBe(true);
  });

  it('tracks reconnecting flag explicitly', () => {
    store.setReconnecting(true);
    expect(store.isReconnecting()).toBe(true);
    store.setReconnecting(false);
    expect(store.isReconnecting()).toBe(false);
  });

  it('markStateUpdated clears stale state', () => {
    store.markStateUpdated();
    expect(store.isStale()).toBe(false);
  });
});

// MOR-1526: the "Radio ↔ Server" chip's steady state must come from live
// per-field facts (rigConnected/radioReady/radioHealth — synced on every
// state_update, same as the MOR-1419 httpState derivation above), never
// from the reconnect-edge `connection_status` event alone. That event's
// ONLY writer is `radioStatus` (default 'disconnected'), which never fires
// on a healthy session with no reconnects — the exact bug this derivation
// fixes.
describe('radio-link chip steady state (MOR-1526)', () => {
  let store: typeof import('../connection.svelte');

  beforeEach(async () => {
    vi.resetModules();
    store = await import('../connection.svelte');
  });

  it('BUG REPRO: fresh session, no reconnect events, live facts connected -> green', () => {
    store.setWsConnected(true);
    store.setRigConnected(true);
    store.setRadioReady(true);
    store.setRadioHealth({
      serverReachable: true,
      radioLink: 'connected',
      readiness: 'ready',
      likelyCause: 'unknown',
      sinceMs: 0,
      lastError: null,
    });

    expect(store.getRadioLinkState()).toBe('connected');
  });

  it('fails closed on genuine link-down facts (MOR-1440 radioLink != connected)', () => {
    store.setWsConnected(true);
    store.setRigConnected(true);
    store.setRadioReady(true);
    store.setRadioHealth({
      serverReachable: true,
      radioLink: 'disconnected',
      readiness: 'stalled',
      likelyCause: 'radio_network_lost',
      sinceMs: 9000,
      lastError: null,
    });

    expect(store.getRadioLinkState()).toBe('disconnected');
  });

  it('an active reconnect event overlays the steady state while in flight', () => {
    // Live facts still look "connected" (stale, pre-drop) but a reconnect
    // is actively underway — the event stream is the only source that
    // knows that, and it must win.
    store.setWsConnected(true);
    store.setRigConnected(true);
    store.setRadioReady(true);
    store.setRadioStatus('reconnecting');

    expect(store.getRadioLinkState()).toBe('reconnecting');
  });

  it('cold start: no events and no facts observed yet -> fail-closed disconnected', () => {
    expect(store.getRadioLinkState()).toBe('disconnected');
  });

  it('a stale "connected" reconnect-status event does not survive a real ws drop', () => {
    // Regression guard: rigConnected/radioReady are only ever refreshed by
    // state_update messages, so they go stale (not reset) the instant the
    // WS drops. The steady state must not read them as current truth once
    // the transport itself is down.
    store.setWsConnected(true);
    store.setRigConnected(true);
    store.setRadioReady(true);
    store.setRadioStatus('connected');
    expect(store.getRadioLinkState()).toBe('connected');

    store.setWsConnected(false);
    expect(store.getRadioLinkState()).toBe('disconnected');
  });
});
