import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ManagedTransmitDocument } from '$lib/types/managed-transmit';
import { TxController, type TxControllerDependencies } from '../controller';
import { projectManagedTransmit, type TxState } from '../model';

const document = (
  intent: 'rx' | 'transmit' | 'ptt',
  observedPtt: 'on' | 'off' | 'unknown',
  options: { releaseRequired?: boolean; lastError?: string | null } = {},
): ManagedTransmitDocument => ({
  schemaVersion: 1,
  sampledAt: '2026-09-04T12:00:00Z',
  managedTransmit: {
    status: 'available',
    intent: intent === 'ptt' ? { kind: 'ptt', owner: 'operator' } : { kind: intent },
    releaseRequired: options.releaseRequired ?? false,
    lastError: options.lastError ?? null,
    lastActuation: null,
    abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
  },
  txObservation: { observedPtt },
});

const state = (value: ManagedTransmitDocument | null, stale = false): TxState =>
  projectManagedTransmit(value, stale);

function harness(initial = state(document('rx', 'off'))) {
  let current = initial;
  let managedOutcome: 'accepted' | 'rejected' = 'accepted';
  let pttOutcome: 'accepted' | 'rejected' = 'accepted';
  let audioDied = () => {};
  const dependencies: TxControllerDependencies = {
    snapshot: vi.fn(() => current),
    refresh: vi.fn(async () => {}),
    invalidate: vi.fn(() => { current = state(null, true); }),
    sendPtt: vi.fn(async () => pttOutcome),
    submit: vi.fn(async () => managedOutcome),
    startAudio: vi.fn(async () => null),
    stopLocalAudio: vi.fn(),
    onAudioDied: vi.fn((handler) => { audioDied = handler; return vi.fn(); }),
  };
  return {
    controller: new TxController(dependencies),
    dependencies,
    observe(next: TxState) { current = next; },
    rejectManaged() { managedOutcome = 'rejected'; },
    rejectPtt() { pttOutcome = 'rejected'; },
    audioDied() { audioDied(); },
  };
}

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

describe('browser managed-transmit thin client', () => {
  afterEach(() => vi.useRealTimers());

  it('awaits local media before momentary PTT ON and uses ordinary PTT OFF', async () => {
    vi.useFakeTimers();
    let resolveAudio!: (error: string | null) => void;
    const h = harness();
    vi.mocked(h.dependencies.startAudio).mockReturnValueOnce(
      new Promise((resolve) => { resolveAudio = resolve; }),
    );

    h.controller.requestPtt();
    await flush();
    expect(h.dependencies.startAudio).toHaveBeenCalledOnce();
    expect(h.dependencies.sendPtt).not.toHaveBeenCalled();

    resolveAudio(null);
    await flush();
    expect(h.dependencies.sendPtt).toHaveBeenCalledWith('ptt_on');
    expect(h.controller.snapshot()).toEqual(state(document('rx', 'off')));
    expect(vi.getTimerCount()).toBe(0);

    await h.controller.releasePtt();
    expect(h.dependencies.sendPtt).toHaveBeenLastCalledWith('ptt_off');
    expect(h.dependencies.submit).not.toHaveBeenCalled();
  });

  it('suppresses late PTT ON when release wins the media-prearm race', async () => {
    let resolveAudio!: (error: string | null) => void;
    const h = harness();
    vi.mocked(h.dependencies.startAudio).mockReturnValueOnce(
      new Promise((resolve) => { resolveAudio = resolve; }),
    );

    h.controller.requestPtt();
    const release = h.controller.releasePtt();
    resolveAudio(null);
    await release;
    await flush();

    expect(h.dependencies.sendPtt).not.toHaveBeenCalled();
    expect(h.dependencies.stopLocalAudio).toHaveBeenCalledOnce();
  });

  it('awaits media before latched TRANSMIT and refuses ON on media failure', async () => {
    const h = harness();
    vi.mocked(h.dependencies.startAudio).mockResolvedValueOnce('microphone denied');

    h.controller.requestTransmit();
    await flush();

    expect(h.dependencies.submit).not.toHaveBeenCalled();
    expect(h.controller.snapshot()).toEqual(state(document('rx', 'off')));
    expect(h.dependencies.stopLocalAudio).toHaveBeenCalledOnce();
  });

  it('keeps terminal rejection out of display state and cleans local media', async () => {
    const h = harness();
    h.rejectPtt();

    h.controller.requestPtt();
    await flush();

    expect(h.controller.snapshot()).toEqual(state(document('rx', 'off')));
    expect(h.dependencies.stopLocalAudio).toHaveBeenCalledOnce();
  });

  it('keeps explicit ForceOFF distinct and waits for server projection', async () => {
    const h = harness(state(document('transmit', 'on')));

    const release = h.controller.forceOff();
    expect(h.controller.snapshot().phase).toBe('active');
    await release;
    expect(h.dependencies.submit).toHaveBeenCalledWith('force_off');
    expect(h.dependencies.stopLocalAudio).not.toHaveBeenCalled();
    expect(h.controller.snapshot().phase).toBe('active');

    h.observe(state(document('transmit', 'on', { releaseRequired: true })));
    await h.controller.refresh();
    expect(h.controller.snapshot().phase).toBe('active');

    h.observe(state(document('rx', 'off', { releaseRequired: true })));
    await h.controller.refresh();
    expect(h.controller.snapshot()).toMatchObject({ phase: 'releasing', radioTx: 'off' });
  });

  it('stops local media and ForceOFFs after audio death', async () => {
    const h = harness();
    h.controller.requestTransmit();
    await flush();
    h.audioDied();
    await flush();

    expect(h.dependencies.submit).toHaveBeenCalledWith('force_off');
    expect(h.dependencies.stopLocalAudio).toHaveBeenCalledOnce();
  });

  it('preserves latched TRANSMIT across session lifecycle release', async () => {
    const h = harness();
    h.controller.requestTransmit();
    await flush();

    await h.controller.releaseSession();

    expect(h.dependencies.submit).toHaveBeenCalledTimes(1);
    expect(h.dependencies.submit).toHaveBeenCalledWith('transmit_on');
    expect(h.dependencies.stopLocalAudio).toHaveBeenCalledOnce();
  });

  it('fails closed on reconnect invalidation without reconstructing browser state', () => {
    const h = harness(state(document('ptt', 'on')));
    expect(h.controller.snapshot()).toMatchObject({ phase: 'active', intent: 'momentary' });

    h.controller.invalidate();

    expect(h.controller.snapshot()).toMatchObject({
      phase: 'idle', intent: null, radioTx: 'unknown',
    });
  });

  it('projects server release and failure fields without a second resolver', () => {
    expect(state(document('transmit', 'unknown'))).toMatchObject({
      phase: 'key-confirm-pending', txRisk: 'uncertain', fault: null,
    });
    expect(state(document('transmit', 'on', { releaseRequired: true }))).toMatchObject({
      phase: 'active', radioTx: 'on', txRisk: 'confirmed-on',
    });
    expect(state(document('rx', 'off', { releaseRequired: true }))).toMatchObject({
      phase: 'releasing', radioTx: 'off',
    });
    expect(state(document('rx', 'off', { lastError: 'server-release-failed' }))).toMatchObject({
      phase: 'failed', radioTx: 'off', fault: 'server-release-failed',
    });
  });
});
