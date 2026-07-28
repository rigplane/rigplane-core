import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TxController, type TxControllerDependencies } from '../controller';

type Event = Parameters<TxController['dispatch']>[0];
type State = ReturnType<TxController['snapshot']>;
type StartEvent = Extract<Event, { type: 'start' }>;
type Eligibility = StartEvent['eligibility'];
type Observation = StartEvent['ptt'];
type Marker = Observation['marker'];
type Command = Parameters<TxControllerDependencies['sendPtt']>[0];
type Report = Parameters<TxControllerDependencies['sendPtt']>[3];
type Send = { command: Command; report: Report };

const marker = (seq: number, epoch = 1): Marker => ({
  authorityEpoch: epoch,
  pttObservationSeq: seq,
  pttLastObservedMonotonic: seq,
});
const target = { receiver: 'MAIN', slot: 'A', frequencyHz: 14_074_000 } as const;
const allowed: Eligibility = {
  catPtt: true,
  browserTxAudio: true,
  controlLive: true,
  permit: 'allowed',
  target,
};
const ptt = (
  value: boolean,
  seq: number,
  epoch = 1,
  fresh = true,
  source: Observation['source'] = 'radio-readback',
): Observation => ({ value, observed: true, fresh, source, marker: marker(seq, epoch) });

function harness(options: { audio?: Promise<string | null>; throwOn?: Command } = {}) {
  const sends: Send[] = [];
  let id = 0;
  const dependencies: TxControllerDependencies = {
    startAudio: vi.fn(() => options.audio ?? Promise.resolve(null)),
    sendPtt: vi.fn((command, _id, _correlation, report) => {
      sends.push({ command, report });
      if (command === options.throwOn) throw new Error(`${command} transport closed`);
    }),
    stopLocalAudio: vi.fn(),
    restoreMod: vi.fn(),
    commandId: vi.fn((command) => `${command}-${++id}`),
    schedule: vi.fn((callback, delay) => setTimeout(callback, delay)),
    cancel: vi.fn((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>)),
    timeoutMs: { 'audio-start': 10, 'on-confirmation': 20, 'off-confirmation': 30 },
  };
  return { controller: new TxController(1, marker(0), dependencies), dependencies, sends };
}

type Harness = ReturnType<typeof harness>;
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
async function begin(h: Harness, eligibility = allowed, intent: StartEvent['intent'] = 'momentary') {
  h.controller.dispatch({ type: 'start', sourceId: 'desktop', leaseId: 'lease', intent, eligibility, ptt: ptt(false, 1) });
  await flush();
}
function report(h: Harness, command: Command, outcome: Parameters<Report>[0]['outcome'], seq: number | null, epoch = 1) {
  const send = [...h.sends].reverse().find((item) => item.command === command);
  expect(send).toBeDefined();
  send!.report({ outcome, eventEpoch: epoch, barrier: seq === null ? null : marker(seq, epoch) });
}
function authority(
  h: Harness,
  value: boolean,
  seq: number,
  eligibility = allowed,
  epoch = 1,
  fresh = true,
  source: Observation['source'] = 'radio-readback',
) {
  h.controller.dispatch({ type: 'authority', epoch, ptt: ptt(value, seq, epoch, fresh, source), eligibility, offCommandId: `off-e${epoch}` });
}
async function key(h: Harness) {
  await begin(h);
  report(h, 'on', 'sent', 2);
  authority(h, true, 3);
  expect(h.controller.snapshot().phase).toBe('active');
}
const commands = (h: Harness, command: Command) => h.sends.filter((item) => item.command === command);

