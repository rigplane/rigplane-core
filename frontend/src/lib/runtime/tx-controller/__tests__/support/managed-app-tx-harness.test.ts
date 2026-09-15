import { describe, expect, it } from 'vitest';
import { ManagedAppTxHarness } from './managed-app-tx-harness';

describe('ManagedAppTxHarness', () => {
  it('records the four canonical actions without changing its server projection', () => {
    const harness = new ManagedAppTxHarness({ intent: 'ptt', observedPtt: 'on' });
    const snapshot = harness.controller.snapshot();
    const before = JSON.stringify(harness.controller.snapshot());
    let notifications = 0;
    const off = harness.controller.subscribe(() => { notifications += 1; });

    harness.controller.pttOn();
    harness.controller.pttOff();
    harness.controller.transmitOn();
    harness.controller.forceOff();
    void harness.controller.setTot(240);

    expect(JSON.stringify(harness.controller.snapshot())).toBe(before);
    expect(harness.controller.snapshot()).toBe(snapshot);
    expect(notifications).toBe(0);
    expect(harness.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'ws', operation: 'ptt_off' },
      { transport: 'http', operation: 'transmit_on' },
      { transport: 'http', operation: 'force_off' },
      { transport: 'http', operation: 'set_tot', configuredSeconds: 240 },
    ]);
    off();
  });

  it('projects RX, PTT, TRANSMIT, errors, and stale snapshots through production projection', () => {
    const harness = new ManagedAppTxHarness();

    expect(harness.emitServerSnapshot({ intent: 'rx', observedPtt: 'off' })).toMatchObject({
      phase: 'idle', intent: null, radioTx: 'off', fresh: true, configuredSeconds: null,
    });
    expect(harness.emitServerSnapshot({ intent: 'ptt', observedPtt: 'on', remainingMs: 2500 })).toMatchObject({
      phase: 'active', intent: 'momentary', radioTx: 'on', remainingMs: 2500,
    });
    expect(harness.emitServerSnapshot({ intent: 'transmit', observedPtt: 'unknown' })).toMatchObject({
      phase: 'key-confirm-pending', intent: 'latched', radioTx: 'unknown', txRisk: 'uncertain',
    });
    expect(harness.emitServerSnapshot({ intent: 'rx', lastError: 'rejected' })).toMatchObject({
      phase: 'failed', fault: 'rejected', fresh: true,
    });
    expect(harness.emitStale()).toMatchObject({
      phase: 'idle', intent: null, radioTx: 'unknown', fresh: false,
    });
  });

  it('notifies once per server snapshot and protects reset from leaked listeners', () => {
    const harness = new ManagedAppTxHarness();
    let received = 0;
    const off = harness.controller.subscribe(() => { received += 1; });

    harness.emitServerSnapshot({ intent: 'ptt', observedPtt: 'on' });
    expect(received).toBe(1);
    expect(harness.listenerCount()).toBe(1);
    expect(() => harness.reset()).toThrow('zero listeners');
    off();
    off();
    expect(harness.listenerCount()).toBe(0);
    harness.reset();
    expect(harness.trace()).toEqual([]);
  });

  it('keeps a frozen controller identity with only the canonical actions', () => {
    const harness = new ManagedAppTxHarness();
    expect(harness.controller).toBe(harness.controller);
    expect(Object.isFrozen(harness.controller)).toBe(true);

    expect(Object.keys(harness.controller).sort()).toEqual([
      'forceOff', 'pttOff', 'pttOn', 'setTot', 'snapshot', 'subscribe', 'transmitOn',
    ]);
  });
});
