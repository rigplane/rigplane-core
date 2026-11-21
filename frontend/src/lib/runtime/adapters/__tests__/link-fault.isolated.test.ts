/**
 * MOR-2241 — `getLinkFault()` reads the live veil state for skins, which
 * must not import `$lib/stores/*` directly. This file mocks that shared
 * store, so it runs in the `isolated` project: under the `fast` project's
 * `isolate: false` the mock would leak into every other file on the worker
 * (see `src/__tests__/fast-pool-leak-guard.test.ts`). The pure
 * `deriveLinkFault` cases stay in the fast `link-fault.test.ts`.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getLinkFault } from '../link-fault';

const connectionState = vi.hoisted(() => ({
  everConnected: false,
  wsConnected: false,
  stale: false,
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getWsConnected: () => connectionState.wsConnected,
  hasEverConnected: () => connectionState.everConnected,
  isStale: () => connectionState.stale,
}));

describe('getLinkFault', () => {
  beforeEach(() => {
    connectionState.everConnected = false;
    connectionState.wsConnected = false;
    connectionState.stale = false;
  });

  it('reads no fault before the first connect', () => {
    connectionState.wsConnected = false;
    connectionState.stale = true;
    expect(getLinkFault()).toBe('none');
  });

  it('reads ws-down once connected and the transport drops', () => {
    connectionState.everConnected = true;
    connectionState.wsConnected = false;
    connectionState.stale = true;
    expect(getLinkFault()).toBe('ws-down');
  });

  it('reads radio-silent when the transport is up but updates stalled', () => {
    connectionState.everConnected = true;
    connectionState.wsConnected = true;
    connectionState.stale = true;
    expect(getLinkFault()).toBe('radio-silent');
  });

  it('reads no fault when the transport is up and updates are current', () => {
    connectionState.everConnected = true;
    connectionState.wsConnected = true;
    connectionState.stale = false;
    expect(getLinkFault()).toBe('none');
  });
});
