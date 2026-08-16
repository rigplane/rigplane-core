/** MOR-1664 — Filter Width lifecycle projection. */
import { afterEach, describe, expect, it, vi } from 'vitest';
type FakeCommand = { id: string; name: string; params: Record<string, unknown>;
  originalEpoch: number; eventEpoch?: number; createdAt: number;
  status: 'pending' | 'acknowledged' | 'confirmed' | 'failed' | 'cancelled' | 'timed-out';
  error?: string;
  ackObservationSeq?: number;
  ackFieldObservationTimes?: Record<string, number>;
};
type FakeState = {
  active: 'MAIN' | 'SUB';
  providerGeneration?: number;
  breakInDelay?: number;
  main: Record<string, unknown>;
  sub: Record<string, unknown>;
  observationSeq?: number;
  fieldStatus?: Record<string, { observed?: boolean; freshness?: string; availability?: string; lastObservedMonotonic?: number | null }>;
};
const lifecycle = { commands: [] as FakeCommand[], superseded: new Set<string>() };
const runtimeState: { state: FakeState | null } = { state: null };
const controlSession = vi.hoisted(() => ({ epoch: 7 }));
const dispatch = vi.hoisted(() => vi.fn());
const commandRadio = vi.hoisted(() => ({
  current: null as Record<string, unknown> | null,
  listeners: new Set<(value: Record<string, unknown> | null) => void>(),
}));
vi.mock('$lib/stores/commands.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/stores/commands.svelte')>();
  return {
    ...actual,
    getCommandLifecycles: () => lifecycle.commands,
    isCommandLifecycleSuperseded: (command: FakeCommand) => lifecycle.superseded.has(command.id),
  };
});
vi.mock('$lib/runtime/commands/radio-intents', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/radio-intents')>();
  return {
    ...actual,
    currentControlSessionEpoch: () => controlSession.epoch,
    dispatchRadioIntent: dispatch,
  };
});
vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: () => commandRadio.current,
  subscribeRadioState: (handler: (value: Record<string, unknown> | null) => void) => {
    commandRadio.listeners.add(handler);
    handler(commandRadio.current);
    return () => commandRadio.listeners.delete(handler);
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: { get state() { return runtimeState.state; }, get caps() { return null; } },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({ getAppTxController: () => null }));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({ toRadioViewModel: () => null }));

