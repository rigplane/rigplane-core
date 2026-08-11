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
