import { beforeEach, describe, expect, it, vi } from 'vitest';
import { deriveLinkFault, getLinkFault } from '../link-fault';

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

describe('deriveLinkFault', () => {
  it('reports no fault when the WS is up and state updates are current', () => {
    expect(deriveLinkFault({
      everConnected: true, wsConnected: true, connectionStale: false,
    })).toBe('none');
  });

  it('reports radio-silent when the WS is up but state updates have stalled', () => {
    expect(deriveLinkFault({
      everConnected: true, wsConnected: true, connectionStale: true,
    })).toBe('radio-silent');
  });

  it('reports ws-down when the WS is down and staleness has not yet been observed', () => {
    expect(deriveLinkFault({
      everConnected: true, wsConnected: false, connectionStale: false,
    })).toBe('ws-down');
  });

  it('reports ws-down when the WS is down and state updates are also stale', () => {
    expect(deriveLinkFault({
      everConnected: true, wsConnected: false, connectionStale: true,
    })).toBe('ws-down');
  });

  // Before the first connect `wsConnected` is false for that reason alone;
  // without this arm every page load would paint the face veiled until the
  // WS opened.
  it.each([
    ['ws down, not stale', false, false],
    ['ws down and stale', false, true],
    ['ws up, not stale', true, false],
    ['ws up and stale', true, true],
  ])('reports no fault before the first connect (%s)', (_label, wsConnected, connectionStale) => {
    expect(deriveLinkFault({
      everConnected: false, wsConnected, connectionStale,
    })).toBe('none');
  });
});

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
