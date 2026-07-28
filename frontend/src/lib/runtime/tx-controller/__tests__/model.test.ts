import { describe, expect, it } from 'vitest';
import { initialTxState, transition, type Eligibility, type PttObservation, type TxEvent, type TxState } from '../model';
const marker = (seq: number, authorityEpoch = 1) => ({ authorityEpoch, pttObservationSeq: seq, pttLastObservedMonotonic: seq });
const target = { receiver: 'MAIN', slot: 'A', frequencyHz: 14_074_000 } as const;
const eligible: Eligibility = { catPtt: true, browserTxAudio: true, controlLive: true, permit: 'allowed', target };
const ptt = (value: boolean, seq: number, epoch = 1): PttObservation => ({ value, observed: true, fresh: true, source: 'radio-readback', marker: marker(seq, epoch) });
const start = (state = initialTxState(1, marker(1)), overrides = {}) => transition(state, { type: 'start', sourceId: 'desktop', leaseId: 'lease', intent: 'momentary', eligibility: eligible, ptt: ptt(false, 2), ...overrides });
const types = (result: ReturnType<typeof transition>) => result.effects.map((effect) => effect.type);
function active(): TxState {
  let result = start(); const guard = result.state.guard!; result = transition(result.state, { type: 'intent', sourceId: 'desktop', guard, intent: 'latched' }); expect(result.state.intent).toBe('latched');
  result = transition(result.state, { type: 'audio-ready', guard, commandId: 'on' }); result = transition(result.state, { type: 'on-sent', guard, commandId: 'on', barrier: marker(2) }); expect(result.state.phase).toBe('key-confirm-pending'); return transition(result.state, { type: 'authority', epoch: 1, ptt: ptt(true, 3), eligibility: eligible, offCommandId: 'off' }).state;
}
function keyPending(): TxState { const pending = start(); const guard = pending.state.guard!; const dispatched = transition(pending.state, { type: 'audio-ready', guard, commandId: 'on' }); return transition(dispatched.state, { type: 'on-sent', guard, commandId: 'on', barrier: marker(2) }).state; }
function complete(state: TxState, seq: number): TxState {
  const begun = start(state, { leaseId: `lease-${seq}`, ptt: ptt(false, seq) }); const guard = begun.state.guard!; const dispatched = transition(begun.state, { type: 'audio-ready', guard, commandId: `on-${seq}` }); expect(types(dispatched)).toContain('dispatch-on');
  const sent = transition(dispatched.state, { type: 'on-sent', guard, commandId: `on-${seq}`, barrier: marker(seq) }); const keyed = transition(sent.state, { type: 'authority', epoch: 1, ptt: ptt(true, seq + 1), eligibility: eligible, offCommandId: 'off' });
  const released = transition(keyed.state, { type: 'release', guard, commandId: `off-${seq}` }); const off = released.state.pendingOff!; const delivered = transition(released.state, { type: 'off-sent', ...off, eventEpoch: 1, barrier: marker(seq + 2) }); return transition(delivered.state, { type: 'authority', epoch: 1, ptt: ptt(false, seq + 3), eligibility: eligible, offCommandId: 'off' }).state;
}
describe('TX reducer', () => {
  it.each([['CAT PTT', { ...eligible, catPtt: false }, ptt(false, 2)], ['browser audio', { ...eligible, browserTxAudio: false }, ptt(false, 2)],
    ['live control', { ...eligible, controlLive: false }, ptt(false, 2)], ['known permit', { ...eligible, permit: 'unknown' }, ptt(false, 2)],
    ['known target', { ...eligible, target: null }, ptt(false, 2)], ['fresh OFF', eligible, ptt(false, 1)]] as const)('fails closed without %s', (_name, eligibility, observation) => {
    const result = start(initialTxState(1, marker(1)), { eligibility, ptt: observation }); expect(result).toMatchObject({ state: { phase: 'failed', fault: 'not-eligible', txRisk: 'none', leaseId: null }, effects: [] });
  });
  it('models the happy path without optimistic RF truth', () => {
    const result = start(); expect(result.state).toMatchObject({ phase: 'audio-start-pending', intent: 'momentary', sourceId: 'desktop', leaseId: 'lease', generation: 1,
      authorityEpoch: 1, leaseTarget: target, startPttBaseline: marker(2), modBarrier: marker(2), radioTx: 'off', mayOwnKey: false, pendingOff: null });
    expect(types(result)).toEqual(['start-audio', 'arm-audio-timeout']);
    expect(start(result.state, { sourceId: 'mobile', leaseId: 'other' })).toEqual({ state: result.state, effects: [] });
    const guard = result.state.guard!; expect(transition(result.state, { type: 'intent', sourceId: 'mobile', guard, intent: 'latched' })).toEqual({ state: result.state, effects: [] }); expect(transition(result.state, { type: 'release', sourceId: 'mobile', guard, commandId: 'off' })).toEqual({ state: result.state, effects: [] });
    const released = transition(result.state, { type: 'release', guard, commandId: 'off' }); expect(transition(released.state, { type: 'intent', sourceId: 'desktop', guard: released.state.guard!, intent: 'latched' })).toEqual({ state: released.state, effects: [] });
    expect(active()).toMatchObject({ phase: 'active', intent: 'latched', radioTx: 'on', onConfirmed: marker(3) });
  });
  it('rejects stale start evidence without regressing authoritative truth', () => { const external = transition(initialTxState(1, marker(1)), { type: 'authority', epoch: 1, ptt: ptt(true, 10), eligibility: eligible, offCommandId: 'off' }); const stale = start(external.state, { ptt: ptt(false, 2) }); expect(stale.state).toMatchObject({ phase: 'failed', fault: 'not-eligible', pttMarker: marker(10), radioTx: 'on' }); });
  it('supports consecutive successful leases without retained markers', () => { const once = complete(initialTxState(1, marker(1)), 2); const twice = complete(once, 6); expect(twice).toMatchObject({ phase: 'idle', generation: 4, leaseTarget: null, startPttBaseline: null, modBarrier: null, onCommandId: null, onDispatch: null, onConfirmed: null, fault: null }); });
  it('cancels pre-key without OFF and coalesces qualified release', () => {
    const pending = start(); const cancelled = transition(pending.state, { type: 'release', sourceId: 'desktop', guard: pending.state.guard!, commandId: 'off-pre' });
    expect(types(cancelled)).toEqual(['cancel-timers', 'stop-local-audio']);
    const keyed = active(); const released = transition(keyed, { type: 'release', sourceId: 'desktop', guard: keyed.guard!, commandId: 'off' });
    expect(types(released)).toEqual(['cancel-timers', 'dispatch-off', 'arm-off-timeout', 'stop-local-audio']); expect(released.state.phase).toBe('releasing');
    expect(released.state).toMatchObject({ phase: 'releasing', generation: 2, pendingOff: { commandId: 'off', leaseId: 'lease', generation: 2,
      originalEpoch: 1, deliveryEpoch: null, deliveryPttBarrier: null, deliveryRebound: false } });
    const opened = transition(released.state, { type: 'epoch', epoch: 2, baseline: marker(1, 2), offCommandId: 'other' }); const premature = transition(opened.state, { type: 'authority', epoch: 2, ptt: ptt(false, 2, 2), eligibility: eligible, offCommandId: 'other' }); expect(types(premature)).toEqual([]); const off = opened.state.pendingOff!; const sent = { type: 'off-sent' as const, ...off, eventEpoch: 2, barrier: marker(2, 2) }; const delivered = transition(opened.state, sent); expect(types(delivered)).toEqual(['arm-off-timeout']); expect(delivered.state.pendingOff?.deliveryRebound).toBe(true); expect(transition(delivered.state, sent)).toEqual({ state: delivered.state, effects: [] });
    expect(transition(released.state, { type: 'release', sourceId: 'desktop', guard: released.state.guard!, commandId: 'other' })).toEqual({ state: released.state, effects: [] });
  });
  it('retains exactly one OFF obligation from ON dispatch through teardown', () => {
    const pending = start(); const guard = pending.state.guard!; const dispatched = transition(pending.state, { type: 'audio-ready', guard, commandId: 'on' });
    expect(dispatched.state).toMatchObject({ mayOwnKey: true, txRisk: 'uncertain', onDispatch: marker(2) }); expect(types(dispatched)).toEqual(['cancel-timers', 'dispatch-on', 'arm-on-timeout']); expect(transition(dispatched.state, { type: 'audio-ready', guard, commandId: 'duplicate' })).toEqual({ state: dispatched.state, effects: [] });
    const routes: TxEvent[] = [{ type: 'release', sourceId: 'desktop', guard, commandId: 'off' }, { type: 'fail', guard, fault: 'ptt-on-rejected', offCommandId: 'off' }, { type: 'fail', guard, fault: 'audio-failed', offCommandId: 'off' },
      { type: 'epoch', epoch: 2, baseline: marker(1, 2), offCommandId: 'off' }, { type: 'authority', epoch: 1, ptt: ptt(false, 2), eligibility: { ...eligible, controlLive: false }, offCommandId: 'off' }];
    for (const event of routes) {
      const released = transition(dispatched.state, event); expect(types(released)).toEqual(['cancel-timers', 'dispatch-off', 'arm-off-timeout', 'stop-local-audio']); expect(released.state.pendingOff).toMatchObject({ commandId: 'off', deliveryPttBarrier: null });
      expect(transition(released.state, { type: 'on-sent', guard, commandId: 'on', barrier: marker(3) })).toEqual({ state: released.state, effects: [] });
    }
  });
  it('handles external and locally-correlated authoritative ON races', () => {
    const pending = start(); const external = transition(pending.state, { type: 'authority', epoch: 1, ptt: ptt(true, 3), eligibility: eligible, offCommandId: 'off' });
    expect(types(external)).toEqual(['cancel-timers', 'stop-local-audio']); expect(external.state).toMatchObject({ phase: 'releasing', radioTx: 'on', mayOwnKey: false, modRestorePending: true });
    const settled = transition(external.state, { type: 'authority', epoch: 1, ptt: ptt(false, 4), eligibility: eligible, offCommandId: 'off' }); expect(types(settled)).toContain('restore-mod');
    const begun = start(); const guard = begun.state.guard!; const dispatched = transition(begun.state, { type: 'audio-ready', guard, commandId: 'on' }); const confirmed = transition(dispatched.state, { type: 'authority', epoch: 1, ptt: ptt(true, 3), eligibility: eligible, offCommandId: 'off' });
    expect(confirmed.state).toMatchObject({ phase: 'audio-start-pending', txRisk: 'confirmed-on', onConfirmed: marker(3) });
    const rebound = transition(confirmed.state, { type: 'on-sent', guard, commandId: 'on', barrier: marker(3) }); expect(rebound.state.phase).toBe('active'); expect(types(rebound)).toEqual(['arm-on-timeout']); expect(transition(rebound.state, { type: 'on-sent', guard, commandId: 'on', barrier: marker(3) })).toEqual({ state: rebound.state, effects: [] });
    const dekeyed = transition(rebound.state, { type: 'authority', epoch: 1, ptt: ptt(false, 4), eligibility: eligible, offCommandId: 'off' }); expect(dekeyed.state.fault).toBe('backend-dekeyed'); const released = transition(dispatched.state, { type: 'release', guard, commandId: 'off' }); const raced = transition(released.state, { type: 'authority', epoch: 1, ptt: ptt(true, 3), eligibility: eligible, offCommandId: 'off' });
    expect(raced.state).toMatchObject({ phase: 'releasing', radioTx: 'on', txRisk: 'confirmed-on', onConfirmed: marker(3) });
  });
  it('qualifies post-ON failures and rejects stale epochs while rebasing pre-key MOD cleanup', () => {
    for (const fault of ['ptt-on-rejected', 'audio-failed'] as const) {
      const keyed = keyPending(); const rejected = transition(keyed, { type: 'fail', guard: keyed.guard!, fault, offCommandId: 'off' });
      expect(rejected.state).toMatchObject({ phase: 'releasing', fault, mayOwnKey: true, pendingOff: { commandId: 'off' } }); expect(types(rejected)).toContain('dispatch-off'); const off = rejected.state.pendingOff!; const sent = transition(rejected.state, { type: 'off-sent', ...off, eventEpoch: 1, barrier: marker(3) }); const idle = transition(sent.state, { type: 'authority', epoch: 1, ptt: ptt(false, 4), eligibility: eligible, offCommandId: 'off' }); expect(idle.state).toMatchObject({ phase: 'idle', fault: null, onCommandId: null, modBarrier: null });
    }
    const pending = start(); const opened = transition(pending.state, { type: 'epoch', epoch: 2, baseline: marker(1, 2), offCommandId: 'off' });
    expect(opened.state.modBarrier).toEqual(marker(1, 2)); for (const [epoch, baselineEpoch] of [[2, 2], [1, 1], [3, 2]] as const) expect(transition(opened.state, { type: 'epoch', epoch, baseline: marker(9, baselineEpoch), offCommandId: 'stale' })).toEqual({ state: opened.state, effects: [] });
    expect(types(transition(opened.state, { type: 'authority', epoch: 2, ptt: ptt(false, 2, 2), eligibility: eligible, offCommandId: 'off' }))).toContain('restore-mod');
  });
  it.each([['receiver', { ...eligible, target: { ...target, receiver: 'SUB' as const } }], ['slot', { ...eligible, target: { ...target, slot: 'B' as const } }], ['frequency', { ...eligible, target: { ...target, frequencyHz: target.frequencyHz + 1 } }], ['unknown', { ...eligible, target: null }], ['permit', { ...eligible, permit: 'unknown' }]] as const)('routes %s loss by key ownership', (_name, eligibility) => {
    const pending = start(); expect(types(transition(pending.state, { type: 'authority', epoch: 1, ptt: ptt(false, 2), eligibility, offCommandId: 'off' }))).not.toContain('dispatch-off');
    expect(types(transition(active(), { type: 'authority', epoch: 1, ptt: ptt(true, 3), eligibility, offCommandId: 'off' }))).toContain('dispatch-off');
  });
  it('treats qualifying backend de-key and later re-key as external', () => { const dekeyed = transition(active(), { type: 'authority', epoch: 1, ptt: ptt(false, 4), eligibility: eligible, offCommandId: 'off' }); expect(dekeyed.state).toMatchObject({ phase: 'failed', fault: 'backend-dekeyed', txRisk: 'none', leaseId: null, radioTx: 'off', modRestorePending: false }); expect(types(dekeyed)).toEqual(['cancel-timers', 'stop-local-audio', 'restore-mod']); const rekeyed = transition(dekeyed.state, { type: 'authority', epoch: 1, ptt: ptt(true, 5), eligibility: eligible, offCommandId: 'off' }); expect(rekeyed).toMatchObject({ state: { radioTx: 'on', leaseId: null }, effects: [] }); });
  it('resets only discharged faults and preserves authority truth', () => {
    const rejected = start(initialTxState(1, marker(1)), { eligibility: { ...eligible, permit: 'unknown' } }); const reset = transition(rejected.state, { type: 'reset-fault' }); expect(reset.state).toMatchObject({ phase: 'idle', intent: null, fault: null, generation: rejected.state.generation, authorityEpoch: 1, pttMarker: rejected.state.pttMarker, radioTx: 'unknown' }); expect(start(reset.state).state.phase).toBe('audio-start-pending');
    const dekeyed = transition(active(), { type: 'authority', epoch: 1, ptt: ptt(false, 4), eligibility: eligible, offCommandId: 'off' }); const recovered = transition(dekeyed.state, { type: 'reset-fault' }); expect(recovered.state).toMatchObject({ phase: 'idle', fault: null, radioTx: 'off', onCommandId: null }); expect(start(recovered.state, { ptt: ptt(false, 5) }).state.phase).toBe('audio-start-pending');
    const pending = start(); const failed = transition(pending.state, { type: 'fail', guard: pending.state.guard!, fault: 'audio-failed', offCommandId: 'off' }); expect(transition(failed.state, { type: 'reset-fault' })).toEqual({ state: failed.state, effects: [] });
    const keyed = active(); const released = transition(keyed, { type: 'release', guard: keyed.guard!, commandId: 'off' }); expect(transition(released.state, { type: 'reset-fault' })).toEqual({ state: released.state, effects: [] });
  });
});
