/**
 * MOD-input TX preflight guard (MOR-617).
 *
 * When network voice TX starts from the web UI and the active DATA group's
 * MOD-input source is not LAN (5), the guard arms a non-blocking warning
 * with a one-click "Set LAN" fix. It must:
 *   - fire on TX-start when the active group's source is known and != LAN;
 *   - NOT fire when the source is LAN, unknown (null), gated off by a
 *     missing fieldStatus, or the radio lacks the data_mode capability;
 *   - send the active group's SET command with source=5 on one-click;
 *   - clear reactively when the source becomes LAN (readback);
 *   - never block or alter the TX path (tx-adapter still delegates).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
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
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import {
  armModInputTxGuard,
  deriveModInputTxGuardProps,
  dismissModInputTxGuard,
  getModInputTxGuardHandlers,
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

function makeState(overrides: Record<string, unknown> = {}): ServerState {
  return {
    revision: 1,
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

beforeEach(() => {
  vi.mocked(sendCommand).mockClear();
  vi.mocked(runtime.startTx).mockClear();
  vi.mocked(runtime.stopTx).mockClear();
  resetRadioState();
  setCapabilities({ capabilities: ['data_mode'] } as never);
  dismissModInputTxGuard();
});

describe('armModInputTxGuard (MOR-617)', () => {
  it('fires when the active group source is known and not LAN', () => {
    setState({ main: receiver(1), data1ModInput: 0 });

    armModInputTxGuard();

    const props = deriveModInputTxGuardProps();
    expect(props.visible).toBe(true);
    expect(props.sourceLabel).toBe('MIC');
  });

  it('does not fire when the source is LAN', () => {
    setState({ main: receiver(1), data1ModInput: 5 });

    armModInputTxGuard();

    expect(deriveModInputTxGuardProps().visible).toBe(false);
  });

  it('does not fire when the source is unknown (null)', () => {
    setState({ main: receiver(1), data1ModInput: null });

    armModInputTxGuard();

    expect(deriveModInputTxGuardProps().visible).toBe(false);
  });

  it('does not fire when fieldStatus marks the group missing', () => {
    setState({
      main: receiver(1),
      data1ModInput: 0,
      fieldStatus: { data1ModInput: missingStatus() },
    });

    armModInputTxGuard();

    expect(deriveModInputTxGuardProps().visible).toBe(false);
  });

  it('does not fire without the data_mode capability', () => {
    setCapabilities({ capabilities: [] } as never);
    setState({ main: receiver(1), data1ModInput: 0 });

    armModInputTxGuard();

    expect(deriveModInputTxGuardProps().visible).toBe(false);
  });

  it('does not fire without radio state', () => {
    armModInputTxGuard();

    expect(deriveModInputTxGuardProps().visible).toBe(false);
  });

  it('resolves the ACTIVE receiver group (SUB on D2)', () => {
    setState({
      active: 'SUB',
      main: receiver(0),
      sub: receiver(2),
      dataOffModInput: 5,
      data2ModInput: 3,
    });

    armModInputTxGuard();

    const props = deriveModInputTxGuardProps();
    expect(props.visible).toBe(true);
    expect(props.sourceLabel).toBe('USB');
  });
});

describe('guard clearing (MOR-617)', () => {
  it('clears when readback reports the source became LAN', () => {
    setState({ main: receiver(1), data1ModInput: 0 });
    armModInputTxGuard();
    expect(deriveModInputTxGuardProps().visible).toBe(true);

    setRadioState(makeState({ revision: 2, main: receiver(1), data1ModInput: 5 }));

    expect(deriveModInputTxGuardProps().visible).toBe(false);
  });

  it('clears on dismiss', () => {
    setState({ main: receiver(1), data1ModInput: 0 });
    armModInputTxGuard();
    expect(deriveModInputTxGuardProps().visible).toBe(true);

    getModInputTxGuardHandlers().onDismiss();

    expect(deriveModInputTxGuardProps().visible).toBe(false);
  });
});

describe('one-click Set LAN (MOR-617)', () => {
  it('sends the active group SET command with source=5', () => {
    setState({ main: receiver(1), data1ModInput: 0 });
    armModInputTxGuard();

    getModInputTxGuardHandlers().onSetLan();

    expect(sendCommand).toHaveBeenCalledWith('set_data1_mod_input', { source: 5 });
  });

  it('routes to the DATA OFF command when DATA is off', () => {
    setState({ main: receiver(0), dataOffModInput: 3 });
    armModInputTxGuard();

    getModInputTxGuardHandlers().onSetLan();

    expect(sendCommand).toHaveBeenCalledWith('set_data_off_mod_input', { source: 5 });
  });
});

describe('tx-adapter TX-start hook (MOR-617)', () => {
  it('arms the guard at startTx and still delegates to runtime', async () => {
    setState({ main: receiver(1), data1ModInput: 0 });

    await getTxAudioControl().startTx();

    expect(runtime.startTx).toHaveBeenCalledTimes(1);
    expect(deriveModInputTxGuardProps().visible).toBe(true);
  });

  it('does not arm when the source is already LAN', async () => {
    setState({ main: receiver(1), data1ModInput: 5 });

    await getTxAudioControl().startTx();

    expect(runtime.startTx).toHaveBeenCalledTimes(1);
    expect(deriveModInputTxGuardProps().visible).toBe(false);
  });
});