describe('TxController public contract matrix', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('moves held to latched, coalesces lifecycle release, and cancels every completed deadline', async () => {
    const h = harness();
    await begin(h);
    expect(h.controller.snapshot()).toMatchObject({ intent: 'momentary', mayOwnKey: true, txRisk: 'uncertain' });
    h.controller.dispatch({ type: 'intent', sourceId: 'desktop', guard: h.controller.snapshot().guard!, intent: 'latched' });
    report(h, 'on', 'sent', 2);
    authority(h, true, 3);
    expect(h.controller.snapshot()).toMatchObject({ phase: 'active', intent: 'latched', radioTx: 'on' });
    h.controller.dispatch({ type: 'release', guard: h.controller.snapshot().guard!, commandId: 'off-pagehide' });
    const pending = h.controller.snapshot();
    expect(pending).toMatchObject({ phase: 'releasing', intent: null, pendingOff: { commandId: 'off-pagehide' } });
    h.controller.dispatch({ type: 'release', guard: pending.guard!, commandId: 'off-duplicate' });
    expect(commands(h, 'off')).toHaveLength(1);
    report(h, 'off', 'sent', 4);
    authority(h, false, 5);
    expect(h.controller.snapshot()).toMatchObject({ phase: 'idle', pendingOff: null, modRestorePending: false });
    expect(h.dependencies.restoreMod).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
    vi.runAllTimers();
    expect(h.controller.snapshot().phase).toBe('idle');
    // Kills replayed lifecycle release, duplicate OFF, and uncancelled deadline mutations.
  });

  it.each([
    ['receiver target', { ...allowed, target: { ...target, receiver: 'SUB' as const } }],
    ['frequency', { ...allowed, target: { ...target, frequencyHz: target.frequencyHz + 1 } }],
    ['permit', { ...allowed, permit: 'unknown' as const }],
  ])('releases an owned key when %s changes', async (_name, eligibility) => {
    const h = harness();
    await begin(h);
    authority(h, false, 2, eligibility);
    expect(h.controller.snapshot()).toMatchObject({ phase: 'releasing', pendingOff: { commandId: 'off-e1' } });
    expect(commands(h, 'off')).toHaveLength(1);
    // Kills eligibility-drift mutations that leave the App lease keyed.
  });

  it('accepts only fresh authority, fails on backend de-key, and never replays ON after external re-key', async () => {
    const h = harness();
    await begin(h);
    report(h, 'on', 'sent', 2);
    authority(h, true, 1);
    authority(h, true, 3, allowed, 1, false);
    expect(h.controller.snapshot().phase).toBe('key-confirm-pending');
    authority(h, true, 3);
    expect(h.controller.snapshot().phase).toBe('active');
    authority(h, false, 4, allowed, 1, true, 'backend-observation');
    expect(h.controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'backend-dekeyed', radioTx: 'off', mayOwnKey: false });
    authority(h, true, 5);
    expect(h.controller.snapshot()).toMatchObject({ phase: 'failed', radioTx: 'on', mayOwnKey: false });
    expect(commands(h, 'on')).toHaveLength(1);
    // Kills stale-read acceptance and lease reconstruction from external RF truth.
  });

  it('fails closed for unknown permit, audio failure, and rejected ON without losing the OFF obligation', async () => {
    const denied = harness();
    await begin(denied, { ...allowed, permit: 'unknown' });
    expect(denied.controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'not-eligible', mayOwnKey: false });
    expect(denied.dependencies.startAudio).not.toHaveBeenCalled();

    const audio = harness({ audio: Promise.resolve('device denied') });
    await begin(audio);
    expect(audio.controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'audio-failed', mayOwnKey: false });
    expect(audio.sends).toHaveLength(0);

    const keyFailure = harness();
    await begin(keyFailure);
    report(keyFailure, 'on', 'response-error', null);
    expect(keyFailure.controller.snapshot()).toMatchObject({ phase: 'releasing', fault: 'on-command-failed', pendingOff: {} });
    expect(commands(keyFailure, 'off')).toHaveLength(1);
    // Kills unknown-as-allowed, audio-continues-to-ON, and rejected-ON-drops-OFF mutations.
  });

  it('rebinds a queued OFF to the new delivery epoch before accepting de-key evidence', async () => {
    const h = harness();
    await key(h);
    h.controller.dispatch({ type: 'release', guard: h.controller.snapshot().guard!, commandId: 'off-queued' });
    h.controller.dispatch({ type: 'epoch', epoch: 2, baseline: marker(1, 2), offCommandId: 'off-e2' });
    authority(h, false, 2, allowed, 2);
    expect(h.controller.snapshot().phase).toBe('releasing');
    report(h, 'off', 'sent', 2, 2);
    expect(h.controller.snapshot().pendingOff).toMatchObject({ deliveryEpoch: 2, deliveryRebound: true });
    authority(h, false, 3, allowed, 2);
    expect(h.controller.snapshot().phase).toBe('idle');
    expect(h.dependencies.restoreMod).toHaveBeenCalledTimes(1);
    // Kills original-epoch binding and pre-delivery OFF acceptance.
  });

  it('keeps release failed and MOD unrestored when OFF dispatch throws', async () => {
    const h = harness({ throwOn: 'off' });
    await key(h);
    h.controller.dispatch({ type: 'release', guard: h.controller.snapshot().guard!, commandId: 'off-fail' });
    expect(h.controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'release-not-confirmed', radioTx: 'on', txRisk: 'confirmed-on', modRestorePending: true });
    expect(h.dependencies.stopLocalAudio).toHaveBeenCalledTimes(1);
    expect(h.dependencies.restoreMod).not.toHaveBeenCalled();
    // Kills false-idle and speculative MOD restoration after failed physical de-key dispatch.
  });

  it('reports uncertain RF honestly and restores MOD only after post-delivery authoritative OFF', async () => {
    const h = harness();
    await begin(h);
    expect(h.controller.snapshot()).toMatchObject({ radioTx: 'off', txRisk: 'uncertain', mayOwnKey: true });
    h.controller.dispatch({ type: 'release', guard: h.controller.snapshot().guard!, commandId: 'off-uncertain' });
    expect(h.controller.snapshot()).toMatchObject({ phase: 'releasing', txRisk: 'uncertain', modRestorePending: true });
    report(h, 'off', 'sent', 2);
    authority(h, false, 2);
    expect(h.controller.snapshot().phase).toBe('releasing');
    expect(h.dependencies.restoreMod).not.toHaveBeenCalled();
    authority(h, false, 3);
    expect(h.controller.snapshot()).toMatchObject({ phase: 'idle', radioTx: 'off', txRisk: 'none', modRestorePending: false });
    expect(h.dependencies.restoreMod).toHaveBeenCalledWith(marker(2), ptt(false, 3));
    // Kills optimistic RF truth and MOD restore at or before the delivery barrier.
  });
});
