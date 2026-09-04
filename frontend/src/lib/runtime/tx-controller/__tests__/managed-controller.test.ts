import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ManagedTxController, type ManagedTxDependencies } from '../managed-controller';
import type { ManagedTxState } from '../managed-state';

const state = (overrides: Partial<ManagedTxState> = {}): ManagedTxState => ({
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none',
  fault: null, faultDetail: null, fresh: true, releaseRequired: false, remainingMs: null,
  lastOperation: null,
  ...overrides,
});
const deferred = <T>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((r) => { resolve = r; }); return { promise, resolve }; };

describe('ManagedTxController', () => {
  let projected: ManagedTxState;
  let audioDied!: () => void;
  let dependencies: ManagedTxDependencies;

  beforeEach(() => {
    projected = state();
    dependencies = {
      snapshot: () => projected,
      refresh: vi.fn(async () => {}),
      invalidate: vi.fn(),
      sendPtt: vi.fn(async () => 'accepted' as const),
      submit: vi.fn(async () => 'accepted' as const),
      startAudio: vi.fn(async () => null),
      stopLocalAudio: vi.fn(),
      onAudioDied: vi.fn((handler) => { audioDied = handler; return vi.fn(); }),
    };
  });

  it('awaits media before the exact WS PTT pair and does not infer radio state', async () => {
    const audio = deferred<string | null>();
    dependencies.startAudio = vi.fn(() => audio.promise);
    const controller = new ManagedTxController(dependencies);
    controller.pttOn();
    expect(dependencies.sendPtt).not.toHaveBeenCalled();
    expect(controller.snapshot()).toEqual(state());
    audio.resolve(null);
    await vi.waitFor(() => expect(dependencies.sendPtt).toHaveBeenCalledWith('ptt_on'));
    await controller.pttOff();
    expect(dependencies.sendPtt).toHaveBeenNthCalledWith(2, 'ptt_off');
    expect(dependencies.submit).not.toHaveBeenCalled();
    expect(dependencies.stopLocalAudio).toHaveBeenCalledTimes(1);
  });

  it('suppresses a late ON and refuses ON when media prearm fails', async () => {
    const audio = deferred<string | null>();
    dependencies.startAudio = vi.fn(() => audio.promise);
    const controller = new ManagedTxController(dependencies);
    controller.pttOn();
    await controller.pttOff();
    audio.resolve(null);
    await Promise.resolve(); await Promise.resolve();
    expect(dependencies.sendPtt).not.toHaveBeenCalled();
    expect(dependencies.stopLocalAudio).toHaveBeenCalledTimes(1);

    dependencies.startAudio = vi.fn(async () => 'microphone denied');
    const refused = new ManagedTxController(dependencies);
    refused.pttOn();
    await vi.waitFor(() => expect(dependencies.startAudio).toHaveBeenCalled());
    expect(dependencies.sendPtt).not.toHaveBeenCalled();
  });

  it('uses HTTP only for TRANSMIT/ForceOFF, rejects stale ON, and always permits ForceOFF', async () => {
    projected = state({ fresh: false, radioTx: 'unknown' });
    const controller = new ManagedTxController(dependencies);
    controller.transmitOn();
    await Promise.resolve();
    expect(dependencies.startAudio).not.toHaveBeenCalled();
    expect(dependencies.submit).not.toHaveBeenCalled();
    await controller.forceOff();
    expect(dependencies.submit).toHaveBeenCalledWith('force_off');
    projected = state();
    controller.transmitOn();
    await vi.waitFor(() => expect(dependencies.submit).toHaveBeenCalledWith('transmit_on'));
    expect(dependencies.sendPtt).not.toHaveBeenCalled();
  });

  it('converts an admitted momentary lease to TRANSMIT only after terminal WS OFF', async () => {
    const released = deferred<'accepted' | 'rejected'>();
    dependencies.sendPtt = vi.fn((operation) => operation === 'ptt_off'
      ? released.promise
      : Promise.resolve('accepted' as const));
    const controller = new ManagedTxController(dependencies);
    controller.pttOn();
    await vi.waitFor(() => expect(dependencies.sendPtt).toHaveBeenCalledWith('ptt_on'));
    controller.transmitOn();
    await vi.waitFor(() => expect(dependencies.sendPtt).toHaveBeenCalledWith('ptt_off'));
    expect(dependencies.submit).not.toHaveBeenCalled();
    released.resolve('accepted');
    await vi.waitFor(() => expect(dependencies.submit).toHaveBeenCalledWith('transmit_on'));
    expect(dependencies.sendPtt).toHaveBeenCalledTimes(2);
  });

  it('cleans media on server rejection and audio death', async () => {
    dependencies.sendPtt = vi.fn(async () => 'rejected' as const);
    const controller = new ManagedTxController(dependencies);
    controller.pttOn();
    await vi.waitFor(() => expect(dependencies.stopLocalAudio).toHaveBeenCalledTimes(1));
    audioDied();
    await vi.waitFor(() => expect(dependencies.submit).toHaveBeenCalledWith('force_off'));
    expect(dependencies.stopLocalAudio).toHaveBeenCalledTimes(1);
  });

  it('coalesces lifecycle ForceOFF while a started TRANSMIT cleanup obligation remains', async () => {
    const force = deferred<'accepted' | 'rejected'>();
    dependencies.submit = vi.fn((operation) => operation === 'force_off' ? force.promise : Promise.resolve('accepted' as const));
    const controller = new ManagedTxController(dependencies);
    controller.transmitOn();
    await vi.waitFor(() => expect(dependencies.submit).toHaveBeenCalledWith('transmit_on'));
    const first = controller.releaseSession();
    const second = controller.releaseSession();
    expect(dependencies.submit).toHaveBeenCalledTimes(2);
    force.resolve('accepted');
    await Promise.all([first, second]);
    expect(dependencies.submit).toHaveBeenCalledTimes(2);
    projected = state({ lastOperation: 'transmit_on' });
    await controller.refresh(); await controller.releaseSession();
    expect(dependencies.submit).toHaveBeenCalledTimes(3);
    projected = state({ lastOperation: 'force_receive' });
    await controller.refresh(); await controller.releaseSession();
    expect(dependencies.submit).toHaveBeenCalledTimes(3);
  });

  it('ForceOFFs fresh canonical TRANSMIT on teardown without local ownership truth', async () => {
    projected = state({ intent: 'latched', radioTx: 'on', phase: 'active' });
    const controller = new ManagedTxController(dependencies);
    await controller.releaseSession();
    expect(dependencies.submit).toHaveBeenCalledWith('force_off');
  });
});
