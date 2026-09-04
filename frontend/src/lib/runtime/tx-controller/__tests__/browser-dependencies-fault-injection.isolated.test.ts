import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ManagedTxController, type ManagedTxDependencies } from '../managed-controller';
import type { ManagedTxState } from '../managed-state';

const state = (fresh = true): ManagedTxState => ({
  phase: 'idle', intent: null, radioTx: fresh ? 'off' : 'unknown',
  txRisk: fresh ? 'none' : 'uncertain', fault: null, faultDetail: null, fresh,
  releaseRequired: false, remainingMs: null, lastOperation: null,
});
const h = { start: vi.fn<() => Promise<string | null>>(), stop: vi.fn(),
  sendPtt: vi.fn<() => Promise<'accepted' | 'rejected'>>(async () => 'accepted'),
  submit: vi.fn(async () => 'accepted' as const),
  projected: state(), audioDied: () => {} };
function controller(): ManagedTxController {
  const dependencies: ManagedTxDependencies = {
    snapshot: () => h.projected, refresh: vi.fn(async () => {}), invalidate: vi.fn(),
    sendPtt: h.sendPtt, submit: h.submit, startAudio: h.start,
    stopLocalAudio: h.stop, onAudioDied: (handler) => { h.audioDied = handler; return vi.fn(); },
  };
  return new ManagedTxController(dependencies);
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

beforeEach(() => {
  h.projected = state(); h.start.mockReset().mockResolvedValue(null); h.stop.mockReset();
  h.sendPtt.mockReset().mockResolvedValue('accepted'); h.submit.mockReset().mockResolvedValue('accepted');
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
});
