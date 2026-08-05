import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';
const h = vi.hoisted(() => ({
  radio: null as any, caps: null as any, deliveries: new Set<(event: CommandDeliveryEvent) => void>(),
  sessions: new Set<(event: ControlSessionTransition) => void>(), send: vi.fn((_name: string, _params: Record<string, unknown>, _id?: string) => true),
  start: vi.fn(async () => null), stop: vi.fn(), restore: vi.fn(), ids: 0,
}));
vi.mock('$lib/stores/radio.svelte', () => ({ getRadioState: () => h.radio }));
vi.mock('$lib/stores/capabilities.svelte', () => ({ getCapabilities: () => h.caps }));
vi.mock('$lib/types/protocol', () => ({ makeCommandId: () => `cmd-${++h.ids}` }));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({ getTxAudioControl: () => ({
  startTx: h.start, stopLocalAudio: h.stop, restoreModAfterConfirmedOff: h.restore,
}) }));
vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: h.send,
  onCommandDelivery: (fn: (event: CommandDeliveryEvent) => void) => {
    h.deliveries.add(fn); return () => h.deliveries.delete(fn);
  },
  onControlSessionTransition: (fn: (event: ControlSessionTransition) => void) => {
    h.sessions.add(fn); return () => h.sessions.delete(fn);
  },
}));
import { createBrowserTxControllerDependencies } from '../browser-dependencies';
import { TxController } from '../controller';
const field = (at: number) => ({ observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: at, source: { source: 'poll_response' } });
const target = { receiver: 'SUB' as const, slot: 'B' as const, frequencyHz: 150 };
const correlation = { leaseId: 'lease', generation: 1, originalEpoch: 4, target };
function resetFacts() {
  h.radio = { revision: 1, ptt: false, active: 'SUB', txTarget: { status: 'known', ...target },
    main: { dataMode: 0 }, sub: { dataMode: 1 }, data1ModInput: 5,
    fieldStatus: { ptt: field(1), txTarget: field(1), data1ModInput: field(1) } };
  h.caps = { tx: true, audioTx: true, capabilities: ['tx', 'mod_input_routing'],
    vfoScheme: 'main_sub', audioTxRequiredModInputSource: 5, txBands: [{ start: 100, end: 200 }] } as Capabilities;
}
const emit = (event: CommandDeliveryEvent) => [...h.deliveries].forEach((fn) => fn(event));
beforeEach(() => {
  vi.useFakeTimers(); resetFacts(); h.deliveries.clear(); h.sessions.clear(); h.send.mockReset().mockReturnValue(true);
  h.start.mockClear(); h.stop.mockClear(); h.restore.mockClear(); h.ids = 0;
});
describe('browser TxController dependencies', () => {
  it('is dormant, exposes removable synchronous seams, delegates audio, and disposes timers', async () => {
    const factory = createBrowserTxControllerDependencies();
    expect([h.deliveries.size, h.sessions.size, h.start.mock.calls.length, vi.getTimerCount()]).toEqual([0, 0, 0, 0]);
    expect(factory.projectAuthority({ state: 'connected', epoch: 4 }).eligibility.target).toEqual(target); expect(factory.dependencies.commandId('on')).toBe('cmd-1');
    const seen: number[] = []; const offSession = factory.subscribeSession((projection) => seen.push(projection.epoch));
    [...h.sessions][0]({ state: 'connected', epoch: 5 }); expect(seen).toEqual([5]); offSession(); expect(h.sessions.size).toBe(0);
    let lifecycle!: () => void; let releases = 0; const unsubscribe = vi.fn();
    factory.bindLifecycleRelease((fn) => { lifecycle = fn; return unsubscribe; }, () => releases++);
    lifecycle(); expect(releases).toBe(1);
    await expect(factory.dependencies.startAudio()).resolves.toBeNull(); factory.dependencies.stopLocalAudio();
    factory.dependencies.restoreMod({ authorityEpoch: 5, pttObservationSeq: 1, pttLastObservedMonotonic: 1 },
      { value: false, observed: true, fresh: true, source: 'radio-readback',
        marker: { authorityEpoch: 5, pttObservationSeq: 2, pttLastObservedMonotonic: 2 } });
    expect([h.start.mock.calls.length, h.stop.mock.calls.length, h.restore.mock.calls.length]).toEqual([1, 1, 1]);
    const canceled = vi.fn(); const clear = vi.spyOn(globalThis, 'clearTimeout');
    const canceledHandle = factory.dependencies.schedule(canceled, 10); factory.dependencies.cancel(canceledHandle);
    expect(clear).toHaveBeenCalledWith(canceledHandle); vi.advanceTimersByTime(20); expect(canceled).not.toHaveBeenCalled();
    const fired = vi.fn(); factory.dependencies.schedule(fired, 10); factory.subscribeSession(vi.fn());
    factory.dispose(); factory.dispose(); vi.advanceTimersByTime(20); lifecycle();
    expect([fired.mock.calls.length, h.sessions.size, h.deliveries.size, vi.getTimerCount(), unsubscribe.mock.calls.length, releases])
      .toEqual([0, 0, 0, 0, 1, 1]);
    factory.dependencies.sendPtt('on', 'disposed', correlation, vi.fn()); expect(h.send).not.toHaveBeenCalled();
    clear.mockRestore();
  });
  it('registers before send, freezes lease correlation, and maps only delivery evidence', () => {
    const factory = createBrowserTxControllerDependencies();
    factory.projectAuthority({ state: 'connected', epoch: 4 });
    h.radio = { ...h.radio, revision: 9_999, txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 199 },
      fieldStatus: { ...h.radio.fieldStatus, ptt: field(2) } };
    h.send.mockImplementation((_name, _params, id) => {
      emit({ commandId: id!, kind: 'transport-sent', originalEpoch: 4, eventEpoch: 7 }); return true;
    });
    const reports: any[] = [];
    factory.dependencies.sendPtt('on', 'on-1', correlation, (report) => reports.push(report));
    expect(h.send).toHaveBeenCalledWith('ptt_on', { target, originalEpoch: 4 }, 'on-1');
    expect(reports).toEqual([{ outcome: 'sent', eventEpoch: 7,
      barrier: { authorityEpoch: 7, pttObservationSeq: 2, pttLastObservedMonotonic: 2 } }]);
    emit({ commandId: 'foreign', kind: 'error', originalEpoch: 4, eventEpoch: 7 });
    emit({ commandId: 'on-1', kind: 'ack', originalEpoch: 3, eventEpoch: 7 }); expect(reports).toHaveLength(1);
    for (const [kind, outcome] of [['ack', 'ack'], ['response-ok', 'response-ok']] as const) {
      emit({ commandId: 'on-1', kind, originalEpoch: 4, eventEpoch: 7 });
      expect(reports.at(-1)).toEqual({ outcome, eventEpoch: 7, barrier: null });
    }
    expect(h.restore).not.toHaveBeenCalled(); expect(h.deliveries.size).toBe(0);
    emit({ commandId: 'on-1', kind: 'error', originalEpoch: 4, eventEpoch: 7 }); expect(reports).toHaveLength(3);
  });
  it('fails ON closed but keeps OFF queued with its original lease epoch across reconnect', () => {
    const factory = createBrowserTxControllerDependencies(); factory.projectAuthority({ state: 'connected', epoch: 4 });
    h.send.mockReturnValue(false); const on: any[] = []; const off: any[] = [];
    factory.dependencies.sendPtt('on', 'on-refused', correlation, (report) => on.push(report));
    factory.dependencies.sendPtt('off', 'off-queued', correlation, (report) => off.push(report));
    expect(on).toEqual([{ outcome: 'transport-error', eventEpoch: 4, barrier: null }]); expect(off).toEqual([]);
    expect(h.send.mock.calls[1]).toEqual(['ptt_off', { target, originalEpoch: 4 }, 'off-queued']);
    h.radio = { ...h.radio, fieldStatus: { ...h.radio.fieldStatus, ptt: field(3) } };
    emit({ commandId: 'off-queued', kind: 'transport-sent', originalEpoch: 4, eventEpoch: 6 });
    expect(off[0]).toMatchObject({ outcome: 'sent', eventEpoch: 6, barrier: { authorityEpoch: 6 } });
    for (const [id, kind, outcome] of [['off-error', 'response-error', 'response-error'], ['off-throw', 'error', 'transport-error']] as const) {
      factory.dependencies.sendPtt('off', id, correlation, (report) => off.push(report));
      emit({ commandId: id, kind, originalEpoch: 4, eventEpoch: 6 });
      expect(off.at(-1)).toEqual({ outcome, eventEpoch: 6, barrier: null });
    }
    factory.dispose(); expect(h.deliveries.size).toBe(0);
  });
  it('supersedes a real off-confirmation timeout: the stale delivery is a no-op, the rearmed one resolves for real', async () => {
    const factory = createBrowserTxControllerDependencies();
    const baseline = factory.projectAuthority({ state: 'connected', epoch: 4 });
    const controller = new TxController(baseline.epoch, baseline.ptt.marker, factory.dependencies);
    h.send.mockImplementation((name, _params, id) => { if (name === 'ptt_on') emit({ commandId: id!, kind: 'transport-sent', originalEpoch: 4, eventEpoch: 4 }); return true; });
    h.radio = { ...h.radio, fieldStatus: { ...h.radio.fieldStatus, ptt: field(2) } };
    const started = factory.projectAuthority({ state: 'connected', epoch: 4 });
    controller.dispatch({ type: 'start', sourceId: 'test', leaseId: 'lease-stale', intent: 'momentary', eligibility: started.eligibility, ptt: started.ptt });
    await Promise.resolve(); await Promise.resolve();
    expect(controller.snapshot().phase).toBe('key-confirm-pending');
    // Release arms a REAL off-confirmation setTimeout (deadline t+5000).
    controller.dispatch({ type: 'release', guard: controller.snapshot().guard!, commandId: 'off-release' });
    const staleRevision = controller.snapshot().timerRevision.off;
    expect(controller.snapshot().phase).toBe('releasing');
    vi.advanceTimersByTime(100);
    // The OFF delivery arrives and rearms a SECOND real off-confirmation timer
    // (deadline (t+100)+5000) via bindOff — the FIRST real setTimeout is never
    // explicitly cancelled and keeps ticking toward its own, now-stale, deadline.
    emit({ commandId: 'off-release', kind: 'transport-sent', originalEpoch: 4, eventEpoch: 4 });
    expect(controller.snapshot().timerRevision.off).toBe(staleRevision + 1);
    expect(controller.snapshot().pendingOff?.deliveryEpoch).toBe(4);
    // Advance past the STALE timer's real deadline (t=5000) but short of the
    // rearmed one's (t=5100): its timer-fired dispatch must be a no-op.
    vi.advanceTimersByTime(4_950);
    expect(controller.snapshot()).toMatchObject({ phase: 'releasing', fault: null });
    expect(controller.snapshot().pendingOff?.deliveryEpoch).toBe(4);
    // Now the rearmed timer's own real deadline (t=5100) is crossed and its
    // matching-revision dispatch is the one that legitimately resolves release.
    vi.advanceTimersByTime(100);
    expect(controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'release-not-confirmed' });
  });
});
