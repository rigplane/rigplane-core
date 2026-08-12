/**
 * MOR-1441 — the pending-frequency accessor.
 *
 * `getPendingFrequencyHz` is the read side of the pending-target
 * affordance: `VfoSurface`'s digit control shows this instead of confirmed
 * radio truth while a `set_freq` intent for the receiver is still in
 * flight, so the operator sees where a hot tuning burst (MOR-1425) is
 * heading rather than a stale confirmed value. It must never surface a
 * command that has already resolved (ack/fail/cancel/timeout) as though it
 * were still pending — that would present RESOLVED state as PENDING, the
 * exact honesty violation MOR-1441 exists to prevent.
 */
import { describe, expect, it, vi } from 'vitest';

type FakeCommand = {
  name: string;
  status: string;
  createdAt: number;
  params: Record<string, unknown>;
};

const state: { commands: FakeCommand[] } = { commands: [] };

vi.mock('$lib/stores/commands.svelte', () => ({
  getCommandLifecycles: () => state.commands,
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: { get state() { return null; }, get caps() { return null; } },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => null,
}));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({
  toRadioViewModel: () => null,
}));

import { getPendingFrequencyHz } from '../panel-adapters';

const cmd = (over: Partial<FakeCommand> = {}): FakeCommand => ({
  name: 'set_freq',
  status: 'pending',
  createdAt: 0,
  params: { freq: 14100000, receiver: 0 },
  ...over,
});

describe('panel-adapters pending-frequency accessor (MOR-1441)', () => {
  it('returns the pending set_freq target for the given receiver', () => {
    state.commands = [cmd({ params: { freq: 14100000, receiver: 0 } })];
    expect(getPendingFrequencyHz(0)).toBe(14100000);
  });

  it('returns null when no set_freq command is pending for that receiver', () => {
    state.commands = [cmd({ params: { freq: 14100000, receiver: 1 } })];
    expect(getPendingFrequencyHz(0)).toBeNull();
  });

  it('returns null when no commands are in flight at all', () => {
    state.commands = [];
    expect(getPendingFrequencyHz(0)).toBeNull();
  });

  // THE kill: an acknowledged/failed/cancelled/timed-out command has
  // RESOLVED — reading it as pending would misrepresent resolved state as
  // still in flight, exactly the fabrication MOR-1441 forbids.
  it.each(['acknowledged', 'failed', 'cancelled', 'timed-out'])(
    'ignores a %s set_freq command', (status) => {
      state.commands = [cmd({ status, params: { freq: 14100000, receiver: 0 } })];
      expect(getPendingFrequencyHz(0)).toBeNull();
    },
  );

  // Kills: reading the OLDEST pending command instead of the freshest — the
  // operator must see where the burst is heading NOW, not its first step.
  it('picks the freshest pending command when several are in flight', () => {
    state.commands = [
      cmd({ createdAt: 1, params: { freq: 14100000, receiver: 0 } }),
      cmd({ createdAt: 2, params: { freq: 14110000, receiver: 0 } }),
    ];
    expect(getPendingFrequencyHz(0)).toBe(14110000);
  });

  // Kills: matching on intent name alone — a pending `set_mode` must never
  // be mistaken for a pending frequency target.
  it('ignores commands for a different intent name', () => {
    state.commands = [cmd({ name: 'set_mode', params: { mode: 'USB', receiver: 0 } })];
    expect(getPendingFrequencyHz(0)).toBeNull();
  });
});
