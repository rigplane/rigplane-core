/**
 * MOR-1441 leg 2 — the discrete-control pending accessors.
 *
 * `getPendingFilterSelection`/`getPendingPreampLevel`/`getPendingNbOn`/
 * `getPendingNrOn` are the read side of the leg-2 pending-target affordance:
 * FilterSurface/RfFrontEndSurface/DspSurface show these instead of confirmed
 * radio truth while the matching intent is still in flight. Same authority
 * (the command-bus lifecycle list) as leg 1's `getPendingFrequencyHz`
 * (`mor1441-pending-frequency.isolated.test.ts`), but a DIFFERENT release
 * rule (MOR-1488): a terminal-without-further-meaning command (failed,
 * cancelled, timed-out) must never be read as still pending, but a merely
 * `'acknowledged'` one (the transport ack) stays pending until the radio's
 * OWN observed state confirms the target — an ack proves only that the
 * radio received the command, typically milliseconds before the next state
 * poll actually echoes it back, and presenting that ack as confirmation is
 * the exact live-bench fabrication MOR-1488 fixes.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

type FakeCommand = {
  name: string;
  status: string;
  createdAt: number;
  params: Record<string, unknown>;
};
type FakeReceiverState = Record<string, unknown>;

const state: { commands: FakeCommand[] } = { commands: [] };
const runtimeState: { state: { main: FakeReceiverState; sub: FakeReceiverState } | null } = { state: null };

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

import {
  getPendingFilterSelection, getPendingNbOn, getPendingNrOn, getPendingPreampLevel,
} from '../panel-adapters';

const cmd = (over: Partial<FakeCommand> = {}): FakeCommand => ({
  name: 'set_filter', status: 'pending', createdAt: 0, params: { filter: 2, receiver: 0 }, ...over,
});

type Case = {
  label: string;
  accessor: (receiver: 0 | 1) => number | boolean | null;
  intentName: string;
  paramKey: string;
  /** The `ServerState.main`/`.sub` field this accessor's target is confirmed
   *  against — differs from `paramKey` for preamp (`level` on the wire,
   *  `preamp` on the receiver state), matching `panel-adapters.ts`'s own
   *  `confirmedField` argument to `latestPendingParam`. */
  confirmedField: string;
  value: number | boolean;
  otherValue: number | boolean;
};

const CASES: readonly Case[] = [
  {
    label: 'getPendingFilterSelection', accessor: getPendingFilterSelection,
    intentName: 'set_filter', paramKey: 'filter', confirmedField: 'filter', value: 2, otherValue: 3,
  },
  {
    label: 'getPendingPreampLevel', accessor: getPendingPreampLevel,
    intentName: 'set_preamp', paramKey: 'level', confirmedField: 'preamp', value: 1, otherValue: 2,
  },
  {
    label: 'getPendingNbOn', accessor: getPendingNbOn,
    intentName: 'set_nb', paramKey: 'on', confirmedField: 'nb', value: true, otherValue: false,
  },
  {
    label: 'getPendingNrOn', accessor: getPendingNrOn,
    intentName: 'set_nr', paramKey: 'on', confirmedField: 'nr', value: true, otherValue: false,
  },
];

