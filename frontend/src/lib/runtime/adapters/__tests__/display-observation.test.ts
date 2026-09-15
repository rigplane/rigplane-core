import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { IC7300_CAPABILITIES as capsFixture, IC7300_STATE as stateFixture } from './fixtures/ic7300-profile';
import { qualifyDisplayObservation, qualifyRadioDisplayObservation } from '../display-observation';

const fresh: FieldStatus = {
  storePath: 'main.rf_gain', observed: true, freshness: 'fresh', availability: 'available',
  lastObservedMonotonic: 310658.42975425,
};
const caps = { ...capsFixture, stateContractVersion: 1, providerGeneration: 1, receivers: 1 } as Capabilities;
const snapshot = (status: Partial<FieldStatus> = {}): ServerState => ({
  ...stateFixture, stateContractVersion: 1, providerGeneration: 1, active: 'MAIN',
  main: { ...stateFixture.main, rfGain: 0 },
  fieldStatus: { 'main.rfGain': { ...fresh, ...status } },
} as ServerState);
function project<T extends number | string | boolean>(
  value: T | undefined, state: ServerState | null = snapshot(),
  capabilities: Capabilities | null = caps, structural = true,
) {
  return qualifyDisplayObservation({ state, caps: capabilities, receiver: 'MAIN', path: 'main.rfGain', structural, value });
}

describe('stateless display observation qualifier', () => {
  it.each([0, false, 0.5, 'USB'] as const)('retains current and stale %s without truthiness coercion', (value) => {
    expect(project(value)).toEqual({ state: 'current', value });
    expect(project(value, snapshot({ freshness: 'stale', availability: 'stale' })))
      .toEqual({ state: 'stale', value });
  });
  it('separates structural absence and never-observed defaults', () => {
    expect(project(0, snapshot(), caps, false)).toEqual({ state: 'unsupported' });
    expect(project(0, snapshot({ observed: false }))).toEqual({ state: 'unknown', reason: 'not-observed' });
    expect(project(0, { ...snapshot(), fieldStatus: {} })).toEqual({ state: 'unknown', reason: 'not-observed' });
  });
  it.each([undefined, NaN, Infinity, ''] as const)('rejects invalid value %s', (value) => {
    expect(project(value)).toEqual({ state: 'unknown', reason: 'invalid-value' });
  });
  it.each([undefined, null, NaN, Infinity, -1])('rejects invalid observation marker %s', (lastObservedMonotonic) => {
    expect(project(0, snapshot({ lastObservedMonotonic })))
      .toEqual({ state: 'unknown', reason: 'invalid-evidence' });
  });
  it('accepts fractional seconds without rounding and lets explicit stale ancestors downgrade a fresh leaf', () => {
    const state = snapshot();
    state.fieldStatus!.main = { ...fresh, freshness: 'stale', availability: 'stale' };
    expect(project(0, state)).toEqual({ state: 'stale', value: 0 });
    expect(state.fieldStatus!['main.rfGain'].lastObservedMonotonic).toBe(310658.42975425);
  });
  it.each([{ observed: false }, { availability: 'missing' as const }])('vetoes an explicitly unobserved parent: %j', (status) => {
    const state = snapshot();
    state.fieldStatus!.main = { ...fresh, ...status };
    expect(project(0, state).state).toBe('unknown');
  });
  it('does not use a parent observation as evidence for an absent leaf', () => {
    const state = { ...snapshot(), fieldStatus: { main: fresh } };
    expect(project(0, state)).toEqual({ state: 'unknown', reason: 'not-observed' });
  });
  it('checks all explicit ancestors rather than allowing a fresh nearest parent to bypass an older stale ancestor', () => {
    const state = snapshot();
    state.fieldStatus!['main.nested'] = fresh;
    state.fieldStatus!['main.nested.value'] = fresh;
    state.fieldStatus!.main = { ...fresh, freshness: 'stale' };
    expect(qualifyDisplayObservation({ state, caps, receiver: 'MAIN', path: 'main.nested.value', structural: true, value: 0 }))
      .toEqual({ state: 'stale', value: 0 });
  });
  it.each([
    null, { ...caps, providerGeneration: 2 }, { ...caps, providerGeneration: undefined },
    { ...caps, stateContractVersion: undefined },
  ])('fails closed on capability identity %j', (capabilities) => {
    expect(project(0, snapshot(), capabilities)).toEqual({ state: 'unknown', reason: 'identity-unresolved' });
  });
  it('does not reuse values across reset, missing generation or contradictory receiver topology', () => {
    expect(project(0.7)).toEqual({ state: 'current', value: 0.7 });
    for (const state of [null, { ...snapshot(), providerGeneration: undefined }, { ...snapshot(), active: 'SUB' as const }]) {
      expect(project(0.7, state)).toEqual({ state: 'unknown', reason: 'identity-unresolved' });
    }
    expect(project(0.2, snapshot({ freshness: 'stale' }))).toEqual({ state: 'stale', value: 0.2 });
  });
});

