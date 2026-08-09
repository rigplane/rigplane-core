/**
 * Opt-in auto LAN MOD-input for network voice TX (MOR-618, T4 of epic MOR-614).
 *
 * The feature is OFF by default — default UX stays the MOR-617 warn +
 * one-click guard. When the user opts in:
 *   - at web TX start, if the active DATA group's MOD-input source is known
 *     and != LAN(5): remember the previous source, set LAN via the existing
 *     per-group SET command, and suppress the MOR-617 warning (the
 *     optimistic LAN patch preempts the guard);
 *   - after authoritative PTT-off confirmation, restore the remembered source
 *     only if auto changed it and the group is still on LAN;
 *   - pending restore state is memory-only and cannot replay on reconnect.
 *
 * When OFF, behavior is exactly MOR-617 (warn-only, no silent changes).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(() => true),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: vi.fn(),
    startRx: vi.fn(),
    stopRx: vi.fn(),
    setRxVolume: vi.fn(),
    startTx: vi.fn(async () => null),
    stopTx: vi.fn(),
    rxEnabled: false,
  },
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    startTx: vi.fn(async () => null),
    stopTx: vi.fn(),
  },
}));

import { sendCommand } from '$lib/transport/ws-client';
import { runtime } from '$lib/runtime/frontend-runtime';
import { getRadioState, resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import {
  AUTO_LAN_PREF_KEY,
  PENDING_RESTORE_KEY,
  clearLegacyPendingModInputRestore,
  deriveAutoLanModInputProps,
  isAutoLanModInputEnabled,
  restoreModInputAfterTx,
  setAutoLanModInputEnabled,
} from '../mod-input-auto.svelte';
import {
  deriveModInputTxGuardProps,
  dismissModInputTxGuard,
} from '../mod-input-tx-guard.svelte';
import {
  getTxAudioControl,
  type AuthoritativePttObservation,
  type PttObservationMarker,
} from '../tx-adapter';
import type { ServerState } from '$lib/types/state';

function receiver(dataMode: number) {
  return {
    freqHz: 14_200_000,
    mode: 'USB',
    filter: 1,
    dataMode,
    sMeter: 40,
    att: 0,
    preamp: 0,
    nb: false,
    nr: false,
    afLevel: 128,
    rfGain: 255,
    squelch: 0,
    agc: 2,
    nbLevel: 0,
    nrLevel: 0,
    autoNotch: false,
    manualNotch: false,
    agcTimeConstant: 0,
  };
}

let revision = 1;

function makeState(overrides: Record<string, unknown> = {}): ServerState {
  return {
    revision: ++revision,
    stateRevision: revision,
    freshnessRevision: revision,
    observationSeq: revision,
    updatedAt: '2026-08-08T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    stateContractVersion: 1,
    providerGeneration: 0,
    main: receiver(0),
    sub: receiver(0),
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    txTarget: { status: 'unknown', reason: 'not-observed' },
    ...overrides,
  } as unknown as ServerState;
}

function setState(overrides: Record<string, unknown> = {}): void {
  setRadioState(makeState(overrides));
}

function useDualReceiverCapabilities(): void {
  setCapabilities({ capabilities: ['data_mode'], receivers: 2, vfoScheme: 'main_sub', stateContractVersion: 1, providerGeneration: 0 } as never);
}

function missingStatus() {
  return {
    storePath: 'test.path',
    observed: false,
    freshness: 'unknown',
    availability: 'missing',
  };
}

function pendingInStorage(): unknown {
  const raw = localStorage.getItem(PENDING_RESTORE_KEY);
  return raw === null ? null : JSON.parse(raw);
}

const OFF_BARRIER: PttObservationMarker = {
  authorityEpoch: 7,
  pttObservationSeq: 41,
  pttLastObservedMonotonic: 1_000,
};

function offObservation(
  overrides: Partial<AuthoritativePttObservation> = {},
): AuthoritativePttObservation {
  return {
    authorityEpoch: 7,
    ptt: false,
    pttObserved: true,
    pttFreshness: 'fresh',
    pttObservationSeq: 42,
    pttLastObservedMonotonic: 1_001,
    pttSource: 'radio-readback',
    ...overrides,
  };
}

function confirmOff(): void {
  getTxAudioControl().restoreModAfterConfirmedOff({
    barrier: OFF_BARRIER,
    observation: offObservation(),
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  getTxAudioControl().stopLocalAudio();
  // Drain any pending restore left by a previous test, then wipe traces.
  restoreModInputAfterTx();
  localStorage.clear();
  vi.mocked(sendCommand).mockClear();
  vi.mocked(sendCommand).mockReturnValue(true);
  vi.mocked(runtime.startTx).mockClear();
  vi.mocked(runtime.startTx).mockResolvedValue(null);
  vi.mocked(runtime.stopTx).mockClear();
  resetRadioState();
  setCapabilities({ capabilities: ['data_mode'], stateContractVersion: 1, providerGeneration: 0 } as never);
  dismissModInputTxGuard();
});

describe('toggle (MOR-618)', () => {
  it('is OFF by default', () => {
    // First test in the file: module loaded with an empty localStorage.
    expect(isAutoLanModInputEnabled()).toBe(false);
    expect(deriveAutoLanModInputProps().enabled).toBe(false);
  });

  it('persists the preference to localStorage', () => {
    setAutoLanModInputEnabled(true);
    expect(localStorage.getItem(AUTO_LAN_PREF_KEY)).toBe('true');
    expect(isAutoLanModInputEnabled()).toBe(true);

    setAutoLanModInputEnabled(false);
    expect(localStorage.getItem(AUTO_LAN_PREF_KEY)).toBe('false');
    expect(isAutoLanModInputEnabled()).toBe(false);
  });

  it('is available only with the data_mode capability and an observed group', () => {
    setState({ main: receiver(1), data1ModInput: 0 });
    expect(deriveAutoLanModInputProps().available).toBe(true);

    setCapabilities({ capabilities: [], stateContractVersion: 1, providerGeneration: 0 } as never);
    expect(deriveAutoLanModInputProps().available).toBe(false);

    setCapabilities({ capabilities: ['data_mode'], stateContractVersion: 1, providerGeneration: 0 } as never);
    setState({
      main: receiver(1),
      data1ModInput: 0,
      fieldStatus: { data1ModInput: missingStatus() },
    });
    expect(deriveAutoLanModInputProps().available).toBe(false);
  });
});

describe('OFF behavior is exactly MOR-617 (MOR-618)', () => {
  it('does not auto-set and the T3 warning still arms', async () => {
    setAutoLanModInputEnabled(false);
    setState({ main: receiver(1), data1ModInput: 0 });

    await getTxAudioControl().startTx();

    expect(sendCommand).not.toHaveBeenCalled();
    expect(deriveModInputTxGuardProps().visible).toBe(true);
    expect(pendingInStorage()).toBeNull();

    getTxAudioControl().stopLocalAudio();
    expect(sendCommand).not.toHaveBeenCalled();
    expect(runtime.stopTx).toHaveBeenCalledTimes(1);
  });
});

describe('auto-set at TX start (MOR-618)', () => {
  it('sets LAN on the active group, remembers the previous source and suppresses the warning', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });

    await getTxAudioControl().startTx();

    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 5 });
    expect(runtime.startTx).toHaveBeenCalledTimes(1);
    // Optimistic patch preempts the MOR-617 guard — no warning.
    expect(getRadioState()?.data1ModInput).toBe(5);
    expect(deriveModInputTxGuardProps().visible).toBe(false);
    // The restore transaction is memory-only and cannot replay after reload.
    expect(pendingInStorage()).toBeNull();
    expect(sendCommand).not.toHaveBeenCalledWith('arm_mod_input_restore', expect.anything());
  });

  it('routes to the ACTIVE receiver group (SUB on D2)', async () => {
    setAutoLanModInputEnabled(true);
    useDualReceiverCapabilities();
    setState({
      active: 'SUB',
      main: receiver(0),
      sub: receiver(2),
      dataOffModInput: 5,
      data2ModInput: 3,
    });

    await getTxAudioControl().startTx();

    expect(sendCommand).toHaveBeenCalledWith('set_data2_mod_input', { source: 5 });
    expect(sendCommand).not.toHaveBeenCalledWith('arm_mod_input_restore', expect.anything());
  });

  it('does nothing when the source is already LAN', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 5 });

    await getTxAudioControl().startTx();

    expect(sendCommand).not.toHaveBeenCalled();
    expect(pendingInStorage()).toBeNull();
  });

  it('does nothing when the source is unknown (null)', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: null });

    await getTxAudioControl().startTx();

    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('does nothing without the data_mode capability', async () => {
    setAutoLanModInputEnabled(true);
    setCapabilities({ capabilities: [], stateContractVersion: 1, providerGeneration: 0 } as never);
    setState({ main: receiver(1), data1ModInput: 0 });

    await getTxAudioControl().startTx();

    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('does nothing when fieldStatus marks the group missing', async () => {
    setAutoLanModInputEnabled(true);
    setState({
      main: receiver(1),
      data1ModInput: 0,
      fieldStatus: { data1ModInput: missingStatus() },
    });

    await getTxAudioControl().startTx();

    expect(sendCommand).not.toHaveBeenCalled();
  });
});

describe('confirmed restore after local TX audio stop (MOR-618, MOR-990)', () => {
  it('treats startTx while already running as a side-effect-free success', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    const control = getTxAudioControl();
    await control.startTx();

    useDualReceiverCapabilities();
    setState({
      active: 'SUB',
      main: receiver(1),
      sub: receiver(2),
      data1ModInput: 5,
      data2ModInput: 3,
    });
    dismissModInputTxGuard();
    vi.mocked(sendCommand).mockClear();
    vi.mocked(runtime.startTx).mockClear();

    await expect(control.startTx()).resolves.toBeNull();

    expect(runtime.startTx).not.toHaveBeenCalled();
    expect(sendCommand).not.toHaveBeenCalled();
    expect(getRadioState()?.data2ModInput).toBe(3);
    expect(deriveModInputTxGuardProps().visible).toBe(false);

    control.stopLocalAudio();
    control.stopLocalAudio();
    expect(runtime.stopTx).toHaveBeenCalledTimes(1);
  });

  it('rejects a second start while the first start is pending without side effects', async () => {
    const pendingStart = deferred<string | null>();
    vi.mocked(runtime.startTx).mockReturnValueOnce(pendingStart.promise);
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    const control = getTxAudioControl();
    const start = control.startTx();

    setState({ main: receiver(1), data1ModInput: 0 });
    dismissModInputTxGuard();
    vi.mocked(sendCommand).mockClear();

    await expect(control.startTx()).resolves.toBe('TX audio start already in progress');
    expect(runtime.startTx).toHaveBeenCalledTimes(1);
    expect(sendCommand).not.toHaveBeenCalled();
    expect(deriveModInputTxGuardProps().visible).toBe(false);

    control.stopLocalAudio();
    pendingStart.resolve(null);
    await start;
  });

  it('cleans immediately and defensively again after a cancelled start succeeds late', async () => {
    const pendingStart = deferred<string | null>();
    vi.mocked(runtime.startTx).mockReturnValueOnce(pendingStart.promise);
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    const control = getTxAudioControl();

    const start = control.startTx();
    control.stopLocalAudio();
    control.stopLocalAudio();

    expect(runtime.stopTx).toHaveBeenCalledTimes(1);
    expect(sendCommand).not.toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(pendingInStorage()).toBeNull();

    pendingStart.resolve(null);
    await expect(start).resolves.toBeNull();
    expect(runtime.stopTx).toHaveBeenCalledTimes(2);
    expect(sendCommand).not.toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    confirmOff();
    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
  });

  it('retains cancellation and cleanup when a late start returns an error', async () => {
    const pendingStart = deferred<string | null>();
    vi.mocked(runtime.startTx).mockReturnValueOnce(pendingStart.promise);
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    const control = getTxAudioControl();

    const start = control.startTx();
    control.stopLocalAudio();
    control.stopLocalAudio();
    expect(runtime.stopTx).toHaveBeenCalledTimes(1);

    pendingStart.resolve('TX MIC: capture failed');
    await expect(start).resolves.toBe('TX MIC: capture failed');
    expect(runtime.stopTx).toHaveBeenCalledTimes(2);
    expect(sendCommand).not.toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(pendingInStorage()).toBeNull();
    confirmOff();
    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
  });

  it('retains cancellation and cleanup when a late start rejects', async () => {
    const pendingStart = deferred<string | null>();
    vi.mocked(runtime.startTx).mockReturnValueOnce(pendingStart.promise);
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    const control = getTxAudioControl();
    const startError = new Error('capture rejected');

    const start = control.startTx();
    control.stopLocalAudio();
    control.stopLocalAudio();
    expect(runtime.stopTx).toHaveBeenCalledTimes(1);

    pendingStart.reject(startError);
    await expect(start).rejects.toBe(startError);
    expect(runtime.stopTx).toHaveBeenCalledTimes(2);
    expect(sendCommand).not.toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(pendingInStorage()).toBeNull();
    confirmOff();
    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
  });

  it('stops local audio without restoring, then restores after confirmed OFF', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    await getTxAudioControl().startTx();
    vi.mocked(sendCommand).mockClear();

    getTxAudioControl().stopLocalAudio();

    expect(runtime.stopTx).toHaveBeenCalledTimes(1);
    expect(sendCommand).not.toHaveBeenCalled();
    expect(pendingInStorage()).toBeNull();

    confirmOff();

    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(sendCommand).not.toHaveBeenCalledWith('disarm_mod_input_restore', expect.anything());
    expect(getRadioState()?.data1ModInput).toBe(0);
    expect(pendingInStorage()).toBeNull();

    // Restore is one-shot — a second stop sends nothing.
    vi.mocked(sendCommand).mockClear();
    getTxAudioControl().stopLocalAudio();
    confirmOff();
    expect(sendCommand).not.toHaveBeenCalled();
    expect(runtime.stopTx).toHaveBeenCalledTimes(1);
  });

  it('does not stomp a manual mid-TX change away from LAN', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    await getTxAudioControl().startTx();
    // Write-through readback confirms LAN (drops the optimistic overlay)…
    setState({ main: receiver(1), data1ModInput: 5 });
    // …then the user changed the group to USB(3) during TX.
    setState({ main: receiver(1), data1ModInput: 3 });
    vi.mocked(sendCommand).mockClear();

    getTxAudioControl().stopLocalAudio();
    expect(sendCommand).not.toHaveBeenCalled();
    confirmOff();

    // The manual choice wins: no frontend or backend restore mutation.
    expect(sendCommand).not.toHaveBeenCalled();
    expect(sendCommand).not.toHaveBeenCalledWith('set_data1_mod_input', expect.anything());
    expect(pendingInStorage()).toBeNull();
  });

  it('still restores when the toggle was turned OFF mid-TX', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    await getTxAudioControl().startTx();
    setAutoLanModInputEnabled(false);
    vi.mocked(sendCommand).mockClear();

    getTxAudioControl().stopLocalAudio();
    confirmOff();

    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
  });

  it('retains the pending restore when TX audio fails until OFF is confirmed', async () => {
    setAutoLanModInputEnabled(true);
    vi.mocked(runtime.startTx).mockResolvedValue('TX MIC: capture failed');
    setState({ main: receiver(1), data1ModInput: 0 });

    const err = await getTxAudioControl().startTx();

    expect(err).toBe('TX MIC: capture failed');
    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 5 });
    expect(sendCommand).not.toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(pendingInStorage()).toBeNull();

    confirmOff();
    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(pendingInStorage()).toBeNull();
  });

  it('rejects cached, stale, old-epoch, non-readback and unrelated evidence', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    await getTxAudioControl().startTx();
    getTxAudioControl().stopLocalAudio();
    vi.mocked(sendCommand).mockClear();

    const rejected: AuthoritativePttObservation[] = [
      offObservation({ pttObservationSeq: 41 }),
      offObservation({ pttObservationSeq: 40 }),
      offObservation({ pttFreshness: 'stale' }),
      offObservation({ authorityEpoch: 6 }),
      offObservation({ pttSource: 'command_response' }),
      offObservation({ pttSource: 'ack' }),
      offObservation({ pttSource: 'optimistic' }),
      offObservation({ pttObserved: false }),
      offObservation({ ptt: true }),
      { ...offObservation({ pttObservationSeq: 41 }), stateRevision: 999_999 } as AuthoritativePttObservation,
    ];
    for (const observation of rejected) {
      getTxAudioControl().restoreModAfterConfirmedOff({ barrier: OFF_BARRIER, observation });
    }
    expect(sendCommand).not.toHaveBeenCalled();
    expect(pendingInStorage()).toBeNull();

    confirmOff();
    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
  });

  it('uses the field-specific monotonic marker when no PTT sequence exists', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    await getTxAudioControl().startTx();
    getTxAudioControl().stopLocalAudio();
    vi.mocked(sendCommand).mockClear();

    getTxAudioControl().restoreModAfterConfirmedOff({
      barrier: {
        authorityEpoch: 7,
        pttObservationSeq: null,
        pttLastObservedMonotonic: 1_000,
      },
      observation: offObservation({
        pttObservationSeq: null,
        pttLastObservedMonotonic: 1_001,
      }),
    });

    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(pendingInStorage()).toBeNull();
  });
});

describe('legacy persisted restore migration (MOR-990)', () => {
  it('clears a legacy record without mutating cached radio state', () => {
    localStorage.setItem(
      PENDING_RESTORE_KEY,
      JSON.stringify({ command: 'set_data1_mod_input', key: 'data1ModInput', source: 0 }),
    );
    setState({ main: receiver(1), data1ModInput: 5 });

    clearLegacyPendingModInputRestore();

    expect(sendCommand).not.toHaveBeenCalled();
    expect(getRadioState()?.data1ModInput).toBe(5);
    expect(pendingInStorage()).toBeNull();
  });

  it('cannot restore from later reconnect or cached-state updates', () => {
    localStorage.setItem(
      PENDING_RESTORE_KEY,
      JSON.stringify({ command: 'set_data1_mod_input', key: 'data1ModInput', source: 0 }),
    );

    clearLegacyPendingModInputRestore();
    setState({ main: receiver(1), data1ModInput: 5 });
    setState({ main: receiver(1), data1ModInput: 5 });

    expect(sendCommand).not.toHaveBeenCalled();
    expect(getRadioState()?.data1ModInput).toBe(5);
    expect(pendingInStorage()).toBeNull();
  });

  it('clears malformed legacy data without radio effects', () => {
    localStorage.setItem(PENDING_RESTORE_KEY, '{not json');
    setState({ main: receiver(1), data1ModInput: 5 });

    clearLegacyPendingModInputRestore();

    expect(sendCommand).not.toHaveBeenCalled();
    expect(getRadioState()?.data1ModInput).toBe(5);
    expect(pendingInStorage()).toBeNull();
  });
});

it('keeps a hung cancelled start stopped and pending for reconciliation', async () => {
  const pendingStart = deferred<string | null>();
  vi.mocked(runtime.startTx).mockReturnValueOnce(pendingStart.promise);
  setAutoLanModInputEnabled(true);
  setState({ main: receiver(1), data1ModInput: 0 });
  const control = getTxAudioControl();

  const start = control.startTx();
  control.stopLocalAudio();
  control.stopLocalAudio();

  expect(runtime.stopTx).toHaveBeenCalledTimes(1);
  expect(sendCommand).not.toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
  expect(pendingInStorage()).toBeNull();

  // Settle only to keep this module-level adapter isolated from later files.
  pendingStart.resolve('test cleanup');
  await expect(start).resolves.toBe('test cleanup');
  expect(runtime.stopTx).toHaveBeenCalledTimes(2);
  confirmOff();
  expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
});