describe.each(CASES)('$label (MOR-1441 leg 2, MOR-1488)', (
  { accessor, intentName, paramKey, confirmedField, value, otherValue },
) => {
  afterEach(() => { runtimeState.state = null; });

  it('returns the pending target for the given receiver', () => {
    state.commands = [cmd({ name: intentName, params: { [paramKey]: value, receiver: 0 } })];
    expect(accessor(0)).toBe(value);
  });

  it('returns null when no matching command is pending for that receiver', () => {
    state.commands = [cmd({ name: intentName, params: { [paramKey]: value, receiver: 1 } })];
    expect(accessor(0)).toBeNull();
  });

  it('returns null when no commands are in flight at all', () => {
    state.commands = [];
    expect(accessor(0)).toBeNull();
  });

  // THE kill: a resolved-and-final command misrepresented as still pending —
  // the exact fabrication MOR-1441 forbids. 'acknowledged' is deliberately
  // NOT in this list (MOR-1488) — see the two tests below.
  it.each(['failed', 'cancelled', 'timed-out'])(
    'ignores a %s command', (status) => {
      state.commands = [cmd({ name: intentName, status, params: { [paramKey]: value, receiver: 0 } })];
      expect(accessor(0)).toBeNull();
    },
  );

  // MOR-1488 (live-bench finding): a transport ack is not a confirming
  // observation. With no observed radio state yet (or one that has not
  // caught up), an acknowledged command must still read as pending — this
  // is the assertion that failed against the pre-fix code (it returned
  // `null`, collapsing the pending window to something imperceptible live).
  it('still returns the target for an acknowledged command when the confirmed state has not caught up', () => {
    runtimeState.state = { main: { [confirmedField]: otherValue }, sub: {} };
    state.commands = [cmd({ name: intentName, status: 'acknowledged', params: { [paramKey]: value, receiver: 0 } })];
    expect(accessor(0)).toBe(value);
  });

  // The other half of the same rule: once the radio's OWN observed state
  // confirms the target, the acknowledged command must stop reading as
  // pending — the leg-1 "pending is display-only, confirmed reading stays
  // authoritative" doctrine, now applied at the confirming-observation seam.
  it('ignores an acknowledged command once the confirmed state matches the target', () => {
    runtimeState.state = { main: { [confirmedField]: value }, sub: {} };
    state.commands = [cmd({ name: intentName, status: 'acknowledged', params: { [paramKey]: value, receiver: 0 } })];
    expect(accessor(0)).toBeNull();
  });

  // SUB receiver parity: the confirming read must follow the SAME receiver
  // split as the command match, not always read MAIN.
  it('confirms against the SUB receiver state for receiver 1', () => {
    runtimeState.state = { main: {}, sub: { [confirmedField]: value } };
    state.commands = [cmd({ name: intentName, status: 'acknowledged', params: { [paramKey]: value, receiver: 1 } })];
    expect(accessor(1)).toBeNull();
  });

  // Kills: reading the OLDEST pending command instead of the freshest.
  it('picks the freshest pending command when several are in flight', () => {
    state.commands = [
      cmd({ name: intentName, createdAt: 1, params: { [paramKey]: value, receiver: 0 } }),
      cmd({ name: intentName, createdAt: 2, params: { [paramKey]: otherValue, receiver: 0 } }),
    ];
    expect(accessor(0)).toBe(otherValue);
  });

  // Kills: matching on receiver alone — a pending command for a DIFFERENT
  // intent must never be mistaken for this one's target.
  it('ignores commands for a different intent name', () => {
    state.commands = [cmd({ name: 'set_freq', params: { freq: 14100000, receiver: 0 } })];
    expect(accessor(0)).toBeNull();
    // A foreign command that SHARES this accessor's paramKey — `set_rf_gain`/
    // `set_nb_level`/… all carry `{ level, receiver }`, and seven intents carry
    // `{ on, receiver }`. Only the intent-name check separates them.
    state.commands = [cmd({ name: 'set_rf_gain', params: { [paramKey]: otherValue, receiver: 0 } })];
    expect(accessor(0)).toBeNull();
  });

  // B3 (leg-1 review): on a same-millisecond `createdAt` tie, the LATER
  // array-order entry is the actually-freshest one.
  it('on a same-millisecond createdAt tie, prefers the LATER dispatched command (array order)', () => {
    state.commands = [
      cmd({ name: intentName, createdAt: 5, params: { [paramKey]: value, receiver: 0 } }),
      cmd({ name: intentName, createdAt: 5, params: { [paramKey]: otherValue, receiver: 0 } }),
    ];
    expect(accessor(0)).toBe(otherValue);
  });
});
