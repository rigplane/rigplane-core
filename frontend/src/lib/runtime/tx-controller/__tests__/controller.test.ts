import { afterEach, describe, expect, it, vi } from 'vitest';
import { TxController, type TxControllerDependencies } from '../controller';
import type { Eligibility, PttMarker, PttObservation, TxEvent } from '../model';
const marker = (seq: number, epoch = 1): PttMarker => ({ authorityEpoch: epoch, pttObservationSeq: seq, pttLastObservedMonotonic: seq });
const ptt = (value: boolean, seq: number): PttObservation => ({ value, observed: true, fresh: true, source: 'radio-readback', marker: marker(seq) });
const eligible: Eligibility = { catPtt: true, browserTxAudio: true, controlLive: true, permit: 'allowed', target: { receiver: 'MAIN', slot: 'A', frequencyHz: 14_074_000 } };
const start = (): TxEvent => ({ type: 'start', sourceId: 'desktop', leaseId: 'lease', intent: 'momentary', eligibility: eligible, ptt: ptt(false, 1) });

function harness(audio: Promise<string | null> = Promise.resolve(null), failure: 'off' | 'cancel' | 10 | 20 | 30 | null = null) {
  const log: string[] = []; const reports: Array<{ command: 'on' | 'off'; report: Parameters<TxControllerDependencies['sendPtt']>[3] }> = [];
  let id = 0;
  const dependencies: TxControllerDependencies = {
    startAudio: vi.fn(() => { log.push('audio'); return audio; }),
    sendPtt: vi.fn((command, _id, _correlation, report) => { log.push(command); reports.push({ command, report }); if (command === 'off' && failure === 'off') throw new Error('closed'); }),
    stopLocalAudio: vi.fn(() => { log.push('stop'); }), restoreMod: vi.fn(() => { log.push('restore'); }),
    commandId: vi.fn((command) => `${command}-${++id}`), schedule: vi.fn((callback, delay) => { if (delay === failure) throw new Error('clock'); return setTimeout(callback, delay); }),
    cancel: vi.fn((handle) => { clearTimeout(handle as ReturnType<typeof setTimeout>); if (failure === 'cancel') throw new Error('cancel'); }),
    timeoutMs: { 'audio-start': 10, 'on-confirmation': 20, 'off-confirmation': 30 },
  };
  return { controller: new TxController(1, marker(0), dependencies), dependencies, log, reports };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

describe('TxController', () => {
  afterEach(() => vi.useRealTimers());

  it('runs audio before one ON and command acknowledgements never create RF truth', async () => {
    vi.useFakeTimers(); const h = harness(); h.controller.dispatch(start()); expect(h.log).toEqual(['audio']);
    await flush(); expect(h.log).toEqual(['audio', 'on']); expect(h.reports.filter((item) => item.command === 'on')).toHaveLength(1);
    const sent = h.reports[0].report; sent({ outcome: 'sent', eventEpoch: 1, barrier: marker(2) }); sent({ outcome: 'ack', eventEpoch: 1, barrier: null });
    expect(h.controller.snapshot()).toMatchObject({ phase: 'key-confirm-pending', radioTx: 'off', mayOwnKey: true });
  });

  it('delivers the exact audio timeout and suppresses its late async completion', async () => {
    vi.useFakeTimers(); let resolve!: (value: string | null) => void; const h = harness(new Promise((done) => { resolve = done; }));
    h.controller.dispatch(start()); vi.advanceTimersByTime(10);
    expect(h.controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'audio-timeout' }); expect(h.log).toEqual(['audio', 'stop']);
    resolve(null); await flush(); expect(h.log).toEqual(['audio', 'stop']); expect(h.reports).toHaveLength(0);
  });

  it('delivers correlated ON and OFF deadlines at their exact fake-clock durations', async () => {
    vi.useFakeTimers(); const h = harness(); h.controller.dispatch(start()); await flush();
    vi.advanceTimersByTime(20);
    expect(h.controller.snapshot()).toMatchObject({ phase: 'releasing', fault: 'on-timeout' }); expect(h.log).toEqual(['audio', 'on', 'off', 'stop']);
    vi.advanceTimersByTime(30);
    expect(h.controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'release-not-confirmed' });
  });

  it('coalesces release, attempts OFF before stop, and cleans up after a synchronous send failure', async () => {
    vi.useFakeTimers(); const h = harness(Promise.resolve(null), 'off'); h.controller.dispatch(start()); await flush();
    const guard = h.controller.snapshot().guard!; h.controller.dispatch({ type: 'release', guard, commandId: 'off-release' });
    expect(h.log).toEqual(['audio', 'on', 'off', 'stop']); expect(h.controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'release-not-confirmed' });
    h.controller.dispatch({ type: 'release', guard: h.controller.snapshot().guard!, commandId: 'off-duplicate' });
    h.reports.find((item) => item.command === 'on')!.report({ outcome: 'sent', eventEpoch: 1, barrier: marker(2) });
    expect(h.log.filter((item) => item === 'off')).toHaveLength(1); expect(h.log.filter((item) => item === 'stop')).toHaveLength(1);
  });

  it('isolates throwing observers and timer cancellation so release still performs OFF then stop', async () => {
    vi.useFakeTimers(); const cases = [harness(), harness(Promise.resolve(null), 'cancel')]; cases[0].controller.subscribe(() => { throw new Error('observer'); });
    for (const h of cases) { h.controller.dispatch(start()); await flush(); h.controller.dispatch({ type: 'release', guard: h.controller.snapshot().guard!, commandId: 'off' }); expect(h.log).toEqual(['audio', 'on', 'off', 'stop']); }
    expect(vi.mocked(cases[1].dependencies.cancel)).toHaveBeenCalled();
  });

  it('fails closed with exact qualified audio, ON, and OFF events when scheduling throws', async () => {
    vi.useFakeTimers(); const audio = harness(Promise.resolve(null), 10); audio.controller.dispatch(start()); await flush(); expect(audio.controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'audio-timeout' }); expect(audio.log).toEqual(['audio', 'stop']);
    const on = harness(Promise.resolve(null), 20); on.controller.dispatch(start()); await flush(); expect(on.controller.snapshot()).toMatchObject({ phase: 'releasing', fault: 'on-timeout' }); expect(on.log).toEqual(['audio', 'on', 'off', 'stop']);
    on.reports[0].report({ outcome: 'sent', eventEpoch: 1, barrier: marker(2) }); expect(on.log).toEqual(['audio', 'on', 'off', 'stop']);
    const off = harness(Promise.resolve(null), 30); off.controller.dispatch(start()); await flush(); off.controller.dispatch({ type: 'release', guard: off.controller.snapshot().guard!, commandId: 'off' });
    expect(off.controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'release-not-confirmed' }); expect(off.log).toEqual(['audio', 'on', 'off', 'stop']);
  });

  it('never replays ON when a pre-key lease is released before audio completes', async () => {
    vi.useFakeTimers(); let resolve!: (value: string | null) => void; const h = harness(new Promise((done) => { resolve = done; }));
    h.controller.dispatch(start()); h.controller.dispatch({ type: 'release', guard: h.controller.snapshot().guard!, commandId: 'off-cancel' });
    resolve(null); await flush(); vi.runAllTimers();
    expect(h.log).toEqual(['audio', 'stop']); expect(h.reports).toHaveLength(0); expect(h.controller.snapshot().mayOwnKey).toBe(false);
  });

  it('restores MOD only from the reducer obligation after post-OFF authority', async () => {
    vi.useFakeTimers(); const h = harness(); h.controller.dispatch(start()); await flush();
    h.reports[0].report({ outcome: 'sent', eventEpoch: 1, barrier: marker(2) });
    h.controller.dispatch({ type: 'authority', epoch: 1, ptt: ptt(true, 3), eligibility: eligible, offCommandId: 'off-a' });
    h.controller.dispatch({ type: 'release', guard: h.controller.snapshot().guard!, commandId: 'off-a' });
    h.reports.find((item) => item.command === 'off')!.report({ outcome: 'sent', eventEpoch: 1, barrier: marker(3) });
    const cancelled = vi.mocked(h.dependencies.cancel).mock.calls.length; h.controller.dispatch({ type: 'authority', epoch: 1, ptt: ptt(false, 4), eligibility: eligible, offCommandId: 'off-a' }); expect(vi.mocked(h.dependencies.cancel).mock.calls).toHaveLength(cancelled + 2); expect(vi.getTimerCount()).toBe(0);
    h.controller.dispatch({ type: 'authority', epoch: 1, ptt: ptt(false, 5), eligibility: eligible, offCommandId: 'off-a' });
    expect(h.log.filter((item) => item === 'restore')).toHaveLength(1); expect(h.controller.snapshot().phase).toBe('idle');
  });
});
