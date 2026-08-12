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

  // ── Round 1 (verifier review): F1/F2/F4 findings ──────────────────────

  it('F1: a reconnect overlay stuck mid-flight (no terminal event) is cleared by the disconnect reset, not left stuck across a ws down/up cycle', () => {
    // A reconnect starts but never resolves with a terminal event — e.g.
    // a server restart, or reconnect_loop hitting asyncio.CancelledError on
    // either exit path (radio_reconnect.py:207) — so `radioStatus` never
    // advances past 'reconnecting' on its own.
    store.setRadioStatus('reconnecting');
    expect(store.getRadioLinkState()).toBe('reconnecting');

    // ws-client's onStateChange 'disconnected' branch (the F1 delta) resets
    // `radioStatus` unconditionally, regardless of whether a terminal
    // connection_status event ever arrived. (R2 ruling: rigConnected/
    // radioReady/radioHealth are deliberately NOT reset here — see F2
    // below and the R2 comment in ws-client.ts — resetting them would be
    // visible to isLiveRadioAvailable()/sendCommand, a command-gate change
    // this display fix must not make.)
    store.setRadioStatus('disconnected');
    store.setWsConnected(false);
    expect(store.getRadioLinkState()).toBe('disconnected');

    // Reconnect to a healthy server that — per the ticket's own premise —
    // never emits connection_status on a session with no further
    // reconnects. Without the reset above, the overlay would still be
    // 'reconnecting' and would suppress these live facts forever.
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

  it('F2: the up-edge (wsConnected flips true again) does not paint green from stale pre-drop facts', () => {
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

    // ws drops. R2 ruling: rigConnected/radioReady/radioHealth are NOT
    // reset (unlike an earlier revision of this fix) — only wsConnected
    // flips, which is what real transport-level onclose observes. The
    // steady-state formula's `wsConnected &&` gate on both the 'connected'
    // and 'degraded' branches is what actually produces 'disconnected'
    // here, not a fact reset.
    store.setWsConnected(false);
    expect(store.getRadioLinkState()).toBe('disconnected');

    // Up-edge: the transport reconnects (setWsConnected(true) fires
    // synchronously at ws-client.ts:550) before any fresh state_update has
    // refreshed rigConnected/radioReady/radioHealth on THIS session.
    // `setWsConnected(false)` above cleared `factsObservedThisSession`, and
    // nothing has set it back to true yet — so even though rigConnected/
    // radioReady/radioHealth still carry the pre-drop true/true/connected
    // values (stale, never reset), the chip must not read them as current
    // truth. It reports 'degraded' (link up, facts not yet reconfirmed on
    // this session) — NEVER 'connected'. That's the assertion this test
    // exists to pin.
    store.setWsConnected(true);
    expect(store.getRadioLinkState()).not.toBe('connected');
    expect(store.getRadioLinkState()).toBe('degraded');

    // And the command gate is untouched by any of this: rigConnected/
    // radioReady/radioHealth were never reset, so isLiveRadioAvailable()
    // (which sendCommand() gates on) still reads the session as available.
    // The chip fix must not move the command gate — this is the pin for
    // that isolation.
    expect(store.isLiveRadioAvailable()).toBe(true);
  });

  it('F4: emits degraded (MOR-620 vocabulary), not connected or disconnected, when the link is up but rigConnected/radioReady have not both caught up', () => {
    store.setWsConnected(true);
    store.setRigConnected(true);
    store.setRadioReady(false);
    store.setRadioHealth({
      serverReachable: true,
      radioLink: 'connected',
      readiness: 'stalled',
      likelyCause: 'unknown',
      sinceMs: 500,
      lastError: null,
    });

    expect(store.getRadioLinkState()).toBe('degraded');
  });
});
