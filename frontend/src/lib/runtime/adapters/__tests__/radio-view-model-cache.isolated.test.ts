import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fixtureById } from '../../../../../fixtures/catalog';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';

beforeEach(() => {
  vi.stubEnv('DEV', false);
  vi.resetModules();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

const fixture = () => {
  const source = fixtureById('topology-2-main-sub')!;
  return { state: structuredClone(source.state()!) as ServerState,
    caps: structuredClone(source.caps()!) as Capabilities };
};

const unavailable: FieldStatus = {
  storePath: 'main.freqHz', observed: false, freshness: 'unknown', availability: 'missing',
};

describe('production bare radio view projection cache', () => {
  it('reuses one immutable snapshot but recomputes for fresh field status and capabilities', async () => {
    const { toRadioViewModel } = await import('../radio-view-model-adapter');
    const { state, caps } = fixture();
    const first = toRadioViewModel(state, caps)!;
    expect(toRadioViewModel(state, caps)).toBe(first);
    expect(first.vfos.find((vfo) => vfo.receiver === 'MAIN')?.display?.frequencyHz.state).toBe('current');

    const nextState = { ...state, fieldStatus: {
      ...state.fieldStatus, 'main.freqHz': unavailable,
    } } as ServerState;
    const unobserved = toRadioViewModel(nextState, caps)!;
    expect(unobserved).not.toBe(first);
    expect(unobserved.vfos.find((vfo) => vfo.receiver === 'MAIN')?.display?.frequencyHz.state).toBe('unknown');
    expect(toRadioViewModel(nextState, caps)).toBe(unobserved);

    const nextCaps = { ...caps, capabilities: caps.capabilities.filter((tag) => tag !== 'dual_rx') };
    const changedCaps = toRadioViewModel(nextState, nextCaps)!;
    expect(changedCaps).not.toBe(unobserved);
    expect(changedCaps.disabledReasons).toContainEqual({ field: 'receiver.SUB', code: 'capability-unavailable' });
    expect(toRadioViewModel(nextState, caps)).not.toBe(unobserved);
  });

  it('does not reuse bare output for TX, RX audio, or scope inputs', async () => {
    const { toRadioViewModel } = await import('../radio-view-model-adapter');
    const { state, caps } = fixture();
    const meteredState = { ...state, main: { ...state.main, sMeter: 42 }, fieldStatus: {
      ...state.fieldStatus,
      'main.sMeter': { storePath: 'main.sMeter', observed: true, freshness: 'fresh',
        availability: 'available', lastObservedMonotonic: 1 },
    } } as ServerState;
    const bare = toRadioViewModel(meteredState, caps)!;
    const receiving = toRadioViewModel(meteredState, caps, { radioTx: 'off', txRisk: 'none' })!;
    const transmitting = toRadioViewModel(meteredState, caps, { radioTx: 'on', txRisk: 'confirmed-on' })!;
    expect(bare.meters).toBeUndefined();
    expect(receiving.meters?.rfState).toBe('receiving');
    expect(transmitting.meters?.rfState).toBe('transmitting');

    const audio = { muted: false, rxEnabled: true, volume: 40, connected: true, routing: null };
    const scope = { source: 'hardware' as const, available: true, resourceSelected: false,
      demand: 0, lifecycle: 'inactive' as const, transport: 'disconnected' as const,
      frameSeen: false, isPoweredOff: false, hardwareConnected: false };
    const full = toRadioViewModel(meteredState, caps, { radioTx: 'off', txRisk: 'none' }, audio, scope)!;
    const muted = toRadioViewModel(meteredState, caps, { radioTx: 'off', txRisk: 'none' },
      { ...audio, muted: true }, scope)!;
    const poweredOff = toRadioViewModel(meteredState, caps, { radioTx: 'off', txRisk: 'none' },
      audio, { ...scope, isPoweredOff: true })!;
    expect(full.rxAudio).toBeDefined();
    expect(muted.rxAudio).not.toEqual(full.rxAudio);
    expect(poweredOff.scopeDisplay).not.toEqual(full.scopeDisplay);

    const audioOnly = toRadioViewModel(meteredState, caps, undefined, audio)!;
    const mutedAudioOnly = toRadioViewModel(meteredState, caps, undefined, { ...audio, muted: true })!;
    expect(audioOnly).not.toBe(bare);
    expect(audioOnly.rxAudio?.monitorMode).toBe('live');
    expect(mutedAudioOnly.rxAudio?.monitorMode).toBe('mute');
    expect(audioOnly.meters).toBeUndefined();

    const scopeOnly = toRadioViewModel(meteredState, caps, undefined, undefined, scope)!;
    const poweredOffScopeOnly = toRadioViewModel(meteredState, caps, undefined, undefined,
      { ...scope, isPoweredOff: true })!;
    expect(scopeOnly).not.toBe(bare);
    expect(scopeOnly.scopeDisplay).toBeDefined();
    expect(poweredOffScopeOnly.scopeDisplay).not.toEqual(scopeOnly.scopeDisplay);
    expect(scopeOnly.rxAudio).toBeUndefined();

    const explicitNullAudio = toRadioViewModel(meteredState, caps, undefined, null)!;
    expect(explicitNullAudio).not.toBe(bare);
    expect(explicitNullAudio.rxAudio).toBeUndefined();
    expect(toRadioViewModel(meteredState, caps)).toBe(bare);
  });

  it('keeps null capability and disconnected state behavior', async () => {
    const { toRadioViewModel } = await import('../radio-view-model-adapter');
    const { state, caps } = fixture();
    expect(toRadioViewModel(state, null)).toBeNull();
    const disconnected = toRadioViewModel(null, caps)!;
    expect(disconnected.activeReceiver.status).toBe('unknown');
    expect(toRadioViewModel(state, caps)).not.toBe(disconnected);
  });
});
