import { describe, it, expect, beforeEach } from 'vitest';
import {
  radio,
  setRadioState,
  resetRadioState,
  getRadioState,
  getLastRevision,
  patchActiveReceiver,
} from './radio.svelte';
import type { ServerState } from '../types/state';
import { setCapabilities } from './capabilities.svelte';

function makeState(revision: number): ServerState {
  return {
    revision,
    stateRevision: revision,
    freshnessRevision: 1,
    stateContractVersion: 1,
    providerGeneration: 0,
    active: 'MAIN',
    powerOn: true,
    ptt: false,
    split: false,
    dualWatch: false,
    main: {
      freqHz: 14074000,
      mode: 'USB',
      filter: 1,
      dataMode: 0,
      sMeter: 0,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 50,
      rfGain: 100,
      squelch: 0,
    },
    sub: {
      freqHz: 7074000,
      mode: 'LSB',
      filter: 1,
      dataMode: 0,
      sMeter: 0,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 50,
      rfGain: 100,
      squelch: 0,
    },
  } as ServerState;
}

describe('resetRadioState', () => {
  beforeEach(() => {
    // Ensure clean state
    resetRadioState();
    setCapabilities({
      model: 'TEST', scope: false, audio: false, tx: false, capabilities: [],
      receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
      audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
      webrtc: { available: false, enabled: false }, txBands: null,
      stateContractVersion: 1, providerGeneration: 0,
    });
  });

  it('clears radio.current to null', () => {
    setRadioState(makeState(1));
    expect(radio.current).not.toBeNull();

    resetRadioState();
    expect(radio.current).toBeNull();
  });

  it('resets lastRevision to -1', () => {
    setRadioState(makeState(42));
    expect(getLastRevision()).toBe(42);

    resetRadioState();
    expect(getLastRevision()).toBe(-1);
  });

  it('allows new state to be set after reset', () => {
    setRadioState(makeState(10));
    resetRadioState();

    // After reset, a state with revision=1 should be accepted
    setRadioState(makeState(1));
    expect(radio.current).not.toBeNull();
    expect(getLastRevision()).toBe(1);
  });

  it('getRadioState returns null after reset', () => {
    setRadioState(makeState(5));
    resetRadioState();
    expect(getRadioState()).toBeNull();
  });
});

describe('StateStore-only VFO truth', () => {
  beforeEach(() => {
    resetRadioState();
    setCapabilities({
      model: 'TEST', scope: false, audio: false, tx: false, capabilities: [],
      receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
      audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
      webrtc: { available: false, enabled: false }, txBands: null,
      stateContractVersion: 1, providerGeneration: 0,
    });
  });

  it('does not let VFO optimistic patches overwrite the observed snapshot', () => {
    const initial = makeState(10);
    initial.main.activeSlot = 'A';
    setRadioState(initial);

    patchActiveReceiver({
      freqHz: 14_200_000,
      mode: 'LSB',
      filter: 2,
      dataMode: 1,
      activeSlot: 'B',
    }, true);

    expect(getRadioState()?.main).toMatchObject({
      freqHz: 14_074_000,
      mode: 'USB',
      filter: 1,
      dataMode: 0,
      activeSlot: 'A',
    });

    const observed = makeState(11);
    Object.assign(observed.main, {
      freqHz: 14_123_000,
      mode: 'CW',
      filter: 3,
      dataMode: 1,
      activeSlot: 'B',
    });
    setRadioState(observed);
    expect(getRadioState()?.main).toMatchObject({
      freqHz: 14_123_000,
      mode: 'CW',
      filter: 3,
      dataMode: 1,
      activeSlot: 'B',
    });
  });

  it('retains optimistic support for non-VFO fields outside MOR-1403', () => {
    setRadioState(makeState(20));

    patchActiveReceiver({ afLevel: 42 }, true);
    expect(getRadioState()?.main.afLevel).toBe(42);
  });
});
