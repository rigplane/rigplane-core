import { describe, it, expect, vi, beforeEach } from 'vitest';

// MOR-490: the NR slider is 0-15 (front-panel scale) but the CI-V wire value
// is 0-255 BCD.  The DSP handler must convert display -> raw before sending,
// and the optimistic store patch must hold the *raw* value so it matches the
// polled readback (which the prop adapter scales raw -> display).  Storing
// display optimistically would double-convert and flicker.

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
import { patchActiveReceiver } from '$lib/stores/radio.svelte';
import { makeDspHandlers as makeBusDspHandlers } from '../command-bus';
import { makeDspHandlers as makeRuntimeDspHandlers } from '$lib/runtime/commands/panel-commands';

beforeEach(() => {
  vi.mocked(sendCommand).mockClear();
  vi.mocked(patchActiveReceiver).mockClear();
});

describe.each([
  ['command-bus', makeBusDspHandlers],
  ['runtime panel-commands', makeRuntimeDspHandlers],
])('onNrLevelChange (%s)', (_name, makeHandlers) => {
  it('converts the 0-15 slider value to the 0-255 wire value before sending', () => {
    makeHandlers().onNrLevelChange(15);
    expect(sendCommand).toHaveBeenCalledWith('set_nr_level', { level: 255, receiver: 0 });
  });

  it('maps the midpoint slider value to the midpoint wire value', () => {
    makeHandlers().onNrLevelChange(8);
    // round(8 * 255 / 15) = 136
    expect(sendCommand).toHaveBeenCalledWith('set_nr_level', { level: 136, receiver: 0 });
  });

  it('maps zero to zero', () => {
    makeHandlers().onNrLevelChange(0);
    expect(sendCommand).toHaveBeenCalledWith('set_nr_level', { level: 0, receiver: 0 });
  });

  it('stores the raw wire value optimistically (matches polled readback units)', () => {
    makeHandlers().onNrLevelChange(15);
    expect(patchActiveReceiver).toHaveBeenCalledWith({ nrLevel: 255 }, true);
  });
});
