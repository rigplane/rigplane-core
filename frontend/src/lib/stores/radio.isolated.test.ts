import { describe, it, expect, beforeEach } from 'vitest';
import { radio, setRadioState, resetRadioState, getRadioState, getLastRevision } from './radio.svelte';
import type { ServerState } from '../types/state';

function makeState(revision: number): ServerState {
  return {
    revision,
    stateRevision: revision,
    freshnessRevision: 1,
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
