/**
 * Opt-in auto LAN MOD-input for network voice TX (MOR-618, T4 of epic MOR-614).
 *
 * The feature is OFF by default — default UX stays the MOR-617 warn +
 * one-click guard. When the user opts in:
 *   - at web TX start, if the active DATA group's MOD-input source is known
 *     and != LAN(5): remember the previous source, set LAN via the existing
 *     per-group SET command, and suppress the MOR-617 warning (the
 *     optimistic LAN patch preempts the guard);
 *   - at TX stop, restore the remembered source (only if auto changed it,
 *     and only if the group is still on LAN);
 *   - the pending restore is persisted so a crash/disconnect mid-TX can be
 *     repaired best-effort on the next connect.
 *
 * When OFF, behavior is exactly MOR-617 (warn-only, no silent changes).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(() => true),
  isConnected: vi.fn(() => true),
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

import { sendCommand, isConnected } from '$lib/transport/ws-client';
import { runtime } from '$lib/runtime/frontend-runtime';
import { getRadioState, resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import {
  AUTO_LAN_PREF_KEY,
  PENDING_RESTORE_KEY,
  applyPendingModInputRestoreOnConnect,
  deriveAutoLanModInputProps,
  isAutoLanModInputEnabled,
  restoreModInputAfterTx,
  setAutoLanModInputEnabled,
} from '../mod-input-auto.svelte';
import {
  deriveModInputTxGuardProps,
  dismissModInputTxGuard,
} from '../mod-input-tx-guard.svelte';
import { getTxAudioControl } from '../tx-adapter';
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
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: receiver(0),
    sub: receiver(0),
    ...overrides,
  } as unknown as ServerState;
}

function setState(overrides: Record<string, unknown> = {}): void {
  setRadioState(makeState(overrides));
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

beforeEach(() => {
  // Drain any pending restore left by a previous test, then wipe traces.
  restoreModInputAfterTx();
  localStorage.clear();
  vi.mocked(sendCommand).mockClear();
  vi.mocked(sendCommand).mockReturnValue(true);
  vi.mocked(isConnected).mockReturnValue(true);
  vi.mocked(runtime.startTx).mockClear();
  vi.mocked(runtime.startTx).mockResolvedValue(null);
  vi.mocked(runtime.stopTx).mockClear();
  resetRadioState();
  setCapabilities({ capabilities: ['data_mode'] } as never);
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

    setCapabilities({ capabilities: [] } as never);
    expect(deriveAutoLanModInputProps().available).toBe(false);

    setCapabilities({ capabilities: ['data_mode'] } as never);
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

    getTxAudioControl().stopTx();
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
    // Pending restore persisted for crash robustness.
    expect(pendingInStorage()).toEqual({
      command: 'set_data1_mod_input',
      key: 'data1ModInput',
      source: 0,
    });
    // MOR-624: backend session-teardown net armed alongside the LAN set.
    expect(sendCommand).toHaveBeenCalledWith('arm_mod_input_restore', {
      command: 'set_data1_mod_input',
      source: 0,
    });
  });

  it('routes to the ACTIVE receiver group (SUB on D2)', async () => {
    setAutoLanModInputEnabled(true);
    setState({
      active: 'SUB',
      main: receiver(0),
      sub: receiver(2),
      dataOffModInput: 5,
      data2ModInput: 3,
    });

    await getTxAudioControl().startTx();

    expect(sendCommand).toHaveBeenCalledWith('set_data2_mod_input', { source: 5 });
    // MOR-624: the armed backend net carries the same group + previous source.
    expect(sendCommand).toHaveBeenCalledWith('arm_mod_input_restore', {
      command: 'set_data2_mod_input',
      source: 3,
    });
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
    setCapabilities({ capabilities: [] } as never);
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

describe('restore at TX stop (MOR-618)', () => {
  it('restores the remembered source and clears the pending restore', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    await getTxAudioControl().startTx();
    vi.mocked(sendCommand).mockClear();

    getTxAudioControl().stopTx();

    expect(runtime.stopTx).toHaveBeenCalledTimes(1);
    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    // MOR-624: a clean stop owns the restore — backend teardown net cleared.
    expect(sendCommand).toHaveBeenCalledWith('disarm_mod_input_restore', {});
    expect(getRadioState()?.data1ModInput).toBe(0);
    expect(pendingInStorage()).toBeNull();

    // Restore is one-shot — a second stop sends nothing.
    vi.mocked(sendCommand).mockClear();
    getTxAudioControl().stopTx();
    expect(sendCommand).not.toHaveBeenCalled();
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

    getTxAudioControl().stopTx();

    // The manual choice wins: no restore SET — but the backend teardown net
    // is still cleared (MOR-624), the clean stop owns the restore decision.
    expect(sendCommand).toHaveBeenCalledTimes(1);
    expect(sendCommand).toHaveBeenCalledWith('disarm_mod_input_restore', {});
    expect(sendCommand).not.toHaveBeenCalledWith('set_data1_mod_input', expect.anything());
    expect(pendingInStorage()).toBeNull();
  });

  it('still restores when the toggle was turned OFF mid-TX', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    await getTxAudioControl().startTx();
    setAutoLanModInputEnabled(false);
    vi.mocked(sendCommand).mockClear();

    getTxAudioControl().stopTx();

    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
  });

  it('restores immediately when TX audio fails to start', async () => {
    setAutoLanModInputEnabled(true);
    vi.mocked(runtime.startTx).mockResolvedValue('TX MIC: capture failed');
    setState({ main: receiver(1), data1ModInput: 0 });

    const err = await getTxAudioControl().startTx();

    expect(err).toBe('TX MIC: capture failed');
    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 5 });
    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(pendingInStorage()).toBeNull();
  });
});

describe('best-effort restore on next connect (MOR-618)', () => {
  it('applies a persisted pending restore once live state shows the group on LAN', () => {
    localStorage.setItem(
      PENDING_RESTORE_KEY,
      JSON.stringify({ command: 'set_data1_mod_input', key: 'data1ModInput', source: 0 }),
    );
    setState({ main: receiver(1), data1ModInput: 5 });

    applyPendingModInputRestoreOnConnect();

    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(pendingInStorage()).toBeNull();
  });

  it('waits for state to arrive before restoring', () => {
    localStorage.setItem(
      PENDING_RESTORE_KEY,
      JSON.stringify({ command: 'set_data1_mod_input', key: 'data1ModInput', source: 0 }),
    );

    applyPendingModInputRestoreOnConnect();
    expect(sendCommand).not.toHaveBeenCalled();

    setState({ main: receiver(1), data1ModInput: 5 });

    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(pendingInStorage()).toBeNull();
  });

  it('drops the pending restore when the group is no longer on LAN', () => {
    localStorage.setItem(
      PENDING_RESTORE_KEY,
      JSON.stringify({ command: 'set_data1_mod_input', key: 'data1ModInput', source: 0 }),
    );
    setState({ main: receiver(1), data1ModInput: 3 });

    applyPendingModInputRestoreOnConnect();

    expect(sendCommand).not.toHaveBeenCalled();
    expect(pendingInStorage()).toBeNull();
  });

  it('does nothing when no pending restore is persisted', () => {
    setState({ main: receiver(1), data1ModInput: 5 });

    applyPendingModInputRestoreOnConnect();

    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('ignores malformed persisted data', () => {
    localStorage.setItem(PENDING_RESTORE_KEY, '{not json');
    setState({ main: receiver(1), data1ModInput: 5 });

    applyPendingModInputRestoreOnConnect();

    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('defers to the in-session clean-stop path while a TX is active', async () => {
    setAutoLanModInputEnabled(true);
    setState({ main: receiver(1), data1ModInput: 0 });
    await getTxAudioControl().startTx();
    vi.mocked(sendCommand).mockClear();

    // A state update arrives mid-TX — the persisted pending must NOT be
    // applied while the in-memory pending (current TX) owns the restore.
    applyPendingModInputRestoreOnConnect();
    setState({ main: receiver(1), data1ModInput: 5 });
    expect(sendCommand).not.toHaveBeenCalled();

    getTxAudioControl().stopTx();
    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 0 });
    expect(pendingInStorage()).toBeNull();
  });
});
