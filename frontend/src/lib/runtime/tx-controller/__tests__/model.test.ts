import { describe, expect, it } from 'vitest';
import { initialTxState, transition, type Eligibility, type PttObservation, type TxEvent, type TxState } from '../model';
const marker = (seq: number, authorityEpoch = 1) => ({ authorityEpoch, pttObservationSeq: seq, pttLastObservedMonotonic: seq });
const target = { receiver: 'MAIN', slot: 'A', frequencyHz: 14_074_000 } as const;
const eligible: Eligibility = { catPtt: true, browserTxAudio: true, controlLive: true, permit: 'allowed', target };
const ptt = (value: boolean, seq: number, epoch = 1): PttObservation => ({ value, observed: true, fresh: true, source: 'radio-readback', marker: marker(seq, epoch) });
const start = (state = initialTxState(1, marker(1)), overrides = {}) => transition(state, { type: 'start', sourceId: 'desktop', leaseId: 'lease', intent: 'momentary', eligibility: eligible, ptt: ptt(false, 2), ...overrides });
const types = (result: ReturnType<typeof transition>) => result.effects.map((effect) => effect.type);
const correlation = <const T extends string | null>(state: TxState, commandId: T) => ({ commandId, leaseId: state.guard!.leaseId, generation: state.guard!.generation, originalEpoch: state.guard!.authorityEpoch, eventEpoch: state.authorityEpoch, offCommandId: 'off' });
const timerEvent = (state: TxState, timer: 'audio-start' | 'on-confirmation' | 'off-confirmation', commandId: string | null) => ({ type: 'timer-fired' as const, timer, armRevision: timer === 'audio-start' ? state.timerRevision.audio : timer === 'on-confirmation' ? state.timerRevision.on : state.timerRevision.off, ...correlation(state, commandId) });
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
  it('correlates audio deadlines and performs one local failure cleanup', () => {
    const pending = start(); const timer = timerEvent(pending.state, 'audio-start', null);
    const stale = transition(pending.state, { ...timer, generation: timer.generation + 1 }); expect(stale).toEqual({ state: pending.state, effects: [] });
    const failed = transition(pending.state, timer);
    expect(failed.state).toMatchObject({ phase: 'failed', fault: 'audio-timeout', mayOwnKey: false, modRestorePending: true, pendingOff: null });
    expect(types(failed)).toEqual(['cancel-timers', 'stop-local-audio']); expect(transition(failed.state, timer)).toEqual({ state: failed.state, effects: [] });
    expect(types(transition(failed.state, { type: 'audio-ready', guard: pending.state.guard!, commandId: 'late-on' }))).not.toContain('dispatch-on');
    const audioFailed = transition(pending.state, { type: 'fail', guard: pending.state.guard!, fault: 'audio-failed', offCommandId: 'off' }); expect(audioFailed.state.fault).toBe('audio-failed'); expect(types(audioFailed)).not.toContain('dispatch-off');
  });
  it('correlates ON command results and errors without RF truth or retry', () => {
    const pending = start(); const guard = pending.state.guard!; const dispatched = transition(pending.state, { type: 'audio-ready', guard, commandId: 'on' });
    const premature = { type: 'command-result', command: 'on', outcome: 'sent', barrier: marker(2), ...correlation(pending.state, null) } as unknown as TxEvent;
    expect(transition(pending.state, premature)).toEqual({ state: pending.state, effects: [] });
    const base = correlation(dispatched.state, 'on'); const sentEvent = { type: 'command-result' as const, command: 'on' as const, outcome: 'sent' as const, barrier: marker(2), ...base };
    for (const outcome of ['ack', 'response-ok'] as const) expect(transition(dispatched.state, { ...sentEvent, outcome, barrier: null })).toEqual({ state: dispatched.state, effects: [] });
    for (const patch of [{ command: 'off' as const }, { commandId: 'wrong' }, { leaseId: 'wrong' }, { generation: base.generation + 1 }, { originalEpoch: 2 }, { eventEpoch: 2 }, { barrier: marker(2, 2) }]) expect(transition(dispatched.state, { ...sentEvent, ...patch })).toEqual({ state: dispatched.state, effects: [] });
    const sent = transition(dispatched.state, sentEvent); expect(sent.state).toMatchObject({ phase: 'key-confirm-pending', radioTx: 'off', onConfirmed: null }); expect(types(sent)).toEqual(['arm-on-timeout']); expect(sent.effects[0]?.armRevision).toBe(sent.state.timerRevision.on);
    expect(transition(sent.state, sentEvent)).toEqual({ state: sent.state, effects: [] });
    for (const outcome of ['response-error', 'transport-error'] as const) {
      const failed = transition(dispatched.state, { ...sentEvent, outcome, barrier: null }); expect(failed.state).toMatchObject({ phase: 'releasing', fault: 'on-command-failed', radioTx: 'off', onConfirmed: null, pendingOff: { commandId: 'off' } });
      expect(types(failed).filter((type) => type === 'dispatch-off')).toEqual(['dispatch-off']); expect(transition(failed.state, { ...sentEvent, outcome, barrier: null })).toEqual({ state: failed.state, effects: [] });
    }
    const timer = timerEvent(dispatched.state, 'on-confirmation', 'on'); const timed = transition(dispatched.state, timer); expect(timed.state).toMatchObject({ phase: 'releasing', fault: 'on-timeout' }); expect(types(timed)).not.toContain('dispatch-on'); expect(transition(timed.state, timer)).toEqual({ state: timed.state, effects: [] });
    const timedAfterSent = transition(sent.state, timer); expect(timedAfterSent).toEqual({ state: sent.state, effects: [] }); expect(transition(sent.state, timerEvent(sent.state, 'on-confirmation', 'on')).state.fault).toBe('on-timeout');
  });
  it('preserves failed OFF obligations for errors and exact deadlines', () => {
    const keyed = active(); const released = transition(keyed, { type: 'release', guard: keyed.guard!, commandId: 'off' }); const base = correlation(released.state, 'off');
    const dispatchTimer = timerEvent(released.state, 'off-confirmation', 'off');
    const sentEvent = { type: 'command-result' as const, command: 'off' as const, outcome: 'sent' as const, barrier: marker(4), ...base };
    for (const outcome of ['ack', 'response-ok'] as const) expect(transition(released.state, { ...sentEvent, outcome, barrier: null })).toEqual({ state: released.state, effects: [] });
    for (const patch of [{ command: 'on' as const }, { commandId: 'wrong' }, { leaseId: 'wrong' }, { generation: base.generation + 1 }, { originalEpoch: 2 }, { eventEpoch: 2 }, { barrier: marker(4, 2) }]) expect(transition(released.state, { ...sentEvent, ...patch })).toEqual({ state: released.state, effects: [] });
    const delivered = transition(released.state, sentEvent); expect(delivered.state.pendingOff).toMatchObject({ deliveryEpoch: 1, deliveryPttBarrier: marker(4) }); expect(delivered.effects[0]?.armRevision).toBe(delivered.state.timerRevision.off); expect(transition(delivered.state, dispatchTimer)).toEqual({ state: delivered.state, effects: [] });
    for (const trigger of [
      { type: 'command-result' as const, command: 'off' as const, outcome: 'response-error' as const, barrier: null, ...base },
      { type: 'command-result' as const, command: 'off' as const, outcome: 'transport-error' as const, barrier: null, ...base },
      timerEvent(delivered.state, 'off-confirmation', 'off'),
    ]) {
      const failed = transition(delivered.state, trigger); expect(failed.state).toMatchObject({ phase: 'failed', fault: 'release-not-confirmed', pendingOff: delivered.state.pendingOff, mayOwnKey: true, modRestorePending: true });
      expect(types(failed)).not.toContain('dispatch-off'); expect(transition(failed.state, trigger)).toEqual({ state: failed.state, effects: [] });
    }
    const failedBeforeSent = transition(released.state, { ...sentEvent, outcome: 'response-error', barrier: null }); const lateSent = transition(failedBeforeSent.state, sentEvent); expect(lateSent.state.pendingOff?.deliveryPttBarrier).toEqual(marker(4));
  });
  it('discharges failed release only from qualifying authoritative OFF', () => {
    const keyed = active(); const released = transition(keyed, { type: 'release', guard: keyed.guard!, commandId: 'off' }); const base = correlation(released.state, 'off');
    const delivered = transition(released.state, { type: 'command-result', command: 'off', outcome: 'sent', barrier: marker(4), ...base });
    const failed = transition(delivered.state, timerEvent(delivered.state, 'off-confirmation', 'off'));
    expect(transition(failed.state, { type: 'reset-fault' })).toEqual({ state: failed.state, effects: [] });
    const cached = transition(failed.state, { type: 'authority', epoch: 1, ptt: ptt(false, 4), eligibility: eligible, offCommandId: 'off' }); expect(cached.state.pendingOff).not.toBeNull(); expect(types(cached)).toEqual([]);
    const other = transition(cached.state, { type: 'authority', epoch: 1, ptt: { ...ptt(false, 5), source: 'other' }, eligibility: eligible, offCommandId: 'off' }); expect(other).toEqual({ state: cached.state, effects: [] });
    const wrongEpoch = transition(cached.state, { type: 'authority', epoch: 2, ptt: ptt(false, 5, 2), eligibility: eligible, offCommandId: 'off' }); expect(wrongEpoch).toEqual({ state: cached.state, effects: [] });
    const discharged = transition(cached.state, { type: 'authority', epoch: 1, ptt: ptt(false, 5), eligibility: eligible, offCommandId: 'off' });
    expect(discharged.state).toMatchObject({ phase: 'failed', fault: 'release-not-confirmed', pendingOff: null, mayOwnKey: false, modRestorePending: false, txRisk: 'none' }); expect(types(discharged)).toEqual(['cancel-timers', 'restore-mod']); expect(discharged.effects.every((item) => item.guard?.generation === released.state.cleanupGuard!.generation)).toBe(true);
    expect(transition(discharged.state, { type: 'authority', epoch: 1, ptt: ptt(false, 5), eligibility: eligible, offCommandId: 'off' })).toEqual({ state: discharged.state, effects: [] });
    expect(transition(discharged.state, { type: 'reset-fault' }).state.phase).toBe('idle');
  });
  it('normalizes cleanup guards across generation invalidation', () => {
    for (const route of ['audio-failed', 'audio-timeout'] as const) {
      const pending = start(); const cleanupGuard = pending.state.cleanupGuard!;
      const failed = route === 'audio-failed' ? transition(pending.state, { type: 'fail', guard: pending.state.guard!, fault: route, offCommandId: 'off' }) : transition(pending.state, timerEvent(pending.state, 'audio-start', null));
      expect(failed.state).toMatchObject({ phase: 'failed', guard: null, cleanupGuard, modRestorePending: true }); expect(failed.effects.every((item) => item.guard === cleanupGuard)).toBe(true); expect(transition(failed.state, { type: 'reset-fault' })).toEqual({ state: failed.state, effects: [] });
      const opened = transition(failed.state, { type: 'epoch', epoch: 2, baseline: marker(1, 2), offCommandId: 'off' }); expect(opened.state).toMatchObject({ cleanupGuard, modBarrier: marker(1, 2) });
      const authority = { type: 'authority' as const, epoch: 2, ptt: ptt(false, 2, 2), eligibility: eligible, offCommandId: 'off' }; const discharged = transition(opened.state, authority);
      expect(discharged.state).toMatchObject({ phase: 'failed', cleanupGuard: null, modRestorePending: false }); expect(discharged.effects.every((item) => item.guard === cleanupGuard)).toBe(true); expect(transition(discharged.state, authority)).toEqual({ state: discharged.state, effects: [] }); expect(transition(discharged.state, { type: 'reset-fault' }).state.phase).toBe('idle');
    }
    const keyed = active(); const released = transition(keyed, { type: 'release', guard: keyed.guard!, commandId: 'off' });
    expect(released.effects.filter((item) => item.type === 'cancel-timers' || item.type === 'stop-local-audio').every((item) => item.guard?.generation === keyed.guard!.generation)).toBe(true);
    expect(released.effects.filter((item) => item.type === 'dispatch-off' || item.type === 'arm-off-timeout').every((item) => item.guard?.generation === released.state.guard!.generation)).toBe(true);
  });
  it('preserves empty command IDs through ON and OFF correlation paths', () => {
    const pending = start(); const guard = pending.state.guard!; const dispatched = transition(pending.state, { type: 'audio-ready', guard, commandId: '' });
    expect(dispatched.effects.filter((item) => item.type === 'dispatch-on' || item.type === 'arm-on-timeout').map((item) => item.commandId)).toEqual(['', '']);
    const onSentEvent = { type: 'command-result' as const, command: 'on' as const, outcome: 'sent' as const, barrier: marker(2), ...correlation(dispatched.state, ''), offCommandId: '' }; const sent = transition(dispatched.state, onSentEvent);
    expect(sent.state.phase).toBe('key-confirm-pending'); expect(sent.effects[0]?.commandId).toBe('');
    const onError = transition(dispatched.state, { ...onSentEvent, outcome: 'response-error', barrier: null }); expect(onError.state.pendingOff?.commandId).toBe(''); expect(onError.effects.filter((item) => item.type === 'dispatch-off' || item.type === 'arm-off-timeout').map((item) => item.commandId)).toEqual(['', '']);
    const onTimed = transition(sent.state, { ...timerEvent(sent.state, 'on-confirmation', ''), offCommandId: '' }); expect(onTimed.state.pendingOff?.commandId).toBe('');
    const keyed = active(); const released = transition(keyed, { type: 'release', guard: keyed.guard!, commandId: '' });
    expect(released.effects.filter((item) => item.type === 'dispatch-off' || item.type === 'arm-off-timeout').map((item) => item.commandId)).toEqual(['', '']);
    const offSentEvent = { type: 'command-result' as const, command: 'off' as const, outcome: 'sent' as const, barrier: marker(4), ...correlation(released.state, ''), offCommandId: '' }; const delivered = transition(released.state, offSentEvent);
    expect(delivered.state.pendingOff?.deliveryPttBarrier).toEqual(marker(4)); expect(delivered.effects[0]?.commandId).toBe('');
    expect(transition(released.state, { ...offSentEvent, outcome: 'response-error', barrier: null }).state.fault).toBe('release-not-confirmed'); expect(transition(delivered.state, { ...timerEvent(delivered.state, 'off-confirmation', ''), offCommandId: '' }).state.fault).toBe('release-not-confirmed');
  });
  it('rejects malformed runtime command IDs before state mutation', () => {
    for (const invalid of [null, 42] as const) {
      const pending = start(); const badAudio = { type: 'audio-ready', guard: pending.state.guard!, commandId: invalid } as unknown as TxEvent; expect(transition(pending.state, badAudio)).toEqual({ state: pending.state, effects: [] }); expect(pending.state.mayOwnKey).toBe(false);
      const keyed = active(); const badRelease = { type: 'release', guard: keyed.guard!, commandId: invalid } as unknown as TxEvent; expect(transition(keyed, badRelease)).toEqual({ state: keyed, effects: [] }); expect(keyed.pendingOff).toBeNull();
      const dispatched = transition(pending.state, { type: 'audio-ready', guard: pending.state.guard!, commandId: 'on' }); const base = correlation(dispatched.state, 'on');
      const malformed = [
        { type: 'fail', guard: dispatched.state.guard!, fault: 'audio-failed', offCommandId: invalid },
        { ...timerEvent(dispatched.state, 'on-confirmation', 'on'), offCommandId: invalid },
        { type: 'command-result', command: 'on', outcome: 'response-error', barrier: null, ...base, offCommandId: invalid },
        { type: 'epoch', epoch: 2, baseline: marker(1, 2), offCommandId: invalid },
        { type: 'authority', epoch: 1, ptt: ptt(false, 3), eligibility: { ...eligible, controlLive: false }, offCommandId: invalid },
      ] as unknown as TxEvent[];
      for (const event of malformed) expect(transition(dispatched.state, event)).toEqual({ state: dispatched.state, effects: [] });
    }
  });
  it('rejects stale identity on every timer family', () => {
    const audio = start(); const guard = audio.state.guard!; const dispatched = transition(audio.state, { type: 'audio-ready', guard, commandId: 'on' }); const keyed = active(); const released = transition(keyed, { type: 'release', guard: keyed.guard!, commandId: 'off' });
    const timers = [
      [audio.state, timerEvent(audio.state, 'audio-start', null)],
      [dispatched.state, timerEvent(dispatched.state, 'on-confirmation', 'on')],
      [released.state, timerEvent(released.state, 'off-confirmation', 'off')],
    ] as const;
    for (const [state, timer] of timers) for (const patch of [{ leaseId: 'wrong' }, { generation: timer.generation + 1 }, { originalEpoch: 2 }, { eventEpoch: 2 }, { commandId: 'wrong' }]) expect(transition(state, { ...timer, ...patch })).toEqual({ state, effects: [] });
  });
});
