import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ServerState } from '../../types/state';

let acceptedState: ServerState | null = null;
const stateListeners = new Set<(state: ServerState | null) => void>();
const emitState = (state: ServerState): void => {
  acceptedState = state;
  for (const listener of stateListeners) listener(state);
};

describe('command lifecycle store', () => {
  let store: typeof import('../commands.svelte');

  beforeEach(async () => {
    vi.useFakeTimers();
    vi.resetModules();
    acceptedState = null;
    stateListeners.clear();
    vi.doMock('../radio.svelte', () => ({
      getRadioState: () => acceptedState,
      subscribeRadioState: (listener: (state: ServerState | null) => void) => {
        stateListeners.add(listener); listener(acceptedState);
        return () => stateListeners.delete(listener);
      },
    }));
    store = await import('../commands.svelte');
  });

  afterEach(() => {
    store.resetCommandLifecycle();
    vi.doUnmock('../radio.svelte');
    vi.useRealTimers();
  });

  it('records only bounded diagnostic lifecycle metadata', () => {
    const command = store.beginCommand({
      id: 'freq-1',
      name: 'set_freq',
      params: { freq: 14_074_000, receiver: 0 },
      originalEpoch: 7,
    });

    expect(command).toMatchObject({
      id: 'freq-1',
      name: 'set_freq',
      params: { freq: 14_074_000, receiver: 0 },
      originalEpoch: 7,
      status: 'pending',
      timeoutMs: 5_000,
    });
    expect(command).not.toHaveProperty('confirmedValue');
    expect(command).not.toHaveProperty('radioState');
    expect(store.getCommandLifecycle('freq-1', 7)?.status).toBe('pending');
    expect(store.hasPendingCommands()).toBe(true);
    expect(() => store.beginCommand({ ...command })).toThrow(/duplicate command id/i);
  });

  it('captures unresolved provider identity once and never backfills it on acknowledgement', () => {
    const command = store.beginCommand({
      id: 'unresolved-provider',
      name: 'set_filter_width',
      params: { width: 2_400, receiver: 0 },
      originalEpoch: 7,
    });

    expect(command.providerGeneration).toBeNull();
    store.acknowledgeCommand(command.id, 7, 7);
    expect(store.getCommandLifecycle(command.id, 7)?.providerGeneration).toBeNull();
  });

  it('fences acknowledgement and failure by command id and originating epoch', () => {
    store.beginCommand({ id: 'same', name: 'set_mode', params: { mode: 'USB' }, originalEpoch: 3 });
    store.acknowledgeCommand('same', 2, 3);
    expect(store.getCommandLifecycle('same', 3)?.status).toBe('pending');

    store.acknowledgeCommand('same', 3, 4);
    expect(store.getCommandLifecycle('same', 3)).toMatchObject({
      status: 'acknowledged',
      eventEpoch: 4,
    });
    store.failCommand('same', 3, 4, 'late NAK');
    expect(store.getCommandLifecycle('same', 3)?.status).toBe('failed');

    store.beginCommand({ id: 'failure', name: 'set_vfo', params: { vfo: 'B' }, originalEpoch: 5 });
    store.failCommand('failure', 5, 5, 'denied');
    expect(store.getCommandLifecycle('failure', 5)).toMatchObject({
      status: 'failed',
      error: 'denied',
    });
  });

  it('cancels all pending work from the disconnected session and ignores stale results', () => {
    store.beginCommand({ id: 'old-a', name: 'set_freq', params: { freq: 1 }, originalEpoch: 10 });
    store.beginCommand({ id: 'old-b', name: 'set_mode', params: { mode: 'CW' }, originalEpoch: 10 });
    store.beginCommand({ id: 'new', name: 'set_filter', params: { filter: 2 }, originalEpoch: 11 });

    store.cancelPendingCommands(10, 'session-disconnected');
    expect(store.getCommandLifecycle('old-a', 10)?.status).toBe('cancelled');
    expect(store.getCommandLifecycle('old-b', 10)?.status).toBe('cancelled');
    expect(store.getCommandLifecycle('new', 11)?.status).toBe('pending');

    store.acknowledgeCommand('old-a', 10, 11);
    expect(store.getCommandLifecycle('old-a', 10)?.status).toBe('cancelled');
  });

  it('uses the existing per-record timeout and isolates it from later commands', () => {
    store.beginCommand({ id: 'slow', name: 'set_freq', params: { freq: 1 }, originalEpoch: 1, timeoutMs: 25 });
    vi.advanceTimersByTime(10);
    store.beginCommand({ id: 'fresh', name: 'set_freq', params: { freq: 2 }, originalEpoch: 1, timeoutMs: 25 });
    vi.advanceTimersByTime(15);

    expect(store.getCommandLifecycle('slow', 1)?.status).toBe('timed-out');
    expect(store.getCommandLifecycle('fresh', 1)?.status).toBe('pending');
  });

  it('keeps an acknowledged command reactively awaiting confirmation until its deadline', () => {
    store.beginCommand({ id: 'awaiting', name: 'set_filter', params: { filter: 2 }, originalEpoch: 4, timeoutMs: 25 });
    vi.advanceTimersByTime(10);

    store.acknowledgeCommand('awaiting', 4, 4);
    expect(store.getCommandLifecycle('awaiting', 4)?.status).toBe('acknowledged');
    vi.advanceTimersByTime(24);
    expect(store.getCommandLifecycle('awaiting', 4)?.status).toBe('acknowledged');
    vi.advanceTimersByTime(1);
    expect(store.getCommandLifecycle('awaiting', 4)?.status).toBe('timed-out');
  });

  it('retains a late acknowledged failure briefly, then retires it without a radio-state push', () => {
    store.beginCommand({ id: 'late-failure', name: 'set_mode', params: { mode: 'CW' }, originalEpoch: 4, timeoutMs: 25 });
    store.acknowledgeCommand('late-failure', 4, 4);
    store.failCommand('late-failure', 4, 4, 'backend rejected');

    expect(store.getCommandLifecycle('late-failure', 4)).toMatchObject({ status: 'failed', error: 'backend rejected' });
    vi.advanceTimersByTime(4_999);
    expect(store.getCommandLifecycle('late-failure', 4)?.status).toBe('failed');
    vi.advanceTimersByTime(1);
    expect(store.getCommandLifecycle('late-failure', 4)).toBeUndefined();
  });

  it('cancels acknowledged confirmation waits and isolates their deadlines from newer records', () => {
    store.beginCommand({ id: 'old', name: 'set_freq', params: { freq: 1 }, originalEpoch: 8, timeoutMs: 25 });
    store.acknowledgeCommand('old', 8, 8);
    store.beginCommand({ id: 'new', name: 'set_freq', params: { freq: 2 }, originalEpoch: 8, timeoutMs: 25 });

    store.cancelPendingCommands(8);
    expect(store.getCommandLifecycle('old', 8)?.status).toBe('cancelled');
    expect(store.getCommandLifecycle('new', 8)?.status).toBe('cancelled');
    vi.advanceTimersByTime(25);
    expect(store.getCommandLifecycle('old', 8)?.status).toBe('cancelled');
  });

  it('exposes confirmation as an explicit lifecycle transition without mutating radio truth', () => {
    store.beginCommand({ id: 'confirmed', name: 'set_filter', params: { filter: 2 }, originalEpoch: 4 });
    store.acknowledgeCommand('confirmed', 4, 4);

    store.confirmCommand('confirmed', 4, 4);
    expect(store.getCommandLifecycle('confirmed', 4)).toMatchObject({ status: 'confirmed', eventEpoch: 4 });
    expect(store.getCommandLifecycle('confirmed', 4)).not.toHaveProperty('confirmedValue');
  });

  it('rejects confirmation before delivery acknowledgement', () => {
    store.beginCommand({ id: 'pre-ack', name: 'set_filter', params: { filter: 2 }, originalEpoch: 4 });

    store.confirmCommand('pre-ack', 4, 4);

    expect(store.getCommandLifecycle('pre-ack', 4)?.status).toBe('pending');
  });

  it.each([
    ['confirmed', (id: string) => store.confirmCommand(id, 5, 5)],
    ['timed-out', () => vi.advanceTimersByTime(25)],
    ['cancelled', (id: string) => store.cancelPendingCommands(5)],
  ] as const)('retains a %s outcome for the bounded announcement window', (_status, complete) => {
    const id = `terminal-${_status}`;
    store.beginCommand({ id, name: 'set_filter', params: { filter: 2 }, originalEpoch: 5, timeoutMs: 25 });
    store.acknowledgeCommand(id, 5, 5);
    complete(id);

    expect(store.getCommandLifecycle(id, 5)?.status).toBe(_status);
    vi.advanceTimersByTime(5_000);
    expect(store.getCommandLifecycle(id, 5)).toBeUndefined();
  });

  it('keeps staggered command outcomes through the normal transport observation window, then expires them', () => {
    store.beginCommand({ id: 'first', name: 'set_freq', params: { freq: 1 }, originalEpoch: 6 });
    vi.advanceTimersByTime(4_500);
    store.beginCommand({ id: 'second', name: 'set_freq', params: { freq: 2 }, originalEpoch: 6 });
    vi.advanceTimersByTime(4_500);
    store.beginCommand({ id: 'third', name: 'set_freq', params: { freq: 3 }, originalEpoch: 6 });

    expect(store.getCommandLifecycles()).toHaveLength(3);
    expect(store.getCommandLifecycle('first', 6)?.status).toBe('timed-out');
    expect(store.getCommandLifecycle('second', 6)?.status).toBe('pending');
    expect(store.getCommandLifecycle('third', 6)?.status).toBe('pending');

    vi.advanceTimersByTime(1_000);
    expect(store.getCommandLifecycle('first', 6)).toBeUndefined();
  });

  it('bounds retained lifecycle records and cleans up oldest terminal entries first', () => {
    for (let i = 0; i < 110; i += 1) {
      store.beginCommand({
        id: `cmd-${i}`,
        name: 'set_freq',
        params: { freq: i },
        originalEpoch: 1,
      });
      store.failCommand(`cmd-${i}`, 1, 1, 'fixture');
    }

    const records = store.getCommandLifecycles();
    expect(records).toHaveLength(100);
    expect(records.some((record) => record.id === 'cmd-0')).toBe(false);
    expect(records.some((record) => record.id === 'cmd-109')).toBe(true);
  });

  it('rejects the 101st pending record without evicting live correlation', () => {
    for (let i = 0; i < 100; i += 1) {
      store.beginCommand({ id: `pending-${i}`, name: 'set_freq', params: { freq: i }, originalEpoch: 9 });
    }
    expect(() => store.beginCommand({
      id: 'overflow', name: 'set_freq', params: { freq: 101 }, originalEpoch: 9,
    })).toThrow(/capacity/i);

    expect(store.getCommandLifecycles()).toHaveLength(100);
    expect(store.getCommandLifecycle('pending-0', 9)?.status).toBe('pending');
    expect(store.getCommandLifecycle('overflow', 9)).toBeUndefined();
  });

  it('rejects the 101st acknowledged record without evicting live correlation', () => {
    for (let i = 0; i < 100; i += 1) {
      store.beginCommand({ id: `acknowledged-${i}`, name: 'set_freq', params: { freq: i }, originalEpoch: 9 });
      store.acknowledgeCommand(`acknowledged-${i}`, 9, 9);
    }

    expect(() => store.beginCommand({
      id: 'overflow', name: 'set_freq', params: { freq: 101 }, originalEpoch: 9,
    })).toThrow(/capacity/i);

    expect(store.getCommandLifecycles()).toHaveLength(100);
    expect(store.getCommandLifecycle('acknowledged-0', 9)?.status).toBe('acknowledged');
    expect(store.getCommandLifecycle('overflow', 9)).toBeUndefined();
  });

  describe('RF/SQL state-backed descriptors', () => {
    it('uses exact receiver scopes and normalized 0..1 targets', () => {
      const rfMain = store.RF_GAIN_COMMAND_DESCRIPTOR.scope({ params: { level: 128, receiver: 0 } })!;
      const rfSub = store.RF_GAIN_COMMAND_DESCRIPTOR.scope({ params: { level: 255, receiver: 1 } })!;
      const sqlMain = store.SQUELCH_COMMAND_DESCRIPTOR.scope({ params: { level: 0, receiver: 0 } })!;
      const sqlSub = store.SQUELCH_COMMAND_DESCRIPTOR.scope({ params: { level: 64, receiver: 1 } })!;

      expect([...store.STATE_BACKED_COMMAND_DESCRIPTORS.keys()]).toEqual([
        'set_filter_width', 'set_break_in_delay', 'set_rf_gain', 'set_squelch',
      ]);
      expect(rfMain).toEqual({ control: 'rf-gain', receiver: 0 });
      expect(rfSub).toEqual({ control: 'rf-gain', receiver: 1 });
      expect(sqlMain).toEqual({ control: 'squelch', receiver: 0 });
      expect(sqlSub).toEqual({ control: 'squelch', receiver: 1 });
      expect(store.RF_GAIN_COMMAND_DESCRIPTOR.fieldPath(rfMain)).toBe('main.rfGain');
      expect(store.RF_GAIN_COMMAND_DESCRIPTOR.fieldPath(rfSub)).toBe('sub.rfGain');
      expect(store.SQUELCH_COMMAND_DESCRIPTOR.fieldPath(sqlMain)).toBe('main.squelch');
      expect(store.SQUELCH_COMMAND_DESCRIPTOR.fieldPath(sqlSub)).toBe('sub.squelch');
      expect(store.RF_GAIN_COMMAND_DESCRIPTOR.target({ params: { level: 128, receiver: 0 } })).toBe(128 / 255);
      expect(store.SQUELCH_COMMAND_DESCRIPTOR.target({ params: { level: 255, receiver: 1 } })).toBe(1);
      expect(store.RF_GAIN_COMMAND_DESCRIPTOR.confirmed({
        main: { rfGain: 0.5 }, sub: { rfGain: 0.75 },
      } as never, rfMain)).toBe(0.5);
      expect(store.SQUELCH_COMMAND_DESCRIPTOR.confirmed({
        main: { squelch: 0.1 }, sub: { squelch: 0.2 },
      } as never, sqlSub)).toBe(0.2);
      expect(store.RF_GAIN_COMMAND_DESCRIPTOR.matches(128 / 255, 128 / 255)).toBe(true);
      expect(store.RF_GAIN_COMMAND_DESCRIPTOR.matches(0.5, 128 / 255)).toBe(false);
    });

    it.each([
      ['missing level', { receiver: 0 }],
      ['missing receiver', { level: 1 }],
      ['extra key', { level: 1, receiver: 0, slot: 'A' }],
      ['string level', { level: '1', receiver: 0 }],
      ['boolean level', { level: true, receiver: 0 }],
      ['fractional level', { level: 1.5, receiver: 0 }],
      ['nonfinite level', { level: Number.POSITIVE_INFINITY, receiver: 0 }],
      ['negative level', { level: -1, receiver: 0 }],
      ['high level', { level: 256, receiver: 0 }],
      ['string receiver', { level: 1, receiver: '0' }],
      ['boolean receiver', { level: 1, receiver: false }],
      ['unknown receiver', { level: 1, receiver: 2 }],
    ])('rejects a %s envelope', (_name, params) => {
      for (const descriptor of [store.RF_GAIN_COMMAND_DESCRIPTOR, store.SQUELCH_COMMAND_DESCRIPTOR]) {
        expect(descriptor.scope({ params })).toBeNull();
        expect(descriptor.target({ params })).toBeNull();
      }
    });

    it('rejects symbol, inherited, and throwing envelopes without executing a default', () => {
      const inherited = Object.create({ receiver: 0 }) as Record<string, unknown>;
      inherited.level = 1;
      const symbol = { level: 1, receiver: 0, [Symbol('extra')]: true };
      const throwing = Object.defineProperty({ receiver: 0 }, 'level', {
        enumerable: true, get: () => { throw new Error('read'); },
      });
      const ownKeysTrap = new Proxy({}, { ownKeys: () => { throw new Error('keys'); } });

      for (const params of [inherited, symbol, throwing, ownKeysTrap]) {
        expect(store.RF_GAIN_COMMAND_DESCRIPTOR.scope({ params })).toBeNull();
        expect(store.RF_GAIN_COMMAND_DESCRIPTOR.target({ params })).toBeNull();
      }
    });

    it('supersedes only the same intent and receiver scope', () => {
      const rfMain = store.beginCommand({
        id: 'rf-main', name: 'set_rf_gain', params: { level: 10, receiver: 0 }, originalEpoch: 4,
      });
      const rfSub = store.beginCommand({
        id: 'rf-sub', name: 'set_rf_gain', params: { level: 20, receiver: 1 }, originalEpoch: 4,
      });
      const sqlMain = store.beginCommand({
        id: 'sql-main', name: 'set_squelch', params: { level: 30, receiver: 0 }, originalEpoch: 4,
      });
      store.beginCommand({
        id: 'rf-main-new', name: 'set_rf_gain', params: { level: 40, receiver: 0 }, originalEpoch: 4,
      });

      expect(store.isCommandLifecycleSuperseded(rfMain)).toBe(true);
      expect(store.isCommandLifecycleSuperseded(rfSub)).toBe(false);
      expect(store.isCommandLifecycleSuperseded(sqlMain)).toBe(false);
    });

    it('confirms only from exact scoped truth after a finite advancing ACK marker', () => {
      const snapshot = (
        rfGain: number, marker: number, freshness: 'fresh' | 'stale' = 'fresh',
      ): ServerState => ({
        providerGeneration: 3, main: { rfGain }, sub: {},
        fieldStatus: { 'main.rfGain': {
          storePath: 'fixture', observed: true, freshness,
          availability: 'available', lastObservedMonotonic: marker,
        } },
      } as unknown as ServerState);
      emitState(snapshot(128 / 255, 4));
      const command = store.beginCommand({
        id: 'rf-confirm', name: 'set_rf_gain', params: { level: 128, receiver: 0 }, originalEpoch: 7,
      });
      store.acknowledgeCommand(command.id, 7, 7);
      const current = () => store.getCommandLifecycle(command.id, 7)!;
      expect(current()).toMatchObject({
        status: 'acknowledged', providerGeneration: 3,
        ackFieldObservationTimes: { 'main.rfGain': 4 },
      });

      emitState(snapshot(128 / 255, 4));
      expect(current().status).toBe('acknowledged');
      emitState(snapshot(0.5, 5));
      expect(current().status).toBe('acknowledged');
      emitState(snapshot(128 / 255, 6, 'stale'));
      expect(current().status).toBe('acknowledged');
      emitState(snapshot(128 / 255, 7));
      expect(current().status).toBe('confirmed');
    });
  });
});