import {
  FILTER_WIDTH_FEEDBACK_DESCRIPTOR,
  getBreakInDelayControlFeedback,
  getFilterWidthCommandLifecycle,
} from '../panel-adapters';
import {
  BREAK_IN_DELAY_COMMAND_DESCRIPTOR, FILTER_WIDTH_COMMAND_DESCRIPTOR,
  STATE_BACKED_COMMAND_DESCRIPTORS,
  beginCommand, getStateBackedCommandDescriptor,
} from '$lib/stores/commands.svelte';
import {
  RADIO_INTENT_NAMES, currentControlSessionEpoch, dispatchRadioIntent,
} from '$lib/runtime/commands/radio-intents';
function emitAcceptedState(value: FakeState | null): void {
  commandRadio.current = value;
  for (const handler of commandRadio.listeners) handler(value);
}
const command = (over: Partial<FakeCommand> = {}): FakeCommand => ({
  id: 'width-1', name: 'set_filter_width', params: { width: 3000 },
  originalEpoch: controlSession.epoch, eventEpoch: controlSession.epoch,
  createdAt: 1, status: 'pending', ...over,
});
const state = (over: Partial<FakeState> = {}): FakeState => ({
  active: 'MAIN', main: { filterWidth: 2400 }, sub: {}, observationSeq: 4,
  fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 } }, ...over,
});
describe('Filter Width command lifecycle projection (MOR-1664)', () => {
  afterEach(() => {
    vi.useRealTimers();
    lifecycle.commands = [];
    lifecycle.superseded.clear();
    controlSession.epoch = 7;
    runtimeState.state = null;
    commandRadio.current = null;
    commandRadio.listeners.clear();
    dispatch.mockClear();
  });
  it('preserves real module exports while exposing a mutable control-session epoch', () => {
    expect(beginCommand).toBeTypeOf('function');
    expect(dispatchRadioIntent).toBeTypeOf('function');
    expect(currentControlSessionEpoch()).toBe(7);
    controlSession.epoch = 8;
    expect(currentControlSessionEpoch()).toBe(8);
    expect(command()).toMatchObject({ originalEpoch: 8, eventEpoch: 8 });
  });
  it('shares one canonical registered Filter Width descriptor and exact paths', () => {
    expect(FILTER_WIDTH_FEEDBACK_DESCRIPTOR).toBe(FILTER_WIDTH_COMMAND_DESCRIPTOR);
    expect(getStateBackedCommandDescriptor('set_filter_width')).toBe(FILTER_WIDTH_COMMAND_DESCRIPTOR);
    expect([...STATE_BACKED_COMMAND_DESCRIPTORS.keys()]).toEqual([
      'set_filter_width', 'set_break_in_delay',
    ]);
    expect(RADIO_INTENT_NAMES).toContain(FILTER_WIDTH_COMMAND_DESCRIPTOR.intentName);
    const main = FILTER_WIDTH_COMMAND_DESCRIPTOR.scope(command({ params: { width: 3000, receiver: 0 } }))!;
    const sub = FILTER_WIDTH_COMMAND_DESCRIPTOR.scope(command({ params: { width: 2100, receiver: 1 } }))!;
    expect(FILTER_WIDTH_COMMAND_DESCRIPTOR.fieldPath(main)).toBe('main.filterWidth');
    expect(FILTER_WIDTH_COMMAND_DESCRIPTOR.fieldPath(sub)).toBe('sub.filterWidth');
    expect(FILTER_WIDTH_COMMAND_DESCRIPTOR.scope(command({ params: { width: 3000, receiver: 2 } }))).toBeNull();
    expect(FILTER_WIDTH_COMMAND_DESCRIPTOR.target(command({ params: { width: 3000.5 } }))).toBeNull();
    expect(FILTER_WIDTH_COMMAND_DESCRIPTOR.repeatPolicy).toBe('latest-target-wins');
  });
  it('stays unavailable rather than fabricating a pending or confirmed value without observed width', () => {
    runtimeState.state = state({ main: {} });
    lifecycle.commands = [command()];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({
      confirmed: null, target: null, phase: 'unavailable', busy: false, outcome: null,
    });
  });
  it('keeps canonical observed width separate from a submitted target', () => {
    runtimeState.state = state();
    lifecycle.commands = [command()];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({
      confirmed: 2400, target: 3000, phase: 'pending', busy: true, outcome: null,
    });
  });
  it('keeps a fresh matching observation acknowledged when a caller only reads the pure accessor', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, observationSeq: 5, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackObservationSeq: 4, ackFieldObservationTimes: { 'main.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({
      confirmed: 3000, target: 3000, phase: 'acknowledged', busy: true, outcome: null,
    });
  });
  it('does not let an unrelated global observation advance confirm a cached matching width', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, observationSeq: 99,
      fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackObservationSeq: 4, ackFieldObservationTimes: { 'main.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ confirmed: 3000, target: 3000, phase: 'acknowledged', busy: true });
  });
  it('does not confirm a matching width from a reverse or stale field marker', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 3 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackFieldObservationTimes: { 'main.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ target: 3000, phase: 'acknowledged', busy: true });
  });
  it.each([
    ['missing status', undefined],
    ['unobserved status', { observed: false, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 }],
    ['stale status', { observed: true, freshness: 'stale', availability: 'available', lastObservedMonotonic: 5 }],
    ['unavailable status', { observed: true, freshness: 'fresh', availability: 'stale', lastObservedMonotonic: 5 }],
    ['missing marker', { observed: true, freshness: 'fresh', availability: 'available' }],
    ['non-finite marker', { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: Number.NaN }],
  ])('makes the full projection unavailable from %s evidence', (_case, fieldStatus) => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, fieldStatus: fieldStatus === undefined ? {} : { 'main.filterWidth': fieldStatus } });
    lifecycle.commands = [command({ status: 'acknowledged', ackFieldObservationTimes: { 'main.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({
      confirmed: null, target: null, phase: 'unavailable', busy: false, outcome: null,
    });
  });
  it('does not let a legacy record without an ACK field boundary confirm', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    lifecycle.commands = [command({ status: 'acknowledged' })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ target: 3000, phase: 'acknowledged', busy: true });
  });
  it('does not establish a cold boundary from an accessor read', () => {
    runtimeState.state = state({ main: { filterWidth: 2400 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackFieldObservationTimes: {} })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ confirmed: 2400, target: 3000, phase: 'acknowledged', busy: true });
    expect(lifecycle.commands[0].ackFieldObservationTimes).toEqual({});
    runtimeState.state = state({ main: { filterWidth: 3000 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 6 } } });
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ confirmed: 3000, target: 3000, phase: 'acknowledged', busy: true });
  });
  it('does not treat a missing exact field marker in a non-empty ACK map as an empty boundary', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackFieldObservationTimes: { 'sub.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ confirmed: 3000, target: 3000, phase: 'acknowledged', busy: true });
  });
  it('keeps a fresh non-matching observation canonical while the acknowledged target remains pending', () => {
    runtimeState.state = state({ main: { filterWidth: 2400 }, observationSeq: 5, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackObservationSeq: 4, ackFieldObservationTimes: { 'main.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({
      confirmed: 2400, target: 3000, phase: 'acknowledged', busy: true, outcome: null,
    });
  });
  it('does not let an accessor read confirm SUB independently of MAIN', () => {
    runtimeState.state = state({
      active: 'SUB', main: { filterWidth: 3000 }, sub: { filterWidth: 2800 }, observationSeq: 5,
      fieldStatus: { 'sub.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } },
    });
    lifecycle.commands = [command({
      id: 'sub-width', params: { width: 2800, receiver: 1 }, status: 'acknowledged', ackObservationSeq: 4,
      ackFieldObservationTimes: { 'sub.filterWidth': 4 },
    })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({
      confirmed: 2800, target: 2800, phase: 'acknowledged', busy: true, outcome: null,
    });
  });
  it('projects only the newest same-receiver target, so older late events cannot overwrite it', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, observationSeq: 5 });
    lifecycle.commands = [
      command({ id: 'older', createdAt: 1, status: 'acknowledged', ackObservationSeq: 4 }),
      command({ id: 'newer', createdAt: 2, params: { width: 2800, receiver: 0 }, status: 'pending' }),
    ];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({
      confirmed: 3000, target: 2800, phase: 'pending', busy: true, outcome: null,
    });
  });
  it('lets a newer terminal record suppress an older pending target', () => {
    runtimeState.state = state();
    lifecycle.commands = [
      command({ id: 'older', createdAt: 1 }),
      command({ id: 'newer', createdAt: 2, params: { width: 2800, receiver: 0 }, status: 'failed', error: 'rejected' }),
    ];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({
      confirmed: 2400, target: null, phase: 'idle', busy: false,
      outcome: { phase: 'failed', error: 'rejected' },
    });
  });
  it.each([
    ['failed', 'backend rejected'],
    ['timed-out', undefined],
    ['cancelled', 'session-disconnected'],
  ] as const)('clears busy and retains a bounded %s outcome without changing confirmed truth', (status, error) => {
    runtimeState.state = state({ main: { filterWidth: 2400 } });
    lifecycle.commands = [command({ status, error })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({
      confirmed: 2400, target: null, phase: 'idle', busy: false, outcome: { phase: status, error },
    });
  });
  it('captures only finite public-field ACK markers through the real command store seam', async () => {
    vi.useFakeTimers();
    controlSession.epoch = 9;
    vi.doUnmock('$lib/stores/commands.svelte');
    vi.resetModules();
    const fields = Object.fromEntries(Array.from({ length: 198 }, (_, index) => [
      index === 142 ? 'sub.filterWidth' : `other.${index}`,
      { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: index === 142 ? 73 : Number.NaN },
    ]));
    commandRadio.current = { observationSeq: 41, fieldStatus: fields };
    const store = await import('$lib/stores/commands.svelte');
    store.beginCommand({ id: 'real-ack', name: 'set_filter_width', params: { width: 3000, receiver: 1 }, originalEpoch: 9 });
    store.acknowledgeCommand('real-ack', 9, 10);
    const acknowledged = store.getCommandLifecycle('real-ack', 9);
    expect(acknowledged).toMatchObject({
      status: 'acknowledged', ackObservationSeq: 41,
      ackFieldObservationTimes: { 'sub.filterWidth': 73 },
    });
    expect(acknowledged?.ackFieldObservationTimes).toEqual({ 'sub.filterWidth': 73 });
    expect(acknowledged).not.toHaveProperty('radioState');
    expect(acknowledged).not.toHaveProperty('fieldStatus');
    expect(acknowledged).not.toHaveProperty('main');
    const { getFilterWidthCommandLifecycle: getLiveView } = await import('../panel-adapters');
    runtimeState.state = state({ active: 'SUB', sub: { filterWidth: 3000 }, fieldStatus: { 'sub.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 73 } } });
    emitAcceptedState(runtimeState.state);
    expect(store.getCommandLifecycle('real-ack', 9)?.status).toBe('acknowledged');
    runtimeState.state = state({ active: 'SUB', sub: { filterWidth: 3000 }, fieldStatus: { 'sub.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 74 } } });
    emitAcceptedState(runtimeState.state);
    expect(store.getCommandLifecycle('real-ack', 9)?.status).toBe('confirmed');
    const retained = getLiveView().presentation;
    expect(getLiveView()).toMatchObject({ confirmed: 3000, phase: 'confirmed', busy: false, outcome: { phase: 'confirmed' }, presentation: {
      receiver: 1, sessionEpoch: 9, target: 3000, status: 'confirmed',
    } });
    vi.advanceTimersByTime(5_000);
    expect(store.getCommandLifecycle('real-ack', 9)).toBeUndefined();
    expect(getLiveView()).toMatchObject({ confirmed: 3000, phase: 'idle', busy: false, outcome: null, presentation: null });
    commandRadio.current = null;
    store.beginCommand({ id: 'cold-ack', name: 'set_filter_width', params: { width: 2800, receiver: 0 }, originalEpoch: 9 });
    store.acknowledgeCommand('cold-ack', 9, 10);
    expect(store.getCommandLifecycle('cold-ack', 9)).toMatchObject({
      status: 'acknowledged', ackObservationSeq: undefined, ackFieldObservationTimes: {},
    });
    runtimeState.state = state({ main: { filterWidth: 2400 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    emitAcceptedState(runtimeState.state);
    expect(getLiveView()).toMatchObject({ confirmed: 2400, target: 2800, phase: 'acknowledged', busy: true });
    expect(store.getCommandLifecycle('cold-ack', 9)?.ackFieldObservationTimes).toEqual({ 'main.filterWidth': 5 });
    runtimeState.state = state({ main: { filterWidth: 2800 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 6 } } });
    emitAcceptedState(runtimeState.state);
    expect(getLiveView()).toMatchObject({ confirmed: 2800, target: null, phase: 'confirmed', busy: false, outcome: { phase: 'confirmed' } });
    expect(store.getCommandLifecycle('cold-ack', 9)?.status).toBe('confirmed');
    expect(getLiveView().presentation?.lifecycleId).not.toBe(retained?.lifecycleId);
    store.resetCommandLifecycle();
    expect(getLiveView().presentation).toBeNull();
  });
  it('never resurfaces a superseded receiver lifecycle after its newer outcome retires', async () => {
    vi.useFakeTimers();
    vi.doUnmock('$lib/stores/commands.svelte');
    vi.resetModules();
    runtimeState.state = state({ main: { filterWidth: 2400 }, sub: { filterWidth: 1800 } });
    const store = await import('$lib/stores/commands.svelte');
    const { getFilterWidthCommandLifecycle: getLiveView } = await import('../panel-adapters');

    const old = store.beginCommand({
      id: 'old-main', name: 'set_filter_width', params: { width: 3000 }, originalEpoch: 7, timeoutMs: 10_000,
    });
    expect(store.isCommandLifecycleSuperseded(old)).toBe(false);
    const repeated = store.beginCommand({
      id: 'repeat-main', name: 'set_filter_width', params: { width: 3000 }, originalEpoch: 7, timeoutMs: 10_000,
    });
    expect(store.isCommandLifecycleSuperseded(old)).toBe(true);
    vi.advanceTimersByTime(1_000);
    const newer = store.beginCommand({
      id: 'new-main', name: 'set_filter_width', params: { width: 2800, receiver: 0 }, originalEpoch: 7, timeoutMs: 10_000,
    });
    store.failCommand(newer.id, newer.originalEpoch, 7, 'newer rejected');
    expect(store.isCommandLifecycleSuperseded(repeated)).toBe(true);
    expect(getLiveView().presentation).toMatchObject({ target: 2800, status: 'failed' });

    vi.advanceTimersByTime(3_000);
    store.acknowledgeCommand(old.id, old.originalEpoch, 7);
    vi.advanceTimersByTime(2_000);
    expect(store.getCommandLifecycle(newer.id, newer.originalEpoch)).toBeUndefined();
    expect(getLiveView()).toMatchObject({ confirmed: 2400, target: null, phase: 'idle', busy: false, outcome: null, presentation: null });

    store.failCommand(old.id, old.originalEpoch, 7, 'late failure');
    expect(getLiveView()).toMatchObject({ confirmed: 2400, target: null, phase: 'idle', busy: false, outcome: null });
    vi.advanceTimersByTime(5_000);
    expect(store.getCommandLifecycle(old.id, old.originalEpoch)).toBeUndefined();

    const sub = store.beginCommand({
      id: 'sub', name: 'set_filter_width', params: { width: 2100, receiver: 1 }, originalEpoch: 7,
    });
    runtimeState.state = state({
      active: 'SUB', main: { filterWidth: 2400 }, sub: { filterWidth: 1800 },
      fieldStatus: { 'sub.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 } },
    });
    expect(store.isCommandLifecycleSuperseded(sub)).toBe(false);
    expect(getLiveView()).toMatchObject({ confirmed: 1800, target: 2100, phase: 'pending', busy: true });
    const subPresentation = getLiveView().presentation;

    store.cancelPendingCommands(7);
    expect(store.getCommandLifecycle('sub', 7)?.status).toBe('cancelled');
    controlSession.epoch = 8;
    expect(getLiveView()).toMatchObject({ confirmed: 1800, phase: 'idle', outcome: null, presentation: null });
    const fresh = store.beginCommand({
      id: 'sub', name: 'set_filter_width', params: { width: 2600, receiver: 1 }, originalEpoch: 8,
    });
    expect(store.isCommandLifecycleSuperseded(fresh)).toBe(false);
    expect(getLiveView()).toMatchObject({ confirmed: 1800, target: 2600, phase: 'pending', busy: true });
    expect(getLiveView().presentation?.lifecycleId).not.toBe(subPresentation?.lifecycleId);
    emitAcceptedState(runtimeState.state); store.acknowledgeCommand('sub', 8, 8);
    runtimeState.state = state({ active: 'SUB', sub: { filterWidth: 2600 }, fieldStatus: {
      'sub.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 },
    } }); emitAcceptedState(runtimeState.state);
    expect(store.getCommandLifecycle('sub', 8)?.status).toBe('confirmed');
    expect(getLiveView()).toMatchObject({ confirmed: 2600, phase: 'confirmed', outcome: { phase: 'confirmed' } });
    expect(store.getCommandLifecycle('sub', 7)?.status).toBe('cancelled');
    store.resetCommandLifecycle();
  });
  it('projects a fresh frozen presentation DTO with stable lifecycle and transition identities', () => {
    runtimeState.state = state();
    lifecycle.commands = [command({ id: 'quoted|id', originalEpoch: 7, status: 'pending' })];

    const first = getFilterWidthCommandLifecycle().presentation;
    const second = getFilterWidthCommandLifecycle().presentation;
    expect(first).toEqual({
      lifecycleId: '[7,"quoted|id"]', transitionId: '[7,"quoted|id","pending"]',
      receiver: 0, sessionEpoch: 7, target: 3000, status: 'pending',
    });
    expect(first).not.toBe(second);
    expect(Object.isFrozen(first)).toBe(true);
    expect(first?.lifecycleId).toBe(second?.lifecycleId);
    expect(first?.transitionId).toBe(second?.transitionId);
    expect(first).not.toHaveProperty('params');
    expect(first).not.toHaveProperty('fieldStatus');
  });
  it('changes only the transition identity across lifecycle statuses and retains terminal targets', () => {
    runtimeState.state = state();
    lifecycle.commands = [command({ status: 'pending' })];
    const pending = getFilterWidthCommandLifecycle().presentation!;
    lifecycle.commands[0].status = 'acknowledged';
    const acknowledged = getFilterWidthCommandLifecycle().presentation!;
    expect(acknowledged).toMatchObject({ lifecycleId: pending.lifecycleId, target: 3000, status: 'acknowledged' });
    expect(acknowledged.transitionId).not.toBe(pending.transitionId);

    const transitionIds = [pending.transitionId, acknowledged.transitionId];
    for (const status of ['confirmed', 'failed', 'timed-out', 'cancelled'] as const) {
      lifecycle.commands[0].status = status;
      lifecycle.commands[0].error = status === 'failed' ? 'x'.repeat(300) : undefined;
      expect(getFilterWidthCommandLifecycle().presentation).toMatchObject({
        lifecycleId: pending.lifecycleId, receiver: 0, sessionEpoch: 7, target: 3000, status,
      });
      const transitionId = getFilterWidthCommandLifecycle().presentation?.transitionId;
      expect(transitionId).not.toBe(acknowledged.transitionId);
      transitionIds.push(transitionId!);
    }
    expect(new Set(transitionIds)).toHaveLength(6);
    expect(getFilterWidthCommandLifecycle().presentation).not.toHaveProperty('error');
    lifecycle.commands[0].status = 'failed'; lifecycle.commands[0].error = 'x'.repeat(300);
    expect(getFilterWidthCommandLifecycle().presentation?.error).toHaveLength(256);
  });
  it('keeps real-store terminal snapshots until GC and gives every transition a unique identity', async () => {
    vi.useFakeTimers(); vi.doUnmock('$lib/stores/commands.svelte'); vi.resetModules();
    controlSession.epoch = 12;
    const store = await import('$lib/stores/commands.svelte');
    const { getFilterWidthCommandLifecycle: getLiveView } = await import('../panel-adapters');
    const transitions: string[] = [];
    const start = (id: string, width: number, timeoutMs?: number) => {
      runtimeState.state = state(); emitAcceptedState(runtimeState.state); store.resetCommandLifecycle();
      return store.beginCommand({ id, name: 'set_filter_width', params: { width }, originalEpoch: 12, timeoutMs });
    };
    const assertRetainedThenGc = (target: number, status: string) => {
      const presentation = getLiveView().presentation;
      expect(presentation).toMatchObject({ target, status, sessionEpoch: 12, receiver: 0 });
      transitions.push(presentation!.transitionId); vi.advanceTimersByTime(5_000);
      expect(getLiveView().presentation).toBeNull();
    };

    const confirmed = start('confirmed', 3000, 50);
    transitions.push(getLiveView().presentation!.transitionId);
    store.acknowledgeCommand(confirmed.id, 12, 12); transitions.push(getLiveView().presentation!.transitionId);
    runtimeState.state = state({ main: { filterWidth: 3000 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } }); emitAcceptedState(runtimeState.state);
    assertRetainedThenGc(3000, 'confirmed');
    const failed = start('failed', 2800); store.failCommand(failed.id, 12, 12, 'rejected'); assertRetainedThenGc(2800, 'failed');
    start('cancelled', 2600); store.cancelPendingCommands(12); assertRetainedThenGc(2600, 'cancelled');
    start('timed-out', 2400, 1); vi.advanceTimersByTime(1); assertRetainedThenGc(2400, 'timed-out');
    expect(new Set(transitions)).toHaveLength(6);
    store.resetCommandLifecycle();
  });
  it('isolates receivers and gives reused command ids in a later session a distinct identity', () => {
    runtimeState.state = state({ active: 'MAIN', sub: { filterWidth: 2100 }, fieldStatus: {
      'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
      'sub.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
    } });
    lifecycle.commands = [
      command({ id: 'same', originalEpoch: 7, params: { width: 3000, receiver: 0 } }),
      command({ id: 'same', originalEpoch: 8, createdAt: 2, params: { width: 2100, receiver: 1 } }),
    ];
    controlSession.epoch = 7;
    const main = getFilterWidthCommandLifecycle().presentation!;
    controlSession.epoch = 8;
    runtimeState.state = state({ active: 'SUB', sub: { filterWidth: 2100 }, fieldStatus: {
      'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
      'sub.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
    } });
    const sub = getFilterWidthCommandLifecycle().presentation!;
    expect(main).toMatchObject({ receiver: 0, target: 3000, sessionEpoch: 7 });
    expect(sub).toMatchObject({ receiver: 1, target: 2100, sessionEpoch: 8 });
    expect(sub.lifecycleId).not.toBe(main.lifecycleId);
    runtimeState.state = state({ active: 'MAIN', sub: { filterWidth: 2100 }, fieldStatus: {
      'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
      'sub.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 },
    } });
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ phase: 'idle', presentation: null });
  });
});

const delayCommand = (over: Partial<FakeCommand> = {}): FakeCommand => ({
  id: 'delay-1', name: 'set_break_in_delay', params: Object.freeze({ level: 64 }),
  originalEpoch: controlSession.epoch, eventEpoch: controlSession.epoch,
  createdAt: 1, status: 'pending', ...over,
});
const delayState = (over: Partial<FakeState> = {}): FakeState => ({
  active: 'MAIN', providerGeneration: 3, breakInDelay: 32, main: {}, sub: {},
  fieldStatus: { breakInDelay: {
    observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4,
  } }, ...over,
});

describe('Break-in Delay ControlFeedback projection (MOR-1744)', () => {
  afterEach(() => {
    vi.useRealTimers();
    lifecycle.commands = []; lifecycle.superseded.clear();
    runtimeState.state = null; controlSession.epoch = 7; dispatch.mockClear();
    commandRadio.current = null; commandRadio.listeners.clear();
  });

  it('registers one exact global descriptor without changing Filter Width', () => {
    expect(getStateBackedCommandDescriptor('set_filter_width')).toBe(FILTER_WIDTH_COMMAND_DESCRIPTOR);
    expect(getStateBackedCommandDescriptor('set_break_in_delay')).toBe(BREAK_IN_DELAY_COMMAND_DESCRIPTOR);
    expect([...STATE_BACKED_COMMAND_DESCRIPTORS.entries()]).toEqual([
      ['set_filter_width', FILTER_WIDTH_COMMAND_DESCRIPTOR],
      ['set_break_in_delay', BREAK_IN_DELAY_COMMAND_DESCRIPTOR],
    ]);
    const scope = BREAK_IN_DELAY_COMMAND_DESCRIPTOR.scope(delayCommand());
    expect(scope).toEqual({ control: 'break-in-delay', receiver: 0 });
    expect(BREAK_IN_DELAY_COMMAND_DESCRIPTOR.fieldPath(scope!)).toBe('breakInDelay');
    expect(BREAK_IN_DELAY_COMMAND_DESCRIPTOR.target(delayCommand())).toBe(64);
    expect(BREAK_IN_DELAY_COMMAND_DESCRIPTOR.repeatPolicy).toBe('latest-target-wins');
  });

  it.each([
    ['missing', {}],
    ['non-number', { level: '64' }],
    ['NaN', { level: Number.NaN }],
    ['infinite', { level: Number.POSITIVE_INFINITY }],
    ['fractional', { level: 64.5 }],
    ['unsafe', { level: Number.MAX_VALUE }],
    ['wrong receiver', { level: 64, receiver: 1 }],
    ['extra scope', { level: 64, slot: 'A' }],
  ])('fails closed for %s target/scope input', (_case, params) => {
    const candidate = delayCommand({ params });
    expect(BREAK_IN_DELAY_COMMAND_DESCRIPTOR.target(candidate)).toBeNull();
    if (Reflect.ownKeys(params).length > 1) {
      expect(BREAK_IN_DELAY_COMMAND_DESCRIPTOR.scope(candidate)).toBeNull();
    }
  });

  it('projects submitted and acknowledged facts without mutation or dispatch', () => {
    runtimeState.state = delayState();
    const pending = Object.freeze(delayCommand());
    lifecycle.commands = [pending];
    const before = JSON.stringify(lifecycle.commands);
    expect(getBreakInDelayControlFeedback()).toMatchObject({
      confirmed: 32, target: 64, requestedTarget: 64, phase: 'submitted', busy: true,
      availability: 'available', scope: { control: 'break-in-delay', receiver: 0 },
    });
    lifecycle.commands = [Object.freeze(delayCommand({ status: 'acknowledged' }))];
    expect(getBreakInDelayControlFeedback()).toMatchObject({
      confirmed: 32, target: 64, phase: 'awaiting-confirmation', busy: true,
    });
    expect(JSON.stringify([pending])).toBe(before);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it.each([
    ['missing state', null],
    ['missing value', delayState({ breakInDelay: undefined })],
    ['non-integer value', delayState({ breakInDelay: 32.5 })],
    ['missing field status', delayState({ fieldStatus: {} })],
    ['stale field', delayState({ fieldStatus: { breakInDelay: {
      observed: true, freshness: 'stale', availability: 'available', lastObservedMonotonic: 5,
    } } })],
    ['unavailable field', delayState({ fieldStatus: { breakInDelay: {
      observed: true, freshness: 'fresh', availability: 'unavailable', lastObservedMonotonic: 5,
    } } })],
  ])('returns unavailable for %s canonical truth', (_case, canonical) => {
    runtimeState.state = canonical; lifecycle.commands = [delayCommand()];
    expect(getBreakInDelayControlFeedback()).toMatchObject({
      confirmed: null, target: null, phase: 'unavailable', busy: false,
      availability: 'unavailable',
    });
  });

  it('handles terminal, superseded, reversal, session/provider replacement, and out-of-band truth', () => {
    runtimeState.state = delayState();
    for (const [status, error] of [
      ['failed', 'rejected'], ['timed-out', undefined], ['cancelled', 'session-disconnected'],
    ] as const) {
      lifecycle.commands = [delayCommand({ status, error })];
      expect(getBreakInDelayControlFeedback()).toMatchObject({
        confirmed: 32, target: null, requestedTarget: 64, phase: status, busy: false,
        outcome: { phase: status, ...(error === undefined ? {} : { error }) },
      });
    }
    lifecycle.commands = [delayCommand({ id: 'old', createdAt: 9 })];
    lifecycle.superseded.add('old');
    expect(getBreakInDelayControlFeedback()).toMatchObject({ phase: 'idle', confirmed: 32 });
    lifecycle.commands = [delayCommand({ id: 'reverse', params: { level: 32 } })];
    expect(getBreakInDelayControlFeedback()).toMatchObject({
      phase: 'submitted', confirmed: 32, target: 32, busy: true,
    });
    controlSession.epoch = 8;
    runtimeState.state = delayState({ providerGeneration: 4, breakInDelay: 48 });
    expect(getBreakInDelayControlFeedback()).toMatchObject({
      phase: 'idle', confirmed: 48, target: null, sessionEpoch: 8,
    });
    lifecycle.commands = [];
    runtimeState.state = delayState({ providerGeneration: 4, breakInDelay: 52 });
    expect(getBreakInDelayControlFeedback()).toMatchObject({ phase: 'idle', confirmed: 52 });
  });

  it('confirms only from matching fresh canonical truth newer than the ACK boundary', async () => {
    vi.useFakeTimers(); vi.doUnmock('$lib/stores/commands.svelte'); vi.resetModules();
    const store = await import('$lib/stores/commands.svelte');
    const { getBreakInDelayControlFeedback: getLiveFeedback } = await import('../panel-adapters');
    runtimeState.state = delayState({ breakInDelay: 64 }); emitAcceptedState(runtimeState.state);
    const record = store.beginCommand({
      id: 'real-delay', name: 'set_break_in_delay', params: { level: 64 }, originalEpoch: 7,
    });
    store.acknowledgeCommand(record.id, 7, 7);
    expect(store.getCommandLifecycle(record.id, 7)).toMatchObject({
      status: 'acknowledged', ackFieldObservationTimes: { breakInDelay: 4 },
    });
    expect(getLiveFeedback()).toMatchObject({ phase: 'awaiting-confirmation', target: 64 });
    emitAcceptedState(delayState({ breakInDelay: 64 }));
    expect(store.getCommandLifecycle(record.id, 7)?.status).toBe('acknowledged');
    emitAcceptedState(delayState({ breakInDelay: 48, fieldStatus: { breakInDelay: {
      observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5,
    } } }));
    expect(store.getCommandLifecycle(record.id, 7)?.status).toBe('acknowledged');
    runtimeState.state = delayState({ breakInDelay: 64, fieldStatus: { breakInDelay: {
      observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 6,
    } } }); emitAcceptedState(runtimeState.state);
    expect(store.getCommandLifecycle(record.id, 7)?.status).toBe('confirmed');
    expect(getLiveFeedback()).toMatchObject({
      phase: 'confirmed', confirmed: 64, target: null, outcome: { phase: 'confirmed' },
    });
    store.resetCommandLifecycle();
  });
});
