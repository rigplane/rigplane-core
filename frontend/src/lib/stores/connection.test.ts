import { describe, it, expect } from 'vitest';
import {
  setRigConnected,
  getRigConnected,
  setRadioReady,
  getRadioReady,
  setControlConnected,
  getControlConnected,
  setRadioHealth,
  getRadioHealth,
} from './connection.svelte';

describe('connection readiness fields', () => {
  it('rigConnected defaults to false and can be set', () => {
    expect(getRigConnected()).toBe(false);
    setRigConnected(true);
    expect(getRigConnected()).toBe(true);
    setRigConnected(false);
    expect(getRigConnected()).toBe(false);
  });

  it('radioReady defaults to false and can be set', () => {
    expect(getRadioReady()).toBe(false);
    setRadioReady(true);
    expect(getRadioReady()).toBe(true);
    setRadioReady(false);
    expect(getRadioReady()).toBe(false);
  });

  it('controlConnected defaults to false and can be set', () => {
    expect(getControlConnected()).toBe(false);
    setControlConnected(true);
    expect(getControlConnected()).toBe(true);
    setControlConnected(false);
    expect(getControlConnected()).toBe(false);
  });

  it('classified radio health defaults to null and can be set', () => {
    expect(getRadioHealth()).toBeNull();
    setRadioHealth({
      serverReachable: true,
      radioLink: 'connected',
      readiness: 'stalled',
      likelyCause: 'radio_not_responding',
      sinceMs: 2500,
      lastError: null,
    });
    expect(getRadioHealth()?.likelyCause).toBe('radio_not_responding');
    setRadioHealth(null);
    expect(getRadioHealth()).toBeNull();
  });
});
