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

  it('records dispatch and held evidence without changing coarse lifecycle authority', () => {
    const command = store.beginCommand({
      id: 'phase-source',
      name: 'set_filter_width',
      params: { width: 2_800, receiver: 0 },
      originalEpoch: 7,
    });

    store.markCommandDispatched(command.id, 7, 7);
    expect(store.getCommandLifecycle(command.id, 7)).toMatchObject({
      status: 'pending',
      dispatchedEventEpoch: 7,
    });
    store.applyCommandLifecycleProjection({
      commandId: command.id,
      originalEpoch: 7,
      eventEpoch: 7,
      kind: 'held',
      reason: 'tx_active',
      expiresAt: 12.5,
    }, 7);
    expect(store.getCommandLifecycle(command.id, 7)?.status).toBe('pending');
    expect(store.getCommandLifecycleHold(command)).toEqual({
      commandId: command.id,
      originalEpoch: 7,
      eventEpoch: 7,
      kind: 'held',
      reason: 'tx_active',
      expiresAt: 12.5,
    });

    store.acknowledgeCommand(command.id, 7, 7);
    expect(store.getCommandLifecycle(command.id, 7)?.status).toBe('acknowledged');
    expect(store.getCommandLifecycleHold(command)?.expiresAt).toBe(12.5);
  });

  it('separates permanent local obsolescence from a remote terminal supersession', () => {
    const old = store.beginCommand({
      id: 'old', name: 'set_filter_width', params: { width: 3_000 }, originalEpoch: 7,
    });
    const latest = store.beginCommand({
      id: 'latest', name: 'set_filter_width', params: { width: 2_800 }, originalEpoch: 7,
    });
    expect(store.getCommandLifecycle(old.id, 7)?.locallyObsolete).toBe(true);
    expect(store.getCommandLifecycle(latest.id, 7)?.locallyObsolete).toBeUndefined();

    store.applyCommandLifecycleProjection({
      commandId: latest.id, originalEpoch: 7, eventEpoch: 7, kind: 'superseded',
    }, 7);
    expect(store.getCommandLifecycle(latest.id, 7)).toMatchObject({
      status: 'cancelled', terminalOutcome: 'superseded',
    });
    expect(store.isCommandLifecycleSuperseded(latest)).toBe(true);

    store.applyCommandLifecycleProjection({
      commandId: old.id, originalEpoch: 7, eventEpoch: 7, kind: 'superseded',
    }, 7);
    expect(store.getCommandLifecycle(old.id, 7)).toMatchObject({
      locallyObsolete: true, terminalOutcome: 'superseded',
    });
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
        'set_cw_pitch', 'set_key_speed', 'set_mic_gain', 'set_drive_gain',
        'set_vox_gain', 'set_anti_vox_gain', 'set_vox_delay',
        'set_compressor_level', 'set_monitor_gain', 'set_nb_level', 'set_nb_width',
        'set_nr_level', 'set_nb_depth',
        'set_notch_filter', 'set_manual_notch_width', 'set_agc_time_constant',
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

  describe('global CW state-backed descriptors', () => {
    it('registers exact global scopes, fields, targets, and canonical values', () => {
      expect([...store.STATE_BACKED_COMMAND_DESCRIPTORS.keys()]).toEqual([
        'set_filter_width', 'set_break_in_delay', 'set_rf_gain', 'set_squelch',
        'set_cw_pitch', 'set_key_speed', 'set_mic_gain', 'set_drive_gain',
        'set_vox_gain', 'set_anti_vox_gain', 'set_vox_delay',
        'set_compressor_level', 'set_monitor_gain', 'set_nb_level', 'set_nb_width',
        'set_nr_level', 'set_nb_depth',
        'set_notch_filter', 'set_manual_notch_width', 'set_agc_time_constant',
      ]);
      const pitchScope = store.CW_PITCH_COMMAND_DESCRIPTOR.scope({ params: { value: 640 } })!;
      const speedScope = store.KEY_SPEED_COMMAND_DESCRIPTOR.scope({ params: { speed: 27 } })!;
      expect(pitchScope).toEqual({ control: 'cw-pitch', receiver: 0 });
      expect(speedScope).toEqual({ control: 'keyer-speed', receiver: 0 });
      expect(store.CW_PITCH_COMMAND_DESCRIPTOR.fieldPath(pitchScope)).toBe('cwPitch');
      expect(store.KEY_SPEED_COMMAND_DESCRIPTOR.fieldPath(speedScope)).toBe('keySpeed');
      expect(store.CW_PITCH_COMMAND_DESCRIPTOR.target({ params: { value: 640 } })).toBe(640);
      expect(store.KEY_SPEED_COMMAND_DESCRIPTOR.target({ params: { speed: 27 } })).toBe(27);
      expect(store.CW_PITCH_COMMAND_DESCRIPTOR.confirmed({ cwPitch: 650 } as ServerState, pitchScope)).toBe(650);
      expect(store.KEY_SPEED_COMMAND_DESCRIPTOR.confirmed({ keySpeed: 28 } as ServerState, speedScope)).toBe(28);
      expect(store.CW_PITCH_COMMAND_DESCRIPTOR.matches(640, 640)).toBe(true);
      expect(store.KEY_SPEED_COMMAND_DESCRIPTOR.matches(27, 28)).toBe(false);
    });

    it.each([
      ['pitch', () => store.CW_PITCH_COMMAND_DESCRIPTOR, 'value'],
      ['speed', () => store.KEY_SPEED_COMMAND_DESCRIPTOR, 'speed'],
    ] as const)('rejects every malformed %s envelope without throwing', (_name, descriptorOf, key) => {
      const descriptor = descriptorOf();
      const inherited = Object.create({ [key]: 24 }) as Record<string, unknown>;
      const throwing = Object.defineProperty({}, key, {
        enumerable: true, get: () => { throw new Error('read'); },
      });
      const ownKeysTrap = new Proxy({}, { ownKeys: () => { throw new Error('keys'); } });
      for (const params of [
        {}, inherited, { [key]: '24' }, { [key]: true }, { [key]: 24.5 },
        { [key]: Number.POSITIVE_INFINITY }, { [key]: Number.MAX_VALUE },
        { [key]: 24, extra: true }, { [key]: 24, [Symbol('extra')]: true }, throwing, ownKeysTrap,
      ]) {
        expect(descriptor.scope({ params })).toBeNull();
        expect(descriptor.target({ params })).toBeNull();
      }
    });

    it.each([
      ['set_cw_pitch', 'cwPitch', 'value', 640, 650],
      ['set_key_speed', 'keySpeed', 'speed', 27, 28],
    ] as const)('confirms %s only after a newer fresh exact field observation', (
      name, field, param, target, mismatch,
    ) => {
      const observed = (value: number, marker: number, freshness: 'fresh' | 'stale' = 'fresh') => ({
        stateContractVersion: 1, providerGeneration: 3, [field]: value,
        fieldStatus: { [field]: {
          observed: true, freshness, availability: 'available', lastObservedMonotonic: marker,
        } },
      } as unknown as ServerState);
      emitState(observed(mismatch, 4));
      store.beginCommand({
        id: name, name, params: { [param]: target }, originalEpoch: 7,
      });
      store.acknowledgeCommand(name, 7, 7);
      const status = () => store.getCommandLifecycle(name, 7)?.status;
      expect(status()).toBe('acknowledged');
      emitState(observed(target, 4));
      emitState(observed(mismatch, 5));
      emitState(observed(target, 6, 'stale'));
      expect(status()).toBe('acknowledged');
      emitState(observed(target, 7));
      expect(status()).toBe('confirmed');
    });
  });

  describe('raw TX/VOX state-backed descriptors', () => {
    const registrations = [
      ['micGain', 'set_mic_gain', 'mic-gain', 128],
      ['driveGain', 'set_drive_gain', 'drive-gain', 127],
      ['voxGain', 'set_vox_gain', 'vox-gain', 64],
      ['antiVoxGain', 'set_anti_vox_gain', 'anti-vox-gain', 192],
      ['voxDelay', 'set_vox_delay', 'vox-delay', 10],
      ['compressorLevel', 'set_compressor_level', 'compressor-level', 96],
      ['monitorGain', 'set_monitor_gain', 'monitor-level', 160],
    ] as const;

    it.each(registrations)('registers %s as exact raw global command %s', (
      field, intentName, control, midpoint,
    ) => {
      const descriptor = store.TX_AUX_COMMAND_DESCRIPTORS[field];
      const scope = descriptor.scope({ params: { level: midpoint } })!;
      expect(descriptor.intentName).toBe(intentName);
      expect(store.getStateBackedCommandDescriptor(intentName)).toBe(descriptor);
      expect(scope).toEqual({ control, receiver: 0 });
      expect(descriptor.fieldPath(scope)).toBe(field);
      for (const value of [0, midpoint, field === 'voxDelay' ? 20 : 255]) {
        expect(descriptor.target({ params: { level: value } })).toBe(value);
        expect(descriptor.confirmed({ [field]: value } as unknown as ServerState, scope)).toBe(value);
        expect(descriptor.matches(value, value)).toBe(true);
      }
      expect(descriptor.matches(midpoint + 1, midpoint)).toBe(false);
    });

    it('rejects malformed level envelopes for every TX/VOX descriptor', () => {
      const inherited = Object.create({ level: 10 }) as Record<string, unknown>;
      const throwing = Object.defineProperty({}, 'level', {
        enumerable: true, get: () => { throw new Error('read'); },
      });
      const ownKeysTrap = new Proxy({}, { ownKeys: () => { throw new Error('keys'); } });
      const malformed = [
        {}, inherited, { level: true }, { level: '10' }, { level: 10.5 },
        { level: Number.POSITIVE_INFINITY }, { level: Number.MAX_VALUE },
        { level: 10, receiver: 0 }, { level: 10, extra: true },
        { level: 10, [Symbol('extra')]: true }, throwing, ownKeysTrap,
      ];
      for (const descriptor of Object.values(store.TX_AUX_COMMAND_DESCRIPTORS)) {
        for (const params of malformed) {
          expect(descriptor.scope({ params })).toBeNull();
          expect(descriptor.target({ params })).toBeNull();
        }
      }
    });

    it('supersedes only the same TX/VOX control while independent controls remain live', () => {
      const micA = store.beginCommand({
        id: 'mic-a', name: 'set_mic_gain', params: { level: 100 }, originalEpoch: 7,
      });
      const drive = store.beginCommand({
        id: 'drive', name: 'set_drive_gain', params: { level: 100 }, originalEpoch: 7,
      });
      const micB = store.beginCommand({
        id: 'mic-b', name: 'set_mic_gain', params: { level: 200 }, originalEpoch: 7,
      });
      expect(store.isCommandLifecycleSuperseded(micA)).toBe(true);
      expect(store.isCommandLifecycleSuperseded(micB)).toBe(false);
      expect(store.isCommandLifecycleSuperseded(drive)).toBe(false);
    });

    it.each(registrations)('confirms %s only from a newer fresh exact raw observation', (
      field, intentName, _control, target,
    ) => {
      const observed = (
        value: number, marker: number, freshness: 'fresh' | 'stale' = 'fresh',
      ): ServerState => ({
        stateContractVersion: 1, providerGeneration: 3, [field]: value,
        fieldStatus: { [field]: {
          observed: true, freshness, availability: 'available', lastObservedMonotonic: marker,
        } },
      } as unknown as ServerState);
      emitState(observed(target - 1, 4));
      const command = store.beginCommand({
        id: intentName, name: intentName, params: { level: target }, originalEpoch: 7,
      });
      store.acknowledgeCommand(command.id, 7, 7);
      const status = () => store.getCommandLifecycle(command.id, 7)?.status;
      expect(status()).toBe('acknowledged');
      emitState(observed(target, 4));
      emitState(observed(target - 1, 5));
      emitState(observed(target, 6, 'stale'));
      expect(status()).toBe('acknowledged');
      emitState(observed(target, 7));
      expect(status()).toBe('confirmed');
    });
  });

  describe('raw DSP state-backed descriptors', () => {
    const receiverRegistrations = [
      ['nbLevel', 'set_nb_level', 'nb-level', 'level'],
      ['nrLevel', 'set_nr_level', 'nr-level', 'level'],
      ['notchFilter', 'set_notch_filter', 'notch-position', 'value'],
      ['manualNotchWidth', 'set_manual_notch_width', 'manual-notch-width', 'value'],
      ['agcTimeConstant', 'set_agc_time_constant', 'agc-time', 'value'],
    ] as const;

    it.each(receiverRegistrations)('registers %s with exact MAIN and SUB raw scopes', (
      field, intentName, control, param,
    ) => {
      const descriptor = store.DSP_COMMAND_DESCRIPTORS[field];
      const main = descriptor.scope({ params: { [param]: -12, receiver: 0 } })!;
      const sub = descriptor.scope({ params: { [param]: 2048, receiver: 1 } })!;
      expect(descriptor.intentName).toBe(intentName);
      expect(store.getStateBackedCommandDescriptor(intentName)).toBe(descriptor);
      expect(main).toEqual({ control, receiver: 0 });
      expect(sub).toEqual({ control, receiver: 1 });
      expect(descriptor.fieldPath(main)).toBe(`main.${field}`);
      expect(descriptor.fieldPath(sub)).toBe(`sub.${field}`);
      expect(descriptor.target({ params: { [param]: -12, receiver: 0 } })).toBe(-12);
      expect(descriptor.confirmed({ main: { [field]: -12 } } as never, main)).toBe(-12);
      expect(descriptor.matches(2048, 2048)).toBe(true);
      expect(descriptor.matches(2047, 2048)).toBe(false);
    });

    it.each([
      ['nbWidth', 'set_nb_width', 'nb-width'],
      ['nbDepth', 'set_nb_depth', 'nb-depth'],
    ] as const)('registers %s as one raw global lane with stable receiver-zero identity', (
      field, intentName, control,
    ) => {
      const descriptor = store.DSP_COMMAND_DESCRIPTORS[field];
      const scope = descriptor.scope({ params: { level: 255 } })!;
      expect(descriptor.intentName).toBe(intentName);
      expect(store.getStateBackedCommandDescriptor(intentName)).toBe(descriptor);
      expect(scope).toEqual({ control, receiver: 0 });
      expect(descriptor.fieldPath(scope)).toBe(field);
      expect(descriptor.target({ params: { level: -1 } })).toBe(-1);
      expect(descriptor.confirmed({ active: 'SUB', [field]: 255 } as unknown as ServerState, scope)).toBe(255);
    });

    it('rejects malformed DSP envelopes without coercion or throwing', () => {
      const inherited = Object.create({ level: 10, receiver: 0 }) as Record<string, unknown>;
      const throwing = Object.defineProperty({ receiver: 0 }, 'level', {
        enumerable: true, get: () => { throw new Error('read'); },
      });
      const globalThrowing = Object.defineProperty({}, 'level', {
        enumerable: true, get: () => { throw new Error('read'); },
      });
      const ownKeysTrap = new Proxy({}, { ownKeys: () => { throw new Error('keys'); } });
      for (const params of [
        {}, inherited, { level: true, receiver: 0 }, { level: '10', receiver: 0 },
        { level: 10.5, receiver: 0 }, { level: 10, receiver: false },
        { level: 10, receiver: 2 }, { level: 10, receiver: 0, extra: true },
        { level: 10, receiver: 0, [Symbol('extra')]: true }, throwing, ownKeysTrap,
      ]) {
        expect(store.DSP_COMMAND_DESCRIPTORS.nbLevel.scope({ params })).toBeNull();
        expect(store.DSP_COMMAND_DESCRIPTORS.nbLevel.target({ params })).toBeNull();
        expect(store.DSP_COMMAND_DESCRIPTORS.nrLevel.scope({ params })).toBeNull();
        expect(store.DSP_COMMAND_DESCRIPTORS.nrLevel.target({ params })).toBeNull();
      }
      for (const params of [
        {}, { level: true }, { level: '10' }, { level: Number.NaN },
        { level: Number.POSITIVE_INFINITY }, { level: 10.5 }, { level: Number.MAX_VALUE },
        { level: 10, receiver: 0 }, { level: 10, extra: true },
        { level: 10, [Symbol('extra')]: true }, globalThrowing, ownKeysTrap,
      ]) {
        expect(store.DSP_COMMAND_DESCRIPTORS.nbWidth.scope({ params })).toBeNull();
        expect(store.DSP_COMMAND_DESCRIPTORS.nbWidth.target({ params })).toBeNull();
        expect(store.DSP_COMMAND_DESCRIPTORS.nbDepth.scope({ params })).toBeNull();
        expect(store.DSP_COMMAND_DESCRIPTORS.nbDepth.target({ params })).toBeNull();
      }
    });

    it.each([
      ['set_nb_level', 'main.nbLevel', 'level', 50, 49, 0],
      ['set_nr_level', 'sub.nrLevel', 'level', 136, 128, 1],
      ['set_notch_filter', 'sub.notchFilter', 'value', -20, -19, 1],
      ['set_manual_notch_width', 'main.manualNotchWidth', 'value', 3, 2, 0],
      ['set_agc_time_constant', 'sub.agcTimeConstant', 'value', 900, 899, 1],
      ['set_nb_width', 'nbWidth', 'level', 64, 63, null],
      ['set_nb_depth', 'nbDepth', 'level', 9, 8, null],
    ] as const)('confirms %s only from a newer fresh exact field observation', (
      name, path, param, target, mismatch, receiver,
    ) => {
      const observed = (value: number, marker: number, freshness: 'fresh' | 'stale' = 'fresh') => ({
        stateContractVersion: 1, providerGeneration: 3, active: receiver === 1 ? 'SUB' : 'MAIN',
        main: receiver === 0 ? { [path.split('.')[1]]: value } : {},
        sub: receiver === 1 ? { [path.split('.')[1]]: value } : {},
        ...(receiver === null ? { [path]: value } : {}),
        fieldStatus: { [path]: {
          observed: true, freshness, availability: 'available', lastObservedMonotonic: marker,
        } },
      } as unknown as ServerState);
      emitState(observed(mismatch, 4));
      const params = receiver === null ? { [param]: target } : { [param]: target, receiver };
      const command = store.beginCommand({ id: name, name, params, originalEpoch: 7 });
      store.acknowledgeCommand(command.id, 7, 7);
      const status = () => store.getCommandLifecycle(command.id, 7)?.status;
      expect(status()).toBe('acknowledged');
      emitState(observed(target, 4));
      emitState(observed(mismatch, 5));
      emitState(observed(target, 6, 'stale'));
      expect(status()).toBe('acknowledged');
      emitState(observed(target, 7));
      expect(status()).toBe('confirmed');
    });

    it('keeps global NB identities fixed while receiver controls and fields stay independent', () => {
      const widthA = store.beginCommand({
        id: 'width-a', name: 'set_nb_width', params: { level: 20 }, originalEpoch: 7,
      });
      const levelMain = store.beginCommand({
        id: 'level-main', name: 'set_nb_level', params: { level: 30, receiver: 0 }, originalEpoch: 7,
      });
      const levelSub = store.beginCommand({
        id: 'level-sub', name: 'set_nb_level', params: { level: 40, receiver: 1 }, originalEpoch: 7,
      });
      store.beginCommand({ id: 'width-b', name: 'set_nb_width', params: { level: 50 }, originalEpoch: 7 });
      const depthA = store.beginCommand({
        id: 'depth-a', name: 'set_nb_depth', params: { level: 4 }, originalEpoch: 7,
      });
      store.beginCommand({ id: 'depth-b', name: 'set_nb_depth', params: { level: 5 }, originalEpoch: 7 });
      expect(store.isCommandLifecycleSuperseded(widthA)).toBe(true);
      expect(store.isCommandLifecycleSuperseded(depthA)).toBe(true);
      expect(store.isCommandLifecycleSuperseded(levelMain)).toBe(false);
      expect(store.isCommandLifecycleSuperseded(levelSub)).toBe(false);
      expect(store.DSP_COMMAND_DESCRIPTORS.nbWidth.scope({ params: { level: 50 } }))
        .toEqual({ control: 'nb-width', receiver: 0 });
      expect(store.DSP_COMMAND_DESCRIPTORS.nbDepth.scope({ params: { level: 5 } }))
        .toEqual({ control: 'nb-depth', receiver: 0 });
    });

    it('does not confirm receiver or global DSP commands from the wrong field path', () => {
      emitState({
        providerGeneration: 3, active: 'SUB', nbWidth: 20, nbDepth: 4,
        main: { nbLevel: 40, nrLevel: 136 }, sub: { nbLevel: 30, nrLevel: 128 },
        fieldStatus: {
          nbWidth: { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
          nbDepth: { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
          'main.nbLevel': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
          'sub.nbLevel': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
          'main.nrLevel': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
          'sub.nrLevel': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
        },
      } as unknown as ServerState);
      store.beginCommand({
        id: 'sub-level', name: 'set_nb_level', params: { level: 40, receiver: 1 }, originalEpoch: 7,
      });
      store.beginCommand({ id: 'global-width', name: 'set_nb_width', params: { level: 40 }, originalEpoch: 7 });
      store.beginCommand({
        id: 'main-nr', name: 'set_nr_level', params: { level: 128, receiver: 0 }, originalEpoch: 7,
      });
      store.beginCommand({ id: 'global-depth', name: 'set_nb_depth', params: { level: 8 }, originalEpoch: 7 });
      store.acknowledgeCommand('sub-level', 7, 7);
      store.acknowledgeCommand('global-width', 7, 7);
      store.acknowledgeCommand('main-nr', 7, 7);
      store.acknowledgeCommand('global-depth', 7, 7);
      emitState({
        providerGeneration: 3, active: 'MAIN', nbWidth: 20, nbDepth: 4,
        main: { nbLevel: 40, nrLevel: 136 }, sub: { nbLevel: 30, nrLevel: 128 },
        fieldStatus: {
          nbWidth: { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 },
          nbDepth: { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 },
          'main.nbLevel': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 },
          'sub.nbLevel': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
          'main.nrLevel': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 },
          'sub.nrLevel': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
        },
      } as unknown as ServerState);
      expect(store.getCommandLifecycle('sub-level', 7)?.status).toBe('acknowledged');
      expect(store.getCommandLifecycle('global-width', 7)?.status).toBe('acknowledged');
      expect(store.getCommandLifecycle('main-nr', 7)?.status).toBe('acknowledged');
      expect(store.getCommandLifecycle('global-depth', 7)?.status).toBe('acknowledged');
    });
  });
});
