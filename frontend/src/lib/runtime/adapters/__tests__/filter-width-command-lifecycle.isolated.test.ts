/**
 * MOR-1664 — Filter Width lifecycle projection.
 *
 * The adapter is the only place this child may join a command record with
 * radio-observed truth.  It must keep the observed width canonical and make
 * an explicitly unconfirmed target visible only while the newest matching
 * record is live.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

type FakeCommand = {
  id: string;
  name: string;
  params: Record<string, unknown>;
  originalEpoch: number;
  eventEpoch?: number;
  createdAt: number;
  status: 'pending' | 'acknowledged' | 'confirmed' | 'failed' | 'cancelled' | 'timed-out';
  error?: string;
  ackObservationSeq?: number;
};
type FakeState = {
  active: 'MAIN' | 'SUB';
  main: Record<string, unknown>;
  sub: Record<string, unknown>;
  observationSeq?: number;
};

const lifecycle = { commands: [] as FakeCommand[], confirms: [] as [string, number, number][] };
const runtimeState: { state: FakeState | null } = { state: null };

vi.mock('$lib/stores/commands.svelte', () => ({
  getCommandLifecycles: () => lifecycle.commands,
  confirmCommand: (id: string, epoch: number, eventEpoch: number) => lifecycle.confirms.push([id, epoch, eventEpoch]),
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: { get state() { return runtimeState.state; }, get caps() { return null; } },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({ getAppTxController: () => null }));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({ toRadioViewModel: () => null }));

import { getFilterWidthCommandLifecycle } from '../panel-adapters';

const command = (over: Partial<FakeCommand> = {}): FakeCommand => ({
  id: 'width-1', name: 'set_filter_width', params: { width: 3000, receiver: 0 },
  originalEpoch: 7, eventEpoch: 7, createdAt: 1, status: 'pending', ...over,
});
const state = (over: Partial<FakeState> = {}): FakeState => ({
  active: 'MAIN', main: { filterWidth: 2400 }, sub: {}, observationSeq: 4, ...over,
});

describe('Filter Width command lifecycle projection (MOR-1664)', () => {
  afterEach(() => {
    lifecycle.commands = [];
    lifecycle.confirms = [];
    runtimeState.state = null;
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

  it('does not let a matching snapshot already present at ACK confirm the command', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, observationSeq: 4 });
    lifecycle.commands = [command({ status: 'acknowledged', ackObservationSeq: 4 })];
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: 3000, target: 3000, phase: 'acknowledged', busy: true, outcome: null,
    });
    expect(lifecycle.confirms).toEqual([]);
  });

  it('confirms the newest acknowledged target only after a fresh matching observation', () => {
    runtimeState.state = state({ main: { filterWidth: 3000 }, observationSeq: 5 });
    lifecycle.commands = [command({ status: 'acknowledged', ackObservationSeq: 4 })];
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: 3000, target: null, phase: 'idle', busy: false, outcome: null,
    });
    expect(lifecycle.confirms).toEqual([['width-1', 7, 7]]);
  });

  it('keeps a fresh non-matching observation canonical while the acknowledged target remains pending', () => {
    runtimeState.state = state({ main: { filterWidth: 2400 }, observationSeq: 5 });
    lifecycle.commands = [command({ status: 'acknowledged', ackObservationSeq: 4 })];
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: 2400, target: 3000, phase: 'acknowledged', busy: true, outcome: null,
    });
    expect(lifecycle.confirms).toEqual([]);
  });

  it('correlates confirmation with the active SUB receiver rather than MAIN', () => {
    runtimeState.state = state({
      active: 'SUB', main: { filterWidth: 3000 }, sub: { filterWidth: 2800 }, observationSeq: 5,
    });
    lifecycle.commands = [command({
      id: 'sub-width', params: { width: 2800, receiver: 1 }, status: 'acknowledged', ackObservationSeq: 4,
    })];
    expect(getFilterWidthCommandLifecycle()).toEqual({
      confirmed: 2800, target: null, phase: 'idle', busy: false, outcome: null,
    });
    expect(lifecycle.confirms).toEqual([['sub-width', 7, 7]]);
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
});
