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
import { toDspProps as toBusDspProps } from '../state-adapter';
import { toDspProps as toRuntimeDspProps } from '$lib/runtime/props/panel-props';

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

// MOR-502: both prop adapters must gate NB depth/width on the nb_depth control
// range and derive the NB-level scale from the nb_level control range, so the
// FTX-1 (native 0-10 NB, no depth/width) and X6200 (nb_level only) render
// correctly without phantom controls.
describe.each([
  ['command-bus', toBusDspProps],
  ['runtime panel-props', toRuntimeDspProps],
])('toDspProps NB capability gating (%s)', (_name, toDspProps) => {
  const state = { active: 'MAIN', main: {} } as any;

  it('shows NB depth/width only when an nb_depth control range exists (IC-7610)', () => {
    const props = toDspProps(
      state,
      { capabilities: ['nb'], controls: { nb_depth: { raw_min: 0, raw_max: 9 } } } as any,
    );
    expect(props.hasNbDepth).toBe(true);
    expect(props.hasNbWidth).toBe(true);
  });

  it('hides NB depth/width when no nb_depth control range exists (FTX-1, X6200)', () => {
    const props = toDspProps(
      state,
      { capabilities: ['nb'], controls: { nb_level: { raw_min: 0, raw_max: 10 } } } as any,
    );
    expect(props.hasNbDepth).toBe(false);
    expect(props.hasNbWidth).toBe(false);
  });

  it('uses the nb_level raw_max with percent display when a 0-255 range exists (IC-7610)', () => {
    const props = toDspProps(
      state,
      { capabilities: ['nb'], controls: { nb_level: { raw_min: 0, raw_max: 255 } } } as any,
    );
    expect(props.nbLevelMax).toBe(255);
    expect(props.nbLevelPercent).toBe(true);
  });

  it('falls back to the native 0-10 raw scale without an nb_level range (FTX-1)', () => {
    const props = toDspProps(state, { capabilities: ['nb'], controls: {} } as any);
    expect(props.nbLevelMax).toBe(10);
    expect(props.nbLevelPercent).toBe(false);
  });
});
