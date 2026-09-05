import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ManagedTxController, type ManagedTxDependencies, type ManagedOperation, type PttOperation } from '../managed-controller';
import type { ManagedTxState } from '../managed-state';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

const state = (fresh = true): ManagedTxState => ({
  phase: 'idle', intent: null, radioTx: fresh ? 'off' : 'unknown',
  txRisk: fresh ? 'none' : 'uncertain', fault: null, faultDetail: null, fresh,
  releaseRequired: false, configuredSeconds: fresh ? 180 : null,
  remainingMs: null, lastOperation: null,
});
const h = { start: vi.fn<() => Promise<string | null>>(), stop: vi.fn(),
  sendPtt: vi.fn<(operation: PttOperation) => Promise<'accepted' | 'rejected'>>(async () => 'accepted'),
  submit: vi.fn<(operation: ManagedOperation) => Promise<'accepted' | 'rejected'>>(async () => 'accepted'),
  setTot: vi.fn(async () => {}),
  projected: state(), audioDied: () => {} };
function controller(overrides: Partial<ManagedTxDependencies> = {}): ManagedTxController {
  const dependencies: ManagedTxDependencies = {
    snapshot: () => h.projected, refresh: vi.fn(async () => {}), invalidate: vi.fn(),
    sendPtt: h.sendPtt, submit: h.submit, setTot: h.setTot, startAudio: h.start,
    stopLocalAudio: h.stop, onAudioDied: (handler) => { h.audioDied = handler; return vi.fn(); },
    ...overrides,
  };
  return new ManagedTxController(dependencies);
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

beforeEach(() => {
  h.projected = state(); h.start.mockReset().mockResolvedValue(null); h.stop.mockReset();
  h.sendPtt.mockReset().mockResolvedValue('accepted'); h.submit.mockReset().mockResolvedValue('accepted');
  h.setTot.mockReset().mockResolvedValue(undefined);
});

describe('managed browser TX fault injection', () => {
  it.each([
    ['rejects', () => Promise.reject(new Error('mic denied'))],
    ['returns an error', () => Promise.resolve('mic denied')],
  ])('fails closed when media pre-arm %s', async (_label, failure) => {
    h.start.mockImplementationOnce(failure);
    controller().pttOn();
    await flush();
    expect(h.sendPtt).not.toHaveBeenCalled();
    expect(h.stop).toHaveBeenCalledTimes(1);
  });

  it('stops media when terminal server admission rejects ON', async () => {
    h.sendPtt.mockResolvedValueOnce('rejected');
    controller().pttOn();
    await vi.waitFor(() => expect(h.stop).toHaveBeenCalledTimes(1));
  });

  it('blocks TRANSMIT on stale canonical state but never blocks emergency ForceOFF', async () => {
    h.projected = state(false);
    const tx = controller();
    tx.transmitOn();
    await flush();
    expect(h.start).not.toHaveBeenCalled();
    expect(h.submit).not.toHaveBeenCalled();
    await tx.forceOff();
    expect(h.submit).toHaveBeenCalledExactlyOnceWith('force_off');
  });

  it('audio death cleans local media and submits exactly one ForceOFF', async () => {
    const tx = controller();
    tx.pttOn();
    await flush();
    h.audioDied();
    await vi.waitFor(() => expect(h.submit).toHaveBeenCalledExactlyOnceWith('force_off'));
    expect(h.stop).toHaveBeenCalledTimes(1);
  });

  it('intentional AudioManager.stopTx during session release does not emit audio death or ForceOFF', async () => {
    const { TxMic } = await import('$lib/audio/tx-mic');
    const { audioManager } = await import('$lib/audio/audio-manager');
    const startMic = vi.spyOn(TxMic.prototype, 'start').mockResolvedValue(null);
    const audioDied = vi.fn();
    instances.length = 0;
    vi.stubGlobal('WebSocket', MockWebSocket);
    const tx = controller({
      startAudio: () => audioManager.startTx(),
      stopLocalAudio: () => audioManager.stopTx(),
      onAudioDied: (handler) => audioManager.onTxAudioDied(() => { audioDied(); handler(); }),
    });
    try {
      tx.transmitOn();
      await vi.waitFor(() => expect(h.submit).toHaveBeenCalledExactlyOnceWith('transmit_on'));
      expect(audioManager.txEnabled).toBe(true);
      instances[0].simulateOpen();
      await tx.releaseSession();
      expect(audioManager.txEnabled).toBe(false);
      expect(audioDied).not.toHaveBeenCalled();
      expect(h.submit).toHaveBeenCalledExactlyOnceWith('transmit_on');
    } finally {
      tx.dispose();
      audioManager.stopTx();
      startMic.mockRestore();
      vi.unstubAllGlobals();
    }
  });

  it.each(['ptt', 'transmit'] as const)(
    'session release cancels deferred %s audio preparation without a late ON', async (intent) => {
    let ready!: (error: string | null) => void;
    h.start.mockImplementationOnce(() => new Promise((resolve) => { ready = resolve; }));
    const tx = controller();
    if (intent === 'ptt') tx.pttOn(); else tx.transmitOn();
    await tx.releaseSession();
    ready(null);
    await flush();
    expect(h.sendPtt).not.toHaveBeenCalled();
    expect(h.submit).not.toHaveBeenCalled();
    expect(h.stop).toHaveBeenCalledTimes(1);
  });

  it.each(['accepted', 'rejected'] as const)(
    'session release leaves already submitted TRANSMIT admission to the server when it later settles %s', async (outcome) => {
    let admitted!: (result: 'accepted' | 'rejected') => void;
    h.submit.mockImplementationOnce(() => new Promise((resolve) => { admitted = resolve; }));
    const tx = controller();
    tx.transmitOn();
    await flush();
    expect(h.submit).toHaveBeenCalledExactlyOnceWith('transmit_on');
    await tx.releaseSession();
    admitted(outcome);
    await flush();
    expect(h.submit).toHaveBeenCalledExactlyOnceWith('transmit_on');
    expect(h.sendPtt).not.toHaveBeenCalled();
    expect(h.stop).toHaveBeenCalledTimes(1);
  });

  it.each(['accepted', 'rejected'] as const)(
    'session release sends owner PTT_OFF while PTT_ON admission later settles %s', async (outcome) => {
    let admitted!: (result: 'accepted' | 'rejected') => void;
    h.sendPtt.mockImplementationOnce(() => new Promise((resolve) => { admitted = resolve; }));
    const tx = controller();
    tx.pttOn();
    await flush();
    await tx.releaseSession();
    admitted(outcome);
    await flush();
    expect(h.sendPtt.mock.calls).toEqual([['ptt_on'], ['ptt_off']]);
    expect(h.submit).not.toHaveBeenCalled();
    expect(h.stop).toHaveBeenCalledTimes(1);
  });

  it.each(['accepted', 'rejected'] as const)(
    'release during PTT-to-TRANSMIT handoff cancels the unsent latch after PTT_OFF settles %s', async (outcome) => {
    let released!: (result: 'accepted' | 'rejected') => void;
    const tx = controller();
    tx.pttOn();
    await flush();
    h.sendPtt.mockImplementationOnce(() => new Promise((resolve) => { released = resolve; }));
    tx.transmitOn();
    await flush();
    expect(h.sendPtt.mock.calls).toEqual([['ptt_on'], ['ptt_off']]);
    await tx.releaseSession();
    released(outcome);
    await flush();
    expect(h.submit).not.toHaveBeenCalled();
    expect(h.sendPtt.mock.calls.map(([operation]) => operation))
      .toEqual(['ptt_on', 'ptt_off', 'ptt_off']);
    expect(h.stop).toHaveBeenCalledTimes(1);
  });
});
