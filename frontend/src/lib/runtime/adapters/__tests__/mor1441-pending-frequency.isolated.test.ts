/**
 * MOR-1441 — the pending-frequency accessor.
 *
 * `getPendingFrequencyHz` is the read side of the pending-target
 * affordance: `VfoSurface`'s digit control shows this instead of confirmed
 * radio truth while a `set_freq` intent for the receiver is still in
 * flight, so the operator sees where a hot tuning burst (MOR-1425) is
 * heading rather than a stale confirmed value. It must never surface a
 * command that has already resolved (failed/cancelled/timed-out) as though
 * it were still pending — that would present RESOLVED state as PENDING,
 * the exact honesty violation MOR-1441 exists to prevent.
 *
 * MOR-1478 (root cause shared with leg 2's MOR-1488): a transport ack is
 * NOT a confirming observation. During a long web-driven tuning spin the
 * MOR-1425 accumulator emits a steady stream of `set_freq` commands; each
 * WS ack lands within milliseconds, well before the next confirming poll
 * echoes `main.freqHz`/`sub.freqHz` back. Releasing pending on ack alone
 * (the pre-fix behavior) let the LAST ack in a spin briefly present the
 * stale pre-spin confirmed value as though it were current — a ~500ms
 * flash of wrong data — before the next poll actually caught up. This
 * accessor now goes through the SAME `latestPendingParam` decision table
 * leg 2's four discrete accessors use
 * (`mor1441-pending-discrete.isolated.test.ts`): an acknowledged command
 * stays pending until the radio's OWN observed state confirms the target,
 * or the 2s grace backstop elapses.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

type FakeCommand = {
  name: string;
  status: string;
  createdAt: number;
  updatedAt?: number;
  ackObservationSeq?: number;
  params: Record<string, unknown>;
};
type FakeReceiverState = Record<string, unknown>;

const state: { commands: FakeCommand[] } = { commands: [] };
const runtimeState: { state: { main: FakeReceiverState; sub: FakeReceiverState; observationSeq?: number } | null } = {
  state: null,
};

vi.mock('$lib/stores/commands.svelte', () => ({
  getCommandLifecycles: () => state.commands,
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: { get state() { return runtimeState.state; }, get caps() { return null; } },
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

describe('panel-adapters pending-frequency accessor (MOR-1441, MOR-1478)', () => {
  afterEach(() => { runtimeState.state = null; });

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

  // THE kill: a resolved-and-final command misrepresented as still pending —
  // the exact fabrication MOR-1441 forbids. 'acknowledged' is deliberately
  // NOT in this list (MOR-1478/MOR-1488) — see the tests below.
  it.each(['failed', 'cancelled', 'timed-out'])(
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

  // B3 (review): a `>` tie-break freezes on the EARLIER of two same-`createdAt`
  // commands (millisecond-resolution timestamps, and a double-dispatch within
  // one ms is real — the MOR-1425 accumulator can flush and a caller step in
  // the same tick). `getCommandLifecycles()` returns commands in dispatch
  // (array) order, so on a tie the LATER array entry is the actually-freshest
  // one. Kills: reverting `>=` back to `>`.
  it('on a same-millisecond createdAt tie, prefers the LATER dispatched command (array order)', () => {
    state.commands = [
      cmd({ createdAt: 5, params: { freq: 14100000, receiver: 0 } }),
      cmd({ createdAt: 5, params: { freq: 14105000, receiver: 0 } }),
    ];
    expect(getPendingFrequencyHz(0)).toBe(14105000);
  });

  // MOR-1478 (live-bench finding): a transport ack is not a confirming
  // observation. With no observed radio state yet (or one that has not
  // caught up), an acknowledged `set_freq` must still read as pending —
  // this is the assertion that failed against the pre-fix code (it
  // returned `null`, letting the stale confirmed value flash through for
  // the ~500ms until the next poll actually caught up).
  it('still returns the target for an acknowledged command when the confirmed state has not caught up', () => {
    runtimeState.state = { main: { freqHz: 14100000 }, sub: {} };
    state.commands = [cmd({ status: 'acknowledged', params: { freq: 14150000, receiver: 0 } })];
    expect(getPendingFrequencyHz(0)).toBe(14150000);
  });

  // The other half of the same rule: once the radio's OWN observed state
  // confirms the target, the acknowledged command must stop reading as
  // pending — pending is display-only, the confirmed reading stays
  // authoritative once it actually catches up.
  it('ignores an acknowledged command once the confirmed state matches the target', () => {
    runtimeState.state = { main: { freqHz: 14150000 }, sub: {} };
    state.commands = [cmd({ status: 'acknowledged', params: { freq: 14150000, receiver: 0 } })];
    expect(getPendingFrequencyHz(0)).toBeNull();
  });

  // SUB receiver parity: the confirming read must follow the SAME receiver
  // split as the command match, not always read MAIN.
  it('confirms against the SUB receiver state for receiver 1', () => {
    runtimeState.state = { main: {}, sub: { freqHz: 14150000 } };
    state.commands = [cmd({ status: 'acknowledged', params: { freq: 14150000, receiver: 1 } })];
    expect(getPendingFrequencyHz(1)).toBeNull();
  });

  // The multi-command spin case (MOR-1478): a long tuning burst emits MANY
  // `set_freq` commands (MOR-1425 accumulator, paced just above the
  // server's 50ms coalescing window, MOR-1427). Only the LATEST command's
  // own confirmation state may drive the display — an EARLIER command's
  // target happening to already match confirmed radio truth must not leak
  // through and prematurely clear the marker for a later, still-unconfirmed
  // target.
  it('tracks only the latest command\'s confirmation state during a multi-command spin', () => {
    // Confirms the FIRST (earlier, already-superseded) target only.
    runtimeState.state = { main: { freqHz: 14110000 }, sub: {} };
    state.commands = [
      cmd({ createdAt: 1, status: 'acknowledged', params: { freq: 14110000, receiver: 0 } }),
      cmd({ createdAt: 2, status: 'acknowledged', params: { freq: 14120000, receiver: 0 } }),
    ];
    expect(getPendingFrequencyHz(0)).toBe(14120000);
  });

  // Grace backstop (MOR-1478, shared 3000ms constant with leg 2): once
  // elapsed, an acknowledged command whose field is never actually
  // observed to confirm must retire even with a non-confirming push —
  // otherwise a dropped confirming observation (MOR-1445 post-ack failure,
  // MOR-1427 coalescing, link death) would leave the marker pending
  // forever.
  it('retires the marker via the grace backstop once it has elapsed, even with a non-confirming state', () => {
    vi.useFakeTimers();
    try {
      const now = Date.now();
      runtimeState.state = { main: { freqHz: 14100000 }, sub: {} };
      state.commands = [cmd({
        status: 'acknowledged', createdAt: now, updatedAt: now, params: { freq: 14150000, receiver: 0 },
      })];
      expect(getPendingFrequencyHz(0)).toBe(14150000);

      vi.advanceTimersByTime(3_001);
      // Still non-confirming (`main.freqHz` unchanged, target never reached).
      expect(getPendingFrequencyHz(0)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