describe('radio-wide display observation qualifier (MOR-2359)', () => {
  const radioState = (status: Partial<FieldStatus> = {}): ServerState => ({
    ...snapshot(), fieldStatus: { powerMeter: { ...fresh, ...status } },
  });
  const qualify = (state: ServerState | null = radioState(), capabilities: Capabilities | null = caps) =>
    qualifyRadioDisplayObservation({ state, caps: capabilities, path: 'powerMeter', structural: true, value: 0 });

  it('qualifies a fractional marker and zero without any receiver evidence', () => {
    expect(qualify({ ...radioState(), active: undefined, main: undefined } as unknown as ServerState))
      .toEqual({ state: 'current', value: 0 });
    expect(qualify(radioState({ freshness: 'stale' }))).toEqual({ state: 'stale', value: 0 });
    expect(qualify(radioState({ availability: 'stale' }))).toEqual({ state: 'stale', value: 0 });
  });
  it.each([null, undefined, -1, NaN, Infinity, 0.5, Number.MAX_SAFE_INTEGER + 1])(
    'rejects invalid matching provider generation %s', (providerGeneration) => {
      expect(qualify({ ...radioState(), providerGeneration } as ServerState, { ...caps, providerGeneration } as Capabilities))
        .toEqual({ state: 'unknown', reason: 'identity-unresolved' });
    },
  );
  it('requires both matching contract identities', () => {
    for (const state of [null, { ...radioState(), stateContractVersion: undefined }]) {
      expect(qualify(state)).toEqual({ state: 'unknown', reason: 'identity-unresolved' });
    }
    for (const capabilities of [null, { ...caps, providerGeneration: 2 }, { ...caps, stateContractVersion: undefined }]) {
      expect(qualify(radioState(), capabilities)).toEqual({ state: 'unknown', reason: 'identity-unresolved' });
    }
  });
  it('does not accept receiver paths through the radio-wide entry point', () => {
    expect(qualifyRadioDisplayObservation({ state: snapshot(), caps, path: 'main.rfGain', structural: true, value: 0 }))
      .toEqual({ state: 'unknown', reason: 'identity-unresolved' });
  });
  it.each([
    [{}, 'not-observed'], [{ powerMeter: { ...fresh, observed: false } }, 'not-observed'],
    [{ powerMeter: { ...fresh, lastObservedMonotonic: null } }, 'invalid-evidence'],
    [{ powerMeter: { ...fresh, availability: 'missing' } }, 'invalid-evidence'],
  ] as const)('shares evidence validation for %j', (fieldStatus, reason) => {
    expect(qualify({ ...radioState(), fieldStatus })).toEqual({ state: 'unknown', reason });
  });
  it('leaves stale receiver-scoped RF gain qualification unchanged', () => {
    expect(project(0.7, snapshot({ freshness: 'stale' }))).toEqual({ state: 'stale', value: 0.7 });
    expect(qualifyDisplayObservation({ state: radioState(), caps, receiver: 'MAIN', path: 'powerMeter', structural: true, value: 0 }))
      .toEqual({ state: 'unknown', reason: 'identity-unresolved' });
  });
});
