/**
 * MOR-2513 — the live-payload ingestion pins.
 *
 * The bench regression: at 86187ed3 the real FTX-1 `/api/v1/state` body —
 * 108 never-observed leaves published as `null` — was silently dropped by
 * `applyDeltaEnvelope` (`lib/transport/ws-client.ts`) because
 * `isValidServerState`'s `validReceiver` demanded non-null primitives for
 * leaves `state.ts` types as nullable (`main.dataMode`, `sub.att`,
 * `sub.preamp`, …). Because the full envelope was dropped before
 * `refreshCapabilities` ran, BOTH stores stayed empty, the view model came
 * back `null`, and the desktop page mounted no radio surfaces at all.
 *
 * These pins run against the REAL store modules (no mocks — unlike the
 * conformance harness, which replaces `$lib/stores/radio.svelte` wholesale)
 * so the acceptance seam itself is exercised: the captured payload must be
 * valid, must match the current capability topology, and must be accepted
 * by `setRadioState` — as-is, and again with every nullable leaf nulled.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import stateJson from '$lib/runtime/adapters/__tests__/fixtures/ftx1-state-unobserved-leaves.json';
import capsJson from '$lib/runtime/adapters/__tests__/fixtures/ftx1-capabilities.json';
import {
  FTX1_STATE,
  FTX1_STATE_FULLY_UNOBSERVED,
} from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';

let store: typeof import('../radio.svelte');
let capabilities: typeof import('../capabilities.svelte');

beforeEach(async () => {
  vi.resetModules();
  store = await import('../radio.svelte');
  capabilities = await import('../capabilities.svelte');
  // The two captures come from different bench sessions (capabilities at
  // backend 60d05a42, state at 86187ed3), so the caps' provider generation
  // is aligned to the state capture's before the epoch gate reads it.
  capabilities.setCapabilities({
    ...capsJson,
    providerGeneration: stateJson.providerGeneration,
  } as Capabilities);
});

describe('MOR-2513 — the live FTX-1 payload is accepted at ingestion', () => {
  it('the capture carries the unobserved-null leaves the bench failed on', () => {
    expect(FTX1_STATE.main!.filter).toBeNull();
    expect(FTX1_STATE.main!.dataMode).toBeNull();
    expect(FTX1_STATE.sub!.att).toBeNull();
    expect(FTX1_STATE.sub!.preamp).toBeNull();
    expect(FTX1_STATE.scopeControls!.receiver).toBeNull();
    expect(FTX1_STATE.fieldStatus!['main.filter']!.observed).toBe(false);
  });

  it('isValidServerState accepts the live payload', () => {
    expect(store.isValidServerState(FTX1_STATE)).toBe(true);
  });

  it('matchesCurrentCapabilityTopology accepts the live payload', () => {
    expect(store.matchesCurrentCapabilityTopology(FTX1_STATE)).toBe(true);
  });

  it('setRadioState stores the live payload', () => {
    expect(store.setRadioState(FTX1_STATE)).toBe(true);
    expect(store.getRadioState()?.main?.filter).toBeNull();
  });
});

describe('MOR-2513 — every nullable leaf nulled is still accepted', () => {
  it('the fully-unobserved variant really nulls the validator-contract leaves', () => {
    expect(FTX1_STATE_FULLY_UNOBSERVED.active).toBeNull();
    expect(FTX1_STATE_FULLY_UNOBSERVED.ptt).toBeNull();
    expect(FTX1_STATE_FULLY_UNOBSERVED.split).toBeNull();
    expect(FTX1_STATE_FULLY_UNOBSERVED.dualWatch).toBeNull();
    expect(FTX1_STATE_FULLY_UNOBSERVED.tunerStatus).toBeNull();
    expect(FTX1_STATE_FULLY_UNOBSERVED.main!.freqHz).toBeNull();
    expect(FTX1_STATE_FULLY_UNOBSERVED.main!.mode).toBeNull();
    expect(FTX1_STATE_FULLY_UNOBSERVED.main!.sMeter).toBeNull();
    expect(FTX1_STATE_FULLY_UNOBSERVED.connection.rigConnected).toBe(true);
  });

  it('isValidServerState accepts the fully-unobserved variant', () => {
    expect(store.isValidServerState(FTX1_STATE_FULLY_UNOBSERVED)).toBe(true);
  });

  it('matchesCurrentCapabilityTopology accepts the fully-unobserved variant', () => {
    expect(store.matchesCurrentCapabilityTopology(FTX1_STATE_FULLY_UNOBSERVED)).toBe(true);
  });

  it('setRadioState stores the fully-unobserved variant', () => {
    expect(store.setRadioState(FTX1_STATE_FULLY_UNOBSERVED)).toBe(true);
    expect(store.getRadioState()?.active).toBeNull();
  });
});
