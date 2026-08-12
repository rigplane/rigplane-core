/**
 * MOR-1441 leg 2 — the discrete-control pending accessors.
 *
 * `getPendingFilterSelection`/`getPendingPreampLevel`/`getPendingNbOn`/
 * `getPendingNrOn` are the read side of the leg-2 pending-target affordance:
 * FilterSurface/RfFrontEndSurface/DspSurface show these instead of confirmed
 * radio truth while the matching intent is still in flight. Same authority
 * (the command-bus lifecycle list) and same honesty rule as leg 1's
 * `getPendingFrequencyHz` (`mor1441-pending-frequency.isolated.test.ts`): a
 * command that has already resolved (ack/fail/cancel/timeout) must never be
 * read as still pending.
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
  value: number | boolean;
  otherValue: number | boolean;
};

const CASES: readonly Case[] = [
  {
    label: 'getPendingFilterSelection', accessor: getPendingFilterSelection,
    intentName: 'set_filter', paramKey: 'filter', value: 2, otherValue: 3,
  },
  {
    label: 'getPendingPreampLevel', accessor: getPendingPreampLevel,
    intentName: 'set_preamp', paramKey: 'level', value: 1, otherValue: 2,
  },
  {
    label: 'getPendingNbOn', accessor: getPendingNbOn,
    intentName: 'set_nb', paramKey: 'on', value: true, otherValue: false,
  },
  {
    label: 'getPendingNrOn', accessor: getPendingNrOn,
    intentName: 'set_nr', paramKey: 'on', value: true, otherValue: false,
  },
];

describe.each(CASES)('$label (MOR-1441 leg 2)', ({ accessor, intentName, paramKey, value, otherValue }) => {
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

  // THE kill: a resolved command misrepresented as still pending — the exact
  // fabrication MOR-1441 forbids.
  it.each(['acknowledged', 'failed', 'cancelled', 'timed-out'])(
    'ignores a %s command', (status) => {
      state.commands = [cmd({ name: intentName, status, params: { [paramKey]: value, receiver: 0 } })];
      expect(accessor(0)).toBeNull();
    },
  );

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
