import { describe, expect, it } from 'vitest';
import { deriveLinkFault } from '../link-fault';

describe('deriveLinkFault', () => {
  it('reports no fault when the WS is up and state updates are current', () => {
    expect(deriveLinkFault({ wsConnected: true, connectionStale: false })).toBe('none');
  });

  it('reports radio-silent when the WS is up but state updates have stalled', () => {
    expect(deriveLinkFault({ wsConnected: true, connectionStale: true })).toBe('radio-silent');
  });

  it('reports ws-down when the WS is down and staleness has not yet been observed', () => {
    expect(deriveLinkFault({ wsConnected: false, connectionStale: false })).toBe('ws-down');
  });

  it('reports ws-down when the WS is down and state updates are also stale', () => {
    expect(deriveLinkFault({ wsConnected: false, connectionStale: true })).toBe('ws-down');
  });
});
