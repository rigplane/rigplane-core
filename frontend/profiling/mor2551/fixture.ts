import { fixtureById } from '../../fixtures/catalog';
import { mockCapabilities, mockState, mockInfo } from '../../tests/e2e/i18n/fixtures';
// @ts-expect-error Vite supplies the canonical backend-generated synthetic fixture.
import canonical from 'virtual:synthetic-fixture';
import type { FieldStatusPublic, ServerState } from '../../src/lib/types/state';

const catalog = fixtureById('topology-2-main-sub')!;
const topology = catalog.state()!;
export const caps = { ...mockCapabilities, ...catalog.caps()!, model: 'IC-7610', stateContractVersion: 1, providerGeneration: 1 };
export const info = mockInfo;
export const state: ServerState = {
  ...canonical.state, ...structuredClone(mockState), ...topology,
  main: { ...canonical.state.main, ...mockState.main, ...topology.main },
  sub: { ...canonical.state.sub, ...mockState.sub, ...topology.sub },
  split: false, ptt: false, providerGeneration: caps.providerGeneration,
  txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14250000 },
  fieldStatus: {},
};
function valueAt(path: string): unknown {
  return path.split('.').reduce<unknown>((value, key) =>
    value && typeof value === 'object' ? (value as Record<string, unknown>)[key] : undefined, state);
}
for (const [path, original] of Object.entries(canonical.statuses) as [string, FieldStatusPublic][]) {
  const value = valueAt(path);
  state.fieldStatus![path] = value === null || value === undefined ? original : {
    ...original, observed: true, freshness: 'fresh', availability: 'available',
    lastObservedMonotonic: 1000, maxAge: 10,
    source: { source: 'test', provider: 'synthetic', transport: 'offline', nativeId: null,
      capabilityId: null, commandSource: null, sessionId: null },
    quality: ['confirmed'],
  };
}
export function frame(sequence: number): string {
  const fieldStatus = Object.fromEntries(Object.entries(state.fieldStatus!).map(([path, status]) =>
    [path, status.observed ? { ...status, lastObservedMonotonic: 1000 + sequence / 11 } : status]));
  const counters = { revision: 1, stateRevision: 1, freshnessRevision: sequence,
    observationSeq: sequence, transportSeq: sequence, publicStateSeq: sequence };
  const changed = { fieldStatus, updatedAt: new Date(Date.UTC(2026, 0, 1) + sequence * 1000 / 11).toISOString() };
  const envelope = sequence === 1 || sequence % 110 === 0
    ? { type: 'full', data: { ...state, ...changed, ...counters } }
    : { type: 'delta', changed };
  return JSON.stringify({ type: 'state_update', data: {
    ...envelope, ...counters, stateContractVersion: 1, providerGeneration: caps.providerGeneration,
  } });
}
export const frames = Array.from({ length: 400 }, (_, i) => frame(i + 1));
export const smallEvent = JSON.stringify({ type: 'event', name: 'synthetic_observation', data: { receiver: 'MAIN' } });
