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
  main: Record<string, unknown>;
  sub: Record<string, unknown>;
  observationSeq?: number;
  fieldStatus?: Record<string, { observed?: boolean; freshness?: string; availability?: string; lastObservedMonotonic?: number | null }>;
};
const lifecycle = { commands: [] as FakeCommand[], confirms: [] as [string, number, number][] };
const runtimeState: { state: FakeState | null } = { state: null };
const commandRadio = vi.hoisted(() => ({
  current: null as Record<string, unknown> | null,
  listeners: new Set<(value: Record<string, unknown> | null) => void>(),
}));
vi.mock('$lib/stores/commands.svelte', () => ({
  getCommandLifecycles: () => lifecycle.commands,
  confirmCommand: (id: string, epoch: number, eventEpoch: number) => lifecycle.confirms.push([id, epoch, eventEpoch]),
  isCommandLifecycleSuperseded: () => false,
}));
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

import { getFilterWidthCommandLifecycle } from '../panel-adapters';
function emitAcceptedState(value: FakeState | null): void {
  commandRadio.current = value;
  for (const handler of commandRadio.listeners) handler(value);
}
const command = (over: Partial<FakeCommand> = {}): FakeCommand => ({
  id: 'width-1', name: 'set_filter_width', params: { width: 3000 },
  originalEpoch: 7, eventEpoch: 7, createdAt: 1, status: 'pending', ...over,
});
const state = (over: Partial<FakeState> = {}): FakeState => ({
  active: 'MAIN', main: { filterWidth: 2400 }, sub: {}, observationSeq: 4,
  fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 } }, ...over,
});
describe('Filter Width command lifecycle projection (MOR-1664)', () => {
  afterEach(() => {
    lifecycle.commands = [];
    lifecycle.confirms = [];
    runtimeState.state = null;
    commandRadio.current = null;
    commandRadio.listeners.clear();
  });
  it('stays unavailable rather than fabricating a pending or confirmed value without observed width', () => {
    runtimeState.state = state({ main: {} });
    lifecycle.commands = [command()];
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: null, target: null, phase: 'unavailable', busy: false, outcome: null,
    });
  });
  it('keeps canonical observed width separate from a submitted target', () => {
    runtimeState.state = state();
    lifecycle.commands = [command()];
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: 2400, target: 3000, phase: 'pending', busy: true, outcome: null,
    });
  });
  it('keeps a fresh matching observation acknowledged when a caller only reads the pure accessor', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, observationSeq: 5, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackObservationSeq: 4, ackFieldObservationTimes: { 'main.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: 3000, target: 3000, phase: 'acknowledged', busy: true, outcome: null,
    });
    expect(lifecycle.confirms).toEqual([]);
  });
  it('does not let an unrelated global observation advance confirm a cached matching width', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, observationSeq: 99,
      fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 4 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackObservationSeq: 4, ackFieldObservationTimes: { 'main.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ confirmed: 3000, target: 3000, phase: 'acknowledged', busy: true });
    expect(lifecycle.confirms).toEqual([]);
  });
  it('does not confirm a matching width from a reverse or stale field marker', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 3 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackFieldObservationTimes: { 'main.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ target: 3000, phase: 'acknowledged', busy: true });
    expect(lifecycle.confirms).toEqual([]);
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
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: null, target: null, phase: 'unavailable', busy: false, outcome: null,
    });
    expect(lifecycle.confirms).toEqual([]);
  });
  it('does not let a legacy record without an ACK field boundary confirm', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    lifecycle.commands = [command({ status: 'acknowledged' })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ target: 3000, phase: 'acknowledged', busy: true });
    expect(lifecycle.confirms).toEqual([]);
  });
  it('does not establish a cold boundary from an accessor read', () => {
    runtimeState.state = state({ main: { filterWidth: 2400 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackFieldObservationTimes: {} })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ confirmed: 2400, target: 3000, phase: 'acknowledged', busy: true });
    expect(lifecycle.commands[0].ackFieldObservationTimes).toEqual({});
    runtimeState.state = state({ main: { filterWidth: 3000 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 6 } } });
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ confirmed: 3000, target: 3000, phase: 'acknowledged', busy: true });
    expect(lifecycle.confirms).toEqual([]);
  });

  it('does not treat a missing exact field marker in a non-empty ACK map as an empty boundary', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackFieldObservationTimes: { 'sub.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toMatchObject({ confirmed: 3000, target: 3000, phase: 'acknowledged', busy: true });
    expect(lifecycle.confirms).toEqual([]);
  });

  it('keeps a fresh non-matching observation canonical while the acknowledged target remains pending', () => {
    runtimeState.state = state({ main: { filterWidth: 2400 }, observationSeq: 5, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    lifecycle.commands = [command({ status: 'acknowledged', ackObservationSeq: 4, ackFieldObservationTimes: { 'main.filterWidth': 4 } })];
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: 2400, target: 3000, phase: 'acknowledged', busy: true, outcome: null,
    });
    expect(lifecycle.confirms).toEqual([]);
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
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: 2800, target: 2800, phase: 'acknowledged', busy: true, outcome: null,
    });
    expect(lifecycle.confirms).toEqual([]);
  });

  it('projects only the newest same-receiver target, so older late events cannot overwrite it', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, observationSeq: 5 });
    lifecycle.commands = [
      command({ id: 'older', createdAt: 1, status: 'acknowledged', ackObservationSeq: 4 }),
      command({ id: 'newer', createdAt: 2, params: { width: 2800, receiver: 0 }, status: 'pending' }),
    ];
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: 3000, target: 2800, phase: 'pending', busy: true, outcome: null,
    });
    expect(lifecycle.confirms).toEqual([]);
  });

  it('lets a newer terminal record suppress an older pending target', () => {
    runtimeState.state = state();
    lifecycle.commands = [
      command({ id: 'older', createdAt: 1 }),
      command({ id: 'newer', createdAt: 2, params: { width: 2800, receiver: 0 }, status: 'failed', error: 'rejected' }),
    ];
    expect(getFilterWidthCommandLifecycle()).toEqual({
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
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: 2400, target: null, phase: 'idle', busy: false, outcome: { phase: status, error },
    });
  });

  it('captures only finite public-field ACK markers through the real command store seam', async () => {
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
    runtimeState.state = state({ active: 'SUB', sub: { filterWidth: 3000 }, fieldStatus: { 'sub.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 73 } } });
    emitAcceptedState(runtimeState.state);
    expect(store.getCommandLifecycle('real-ack', 9)?.status).toBe('acknowledged');
    runtimeState.state = state({ active: 'SUB', sub: { filterWidth: 3000 }, fieldStatus: { 'sub.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 74 } } });
    emitAcceptedState(runtimeState.state);
    expect(store.getCommandLifecycle('real-ack', 9)?.status).toBe('confirmed');

    commandRadio.current = null;
    store.beginCommand({ id: 'cold-ack', name: 'set_filter_width', params: { width: 2800, receiver: 0 }, originalEpoch: 9 });
    store.acknowledgeCommand('cold-ack', 9, 10);
    expect(store.getCommandLifecycle('cold-ack', 9)).toMatchObject({
      status: 'acknowledged', ackObservationSeq: undefined, ackFieldObservationTimes: {},
    });
    const { getFilterWidthCommandLifecycle: getLiveView } = await import('../panel-adapters');
    runtimeState.state = state({ main: { filterWidth: 2400 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5 } } });
    emitAcceptedState(runtimeState.state);
    expect(getLiveView()).toMatchObject({ confirmed: 2400, target: 2800, phase: 'acknowledged', busy: true });
    expect(store.getCommandLifecycle('cold-ack', 9)?.ackFieldObservationTimes).toEqual({ 'main.filterWidth': 5 });
    runtimeState.state = state({ main: { filterWidth: 2800 }, fieldStatus: { 'main.filterWidth': { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 6 } } });
    emitAcceptedState(runtimeState.state);
    expect(getLiveView()).toMatchObject({ confirmed: 2800, target: null, phase: 'idle', busy: false });
    expect(store.getCommandLifecycle('cold-ack', 9)?.status).toBe('confirmed');
    store.resetCommandLifecycle();
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
    vi.advanceTimersByTime(1_000);
    const newer = store.beginCommand({
      id: 'new-main', name: 'set_filter_width', params: { width: 2800, receiver: 0 }, originalEpoch: 7, timeoutMs: 10_000,
    });
    store.failCommand(newer.id, newer.originalEpoch, 7, 'newer rejected');
    expect(store.isCommandLifecycleSuperseded(old)).toBe(true);

    vi.advanceTimersByTime(3_000);
    store.acknowledgeCommand(old.id, old.originalEpoch, 7);
    vi.advanceTimersByTime(2_000);
    expect(store.getCommandLifecycle(newer.id, newer.originalEpoch)).toBeUndefined();
    expect(getLiveView()).toMatchObject({ confirmed: 2400, target: null, phase: 'idle', busy: false, outcome: null });

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

    store.resetCommandLifecycle();
    const fresh = store.beginCommand({
      id: 'fresh-main', name: 'set_filter_width', params: { width: 2600, receiver: 0 }, originalEpoch: 8,
    });
    expect(store.isCommandLifecycleSuperseded(fresh)).toBe(false);
    runtimeState.state = state({ main: { filterWidth: 2400 }, sub: { filterWidth: 1800 } });
    expect(getLiveView()).toMatchObject({ confirmed: 2400, target: 2600, phase: 'pending', busy: true });
    store.resetCommandLifecycle();
  });
});
