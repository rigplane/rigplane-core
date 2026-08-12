/**
 * MOR-1519 — the generic armed signal, MODE buttons' first consumer.
 *
 * Owner ruling: any control that does not switch in real time but is
 * instead confirmed by polling needs a generic "in flight" marker
 * (`ArmedFact`/`getModeArmed`, `panel-adapters.ts`). This is a GENERIC READ
 * over the SAME `latestPendingParam` decision table the four MOR-1441 leg-2
 * accessors already use (`mor1441-pending-discrete.isolated.test.ts`) — not
 * a second source of truth — so it inherits that table's honesty rules
 * verbatim: pending survives the transport ack until a confirming
 * post-ack observation (or the shared grace backstop), a re-click re-arms
 * at the freshest target, and a terminal failure clears `armed` immediately
 * (it must never present a failed command as still in flight).
 *
 * `getModeArmed` differs from the leg-2 accessors only in HOW it picks the
 * receiver: no `receiver` param — `ModePanel` renders a single grid for the
 * ACTIVE receiver only, so the accessor reads `runtime.state.active` itself
 * (mirroring `panel-props.ts`'s `activeRx` helper).
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
type FakeState = {
  active: 'MAIN' | 'SUB';
  main: FakeReceiverState;
  sub: FakeReceiverState;
  observationSeq?: number;
};

const state: { commands: FakeCommand[] } = { commands: [] };
const runtimeState: { state: FakeState | null } = { state: null };

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

import { getModeArmed } from '../panel-adapters';

const cmd = (over: Partial<FakeCommand> = {}): FakeCommand => ({
  name: 'set_mode',
  status: 'pending',
  createdAt: 0,
  params: { mode: 'CW', receiver: 0 },
  ...over,
});

describe('getModeArmed (MOR-1519)', () => {
  afterEach(() => { runtimeState.state = null; });

  it('returns unarmed with no state at all', () => {
    state.commands = [cmd()];
    expect(getModeArmed()).toEqual({ armed: false, value: null });
  });

  it('arms on a pending set_mode command targeting the ACTIVE (MAIN) receiver', () => {
    runtimeState.state = { active: 'MAIN', main: { mode: 'USB' }, sub: {} };
    state.commands = [cmd({ params: { mode: 'CW', receiver: 0 } })];
    expect(getModeArmed()).toEqual({ armed: true, value: 'CW' });
  });

  it('reads the SUB receiver when SUB is active, not MAIN', () => {
    runtimeState.state = { active: 'SUB', main: { mode: 'USB' }, sub: { mode: 'USB' } };
    state.commands = [
      cmd({ params: { mode: 'CW', receiver: 0 } }), // MAIN pending — irrelevant, SUB is active
      cmd({ params: { mode: 'LSB', receiver: 1 } }),
    ];
    expect(getModeArmed()).toEqual({ armed: true, value: 'LSB' });
  });

  it('stays unarmed when nothing is pending for the active receiver', () => {
    runtimeState.state = { active: 'MAIN', main: { mode: 'USB' }, sub: {} };
    state.commands = [];
    expect(getModeArmed()).toEqual({ armed: false, value: null });
  });

  // THE kill: a resolved-and-final command misrepresented as still armed —
  // armed must never mask a FAILED command.
  it.each(['failed', 'cancelled', 'timed-out'])(
    'clears armed for a %s command', (status) => {
      runtimeState.state = { active: 'MAIN', main: { mode: 'USB' }, sub: {} };
      state.commands = [cmd({ status, params: { mode: 'CW', receiver: 0 } })];
      expect(getModeArmed()).toEqual({ armed: false, value: null });
    },
  );

  // A transport ack is not a confirming observation — armed survives ack
  // until the radio's OWN observed mode confirms the target.
  it('stays armed for an acknowledged command when the confirmed mode has not caught up', () => {
    runtimeState.state = { active: 'MAIN', main: { mode: 'USB' }, sub: {} };
    state.commands = [cmd({ status: 'acknowledged', params: { mode: 'CW', receiver: 0 } })];
    expect(getModeArmed()).toEqual({ armed: true, value: 'CW' });
  });

  // Confirming observation clears armed — pending is display-only.
  it('clears armed once the confirmed mode matches the target', () => {
    runtimeState.state = { active: 'MAIN', main: { mode: 'CW' }, sub: {} };
    state.commands = [cmd({ status: 'acknowledged', params: { mode: 'CW', receiver: 0 } })];
    expect(getModeArmed()).toEqual({ armed: false, value: null });
  });

  // Re-click while armed re-arms at the new target (freshest-createdAt-wins,
  // same tie-break the shared decision table already provides).
  it('re-arms at the freshest target on a re-click before the first confirms', () => {
    runtimeState.state = { active: 'MAIN', main: { mode: 'USB' }, sub: {} };
    state.commands = [
      cmd({ createdAt: 1, params: { mode: 'CW', receiver: 0 } }),
      cmd({ createdAt: 2, params: { mode: 'LSB', receiver: 0 } }),
    ];
    expect(getModeArmed()).toEqual({ armed: true, value: 'LSB' });
  });

  // Grace backstop (shared 3000ms constant): an acknowledged command whose
  // mode is never actually observed to confirm must retire even with a
  // non-confirming push, or a dropped confirmation would leave armed true
  // forever.
  it('clears armed via the grace backstop once elapsed, even with a non-confirming state', () => {
    vi.useFakeTimers();
    try {
      const now = Date.now();
      runtimeState.state = { active: 'MAIN', main: { mode: 'USB' }, sub: {} };
      state.commands = [cmd({
        status: 'acknowledged', createdAt: now, updatedAt: now, params: { mode: 'CW', receiver: 0 },
      })];
      expect(getModeArmed()).toEqual({ armed: true, value: 'CW' });

      vi.advanceTimersByTime(3_001);
      expect(getModeArmed()).toEqual({ armed: false, value: null });
    } finally {
      vi.useRealTimers();
    }
  });

  // Ignores a pending command for a different intent entirely (e.g. a
  // simultaneous set_filter) — armed must not fire off an unrelated intent.
  it('ignores commands for a different intent name', () => {
    runtimeState.state = { active: 'MAIN', main: { mode: 'USB' }, sub: {} };
    state.commands = [cmd({ name: 'set_filter', params: { filter: 2, receiver: 0 } })];
    expect(getModeArmed()).toEqual({ armed: false, value: null });
  });
});
