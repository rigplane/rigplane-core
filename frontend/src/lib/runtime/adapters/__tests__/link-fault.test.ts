import { describe, expect, it } from 'vitest';
import { deriveLinkFault } from '../link-fault';

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
