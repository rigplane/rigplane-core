import { describe, it, expect, vi, beforeEach } from 'vitest';

// MOR-498: the NB-depth slider is 1-10 (front-panel scale) but the CI-V wire
// value is 0-9.  The DSP handler must convert display -> wire before sending,
// and the optimistic store patch must hold the *wire* value so it matches the
// polled/NB-B readback (which the prop adapter scales wire -> display).
// Storing display optimistically would double-convert and flicker.

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => null),
  getRadioState: vi.fn(() => ({ active: 'MAIN' })),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: vi.fn(),
    startRx: vi.fn(),
    stopRx: vi.fn(),
    setRxVolume: vi.fn(),
    rxEnabled: false,
  },
}));

import { sendCommand } from '$lib/transport/ws-client';
import { patchRadioState } from '$lib/stores/radio.svelte';
import { makeDspHandlers as makeBusDspHandlers } from '../command-bus';
import { makeDspHandlers as makeRuntimeDspHandlers } from '$lib/runtime/commands/panel-commands';

beforeEach(() => {
  vi.mocked(sendCommand).mockClear();
  vi.mocked(patchRadioState).mockClear();
});

describe.each([
  ['command-bus', makeBusDspHandlers],
  ['runtime panel-commands', makeRuntimeDspHandlers],
])('onNbDepthChange (%s)', (_name, makeHandlers) => {
  it('converts the 1-10 slider value to the 0-9 wire value before sending', () => {
    makeHandlers().onNbDepthChange(6);
    expect(sendCommand).toHaveBeenCalledWith('set_nb_depth', { level: 5 });
  });

  it('maps slider minimum 1 to wire 0', () => {
    makeHandlers().onNbDepthChange(1);
    expect(sendCommand).toHaveBeenCalledWith('set_nb_depth', { level: 0 });
  });

  it('maps slider maximum 10 to wire 9', () => {
    makeHandlers().onNbDepthChange(10);
    expect(sendCommand).toHaveBeenCalledWith('set_nb_depth', { level: 9 });
  });

  it('stores the wire value optimistically (matches polled readback units)', () => {
    makeHandlers().onNbDepthChange(6);
    expect(patchRadioState).toHaveBeenCalledWith({ nbDepth: 5 });
  });
});
