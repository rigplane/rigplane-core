import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';

const h = vi.hoisted(() => ({
  deliveries: new Set<(event: CommandDeliveryEvent) => void>(),
  sessions: new Set<(event: ControlSessionTransition) => void>(),
  send: vi.fn((_name: string, _params: Record<string, unknown>, _id?: string) => true),
  refresh: vi.fn(async () => {}),
  invalidate: vi.fn(),
  submit: vi.fn(async () => 'accepted' as const),
  start: vi.fn(async () => null as string | null),
  stop: vi.fn(),
  audioDied: new Set<() => void>(),
  ids: 0,
}));

vi.mock('$lib/stores/managed-transmit.svelte', () => ({
  managedTransmitSnapshot: () => null,
  managedTransmitIsStale: () => true,
  refreshManagedTransmit: h.refresh,
  invalidateManagedTransmit: h.invalidate,
  submitManagedTransmit: h.submit,
}));
vi.mock('$lib/types/protocol', () => ({ makeCommandId: () => `cmd-${++h.ids}` }));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({
  getTxAudioControl: () => ({
    startTx: h.start,
    stopLocalAudio: h.stop,
    onTxAudioDied: (handler: () => void) => {
      h.audioDied.add(handler);
      return () => h.audioDied.delete(handler);
    },
  }),
}));
vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: h.send,
  onCommandDelivery: (handler: (event: CommandDeliveryEvent) => void) => {
    h.deliveries.add(handler);
    return () => h.deliveries.delete(handler);
  },
  onControlSessionTransition: (handler: (event: ControlSessionTransition) => void) => {
    h.sessions.add(handler);
    return () => h.sessions.delete(handler);
  },
}));

import { createBrowserTxControllerDependencies } from '../browser-dependencies';

const emit = (event: CommandDeliveryEvent) => {
  for (const handler of [...h.deliveries]) handler(event);
};

beforeEach(() => {
  h.deliveries.clear();
  h.sessions.clear();
  h.audioDied.clear();
  h.send.mockReset().mockReturnValue(true);
  h.refresh.mockReset().mockResolvedValue(undefined);
  h.invalidate.mockClear();
  h.submit.mockClear();
  h.start.mockClear();
  h.stop.mockClear();
  h.ids = 0;
});

describe('browser thin-client dependencies', () => {
  it('resolves PTT from terminal server response rather than enqueue', async () => {
    const factory = createBrowserTxControllerDependencies();
    let settled = false;
    const result = factory.dependencies.sendPtt('ptt_on').then((value) => {
      settled = true;
      return value;
    });

    expect(h.send).toHaveBeenCalledWith('ptt_on', {}, 'cmd-1');
    await Promise.resolve();
    expect(settled).toBe(false);
    emit({ commandId: 'cmd-1', kind: 'ack', originalEpoch: 1, eventEpoch: 1 });
    await Promise.resolve();
    expect(settled).toBe(false);
    emit({ commandId: 'cmd-1', kind: 'response-ok', originalEpoch: 1, eventEpoch: 1 });

    await expect(result).resolves.toBe('accepted');
    expect(h.refresh).toHaveBeenCalledOnce();
    expect(h.deliveries.size).toBe(0);
  });

  it('maps server rejection and immediate offline PTT ON refusal', async () => {
    const factory = createBrowserTxControllerDependencies();
    const rejected = factory.dependencies.sendPtt('ptt_on');
    emit({ commandId: 'cmd-1', kind: 'response-error', originalEpoch: 1, eventEpoch: 1 });
    await expect(rejected).resolves.toBe('rejected');

    h.send.mockReturnValue(false);
    await expect(factory.dependencies.sendPtt('ptt_on')).resolves.toBe('rejected');
    expect(h.deliveries.size).toBe(0);
  });

  it('keeps OFF queued for terminal delivery and removes all subscriptions on dispose', async () => {
    const factory = createBrowserTxControllerDependencies();
    h.send.mockReturnValue(false);
    const release = factory.dependencies.sendPtt('ptt_off');
    expect(h.deliveries.size).toBe(1);

    emit({ commandId: 'cmd-1', kind: 'response-ok', originalEpoch: 1, eventEpoch: 2 });
    await expect(release).resolves.toBe('accepted');
    factory.subscribeSession(vi.fn());
    expect(h.sessions.size).toBe(1);

    factory.dispose();
    expect(h.deliveries.size).toBe(0);
    expect(h.sessions.size).toBe(0);
    expect(h.stop).not.toHaveBeenCalled();
  });
});
