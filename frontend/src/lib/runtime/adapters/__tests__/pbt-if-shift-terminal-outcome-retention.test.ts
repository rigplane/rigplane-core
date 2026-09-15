/**
 * MOR-2425 regression — `getIfShiftControlFeedback`'s derived outcome across
 * the real `commands.svelte` store's `OUTCOME_RETENTION_MS` window.
 *
 * The sibling `pbt-if-shift-command-feedback.isolated.test.ts` drives a
 * hand-fed lifecycle array with no timers, so it cannot reach a race between
 * a record's own retirement and the merge in `panel-adapters.ts:
 * mergeTerminalOutcome`. This file drives the real store with fake timers.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const runtimeState: { state: ServerState | null; caps: Capabilities | null } = { state: null, caps: null };
const controlSession = { state: 'connected' as const, epoch: 7 };
const radioStore: { current: { providerGeneration: number } | null } = { current: null };

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return runtimeState.state; },
    get caps() { return runtimeState.caps; },
    get controlSession() { return controlSession; },
  },
}));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({
  toRadioViewModel: () => ({
    activeReceiver: { status: 'known', receiver: 'MAIN' },
    receiverIndicators: [{ receiver: 'MAIN', availability: { structural: true, operational: true } }],
  }),
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: () => radioStore.current,
  subscribeRadioState: () => () => {},
}));

const PBT_SCALE = { raw_center: 128, display_min: -1200, display_max: 1200 };
const fresh = (marker = 5) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
const fixtureState = (): ServerState => ({
  stateContractVersion: 1, providerGeneration: 3, active: 'MAIN',
  main: { pbtInner: 131, pbtOuter: 140, ifShift: 60 },
  sub: { pbtInner: 132, pbtOuter: 141, ifShift: 61 },
  fieldStatus: {
    'main.pbtInner': fresh(), 'main.pbtOuter': fresh(), 'main.ifShift': fresh(),
    'sub.pbtInner': fresh(), 'sub.pbtOuter': fresh(), 'sub.ifShift': fresh(),
  },
} as unknown as ServerState);
const pbtCaps = (): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 3, receivers: 1, vfoScheme: 'single',
  capabilities: ['pbt'],
  controls: { pbt_inner: PBT_SCALE },
} as unknown as Capabilities);

describe('IF-shift derived outcome across OUTCOME_RETENTION_MS (MOR-2425 regression)', () => {
  let store: typeof import('$lib/stores/commands.svelte');
  let getIfShiftControlFeedback: typeof import('../panel-adapters').getIfShiftControlFeedback;

  beforeEach(async () => {
    vi.useFakeTimers();
    vi.resetModules();
    store = await import('$lib/stores/commands.svelte');
    ({ getIfShiftControlFeedback } = await import('../panel-adapters'));
    runtimeState.state = fixtureState();
    runtimeState.caps = pbtCaps();
    radioStore.current = { providerGeneration: 3 };
  });

  afterEach(() => {
    store.resetCommandLifecycle();
    runtimeState.state = null; runtimeState.caps = null; radioStore.current = null;
    vi.useRealTimers();
  });

  it('does not surface confirmed once the failed half retires, then settles to idle once both retire', () => {
    const { PBT_INNER_COMMAND_DESCRIPTOR, PBT_OUTER_COMMAND_DESCRIPTOR } = store;
    store.beginCommand({
      id: PBT_INNER_COMMAND_DESCRIPTOR.intentName, name: PBT_INNER_COMMAND_DESCRIPTOR.intentName,
      params: { value: 90, receiver: 0 }, originalEpoch: 7,
    });
    store.beginCommand({
      id: PBT_OUTER_COMMAND_DESCRIPTOR.intentName, name: PBT_OUTER_COMMAND_DESCRIPTOR.intentName,
      params: { value: 150, receiver: 0 }, originalEpoch: 7,
    });
    store.acknowledgeCommand(PBT_INNER_COMMAND_DESCRIPTOR.intentName, 7, 1);
    store.acknowledgeCommand(PBT_OUTER_COMMAND_DESCRIPTOR.intentName, 7, 1);
    store.failCommand(PBT_INNER_COMMAND_DESCRIPTOR.intentName, 7, 1, 'echo mismatch');

    vi.advanceTimersByTime(300);
    store.confirmCommand(PBT_OUTER_COMMAND_DESCRIPTOR.intentName, 7, 1);

    const atConfirm = getIfShiftControlFeedback();
    expect(atConfirm.phase).toBe('failed');
    expect(atConfirm.outcome).toMatchObject({ phase: 'failed' });

    // Inner failed at t=0 (relative); its own OUTCOME_RETENTION_MS timer
    // fires 5s after THAT transition, independent of outer's t=300ms confirm
    // (whose own timer fires at t=5300ms). Land strictly between the two.
    vi.advanceTimersByTime(5_000 - 300 + 1);
    const afterInnerRetires = getIfShiftControlFeedback();
    expect(afterInnerRetires.phase).not.toBe('confirmed');
    expect(afterInnerRetires.outcome).toBeNull();
    expect(afterInnerRetires.transitionId).toBeNull();
    expect(afterInnerRetires.lifecycleId).toBeNull();

    vi.advanceTimersByTime(300);
    const afterBothRetire = getIfShiftControlFeedback();
    expect(afterBothRetire.phase).toBe('idle');
    expect(afterBothRetire.outcome).toBeNull();
  });
});
