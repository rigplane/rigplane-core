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
  const counters = { revision: 1, stateRevision: 1, freshnessRevision: 1,
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

export function assertSteadyFreshFixture() {
  let previous: ServerState | undefined;
  let fullCount = 0;
  for (const [index, raw] of frames.entries()) {
    const envelope = JSON.parse(raw).data;
    const next = envelope.type === 'full' ? envelope.data : { ...previous, ...envelope.changed };
    if (envelope.type === 'full') fullCount++;
    if (envelope.revision !== 1 || envelope.stateRevision !== 1 || envelope.freshnessRevision !== 1
      || envelope.observationSeq !== index + 1 || envelope.transportSeq !== index + 1
      || envelope.publicStateSeq !== index + 1 || next.ptt !== false) {
      throw new Error(`Steady-fresh RX contract failed at frame ${index + 1}`);
    }
    if (previous && next.updatedAt <= previous.updatedAt) throw new Error('Updated timestamp did not advance');
    for (const [path, status] of Object.entries(next.fieldStatus) as [string, FieldStatusPublic][]) {
      if (!status.observed) continue;
      if (status.freshness !== 'fresh' || status.availability !== 'available'
        || (previous && status.lastObservedMonotonic! <= previous.fieldStatus![path].lastObservedMonotonic!)) {
        throw new Error(`Steady-fresh observation failed at ${path}`);
      }
    }
    previous = next;
  }
  return { frames: frames.length, fullCount, deltaCount: frames.length - fullCount,
    stateRevision: 1, freshnessRevision: 1, ptt: false };
}
