import { describe, it, expect, vi, beforeEach } from 'vitest';

// MOR-490: the NR slider is 0-15 (front-panel scale) but the CI-V wire value
// is 0-255 BCD.  The DSP handler must convert display -> raw before sending.
// MOR-1409 A09b removed the store's optimistic patch path entirely — the WS
// B2/C reducer is the sole writer, so there is no local overlay to flicker.

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
}));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => ({ nr: false, nrLevel: 0 })),
  getRadioState: vi.fn(() => ({
    active: 'MAIN', main: { nr: false, nrLevel: 0 }, sub: { nr: false, nrLevel: 0 },
  })),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => ({ capabilities: ['nr'], receivers: 2, vfoScheme: 'main_sub' })),
  getControlRange: vi.fn(() => null),
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
import * as radioStore from '$lib/stores/radio.svelte';
import { makeDspHandlers as makeBusDspHandlers } from '../command-bus';
import { makeDspHandlers as makeRuntimeDspHandlers } from '$lib/runtime/commands/panel-commands';

beforeEach(() => {
  vi.mocked(sendCommand).mockClear();
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

  it('has no optimistic store path left to write through (MOR-1409 A09b)', () => {
    makeHandlers().onNrLevelChange(15);
    expect(Object.keys(radioStore)).not.toContain('patchActiveReceiver');
  });
});
