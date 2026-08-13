/**
 * MOR-1536 — armed-signal adoption long tail.
 *
 * `getModeArmed` (MOR-1519) was the ARMED-SIGNAL CONTRACT's first consumer.
 * This ticket fans the SAME `armedFact()` primitive out to the other
 * polled-confirmation controls whose command dispatch goes through the
 * shared `latestPendingParam` decision table (MOR-1441/MOR-1488): AGC mode,
 * filter selection, preamp level, attenuator, data mode, and notch
 * (auto/manual, two independent commands — see `getAutoNotchArmed`'s doc
 * comment in `panel-adapters.ts`). Every accessor here is a thin,
 * no-receiver-arg `ArmedFact`-shaped projection over the exact same
 * primitive `getModeArmed` uses — no re-derivation, same honesty rules
 * (pending survives ack until a confirming post-ack observation or the
 * shared grace backstop, a re-click re-arms at the freshest target, a
 * terminal failure clears armed immediately).
 *
 * RIT/XIT/scan/antenna are NOT covered here: their handlers
 * (`makeRitXitHandlers`/`makeScanHandlers`/`makeAntennaHandlers`,
 * `panel-commands.ts`) dispatch `dispatchRadioIntent` WITHOUT a `receiver`
 * param at all, so `latestPendingParam`'s `command.params.receiver !==
 * receiver` guard can never match them — the pending decision table has no
 * path to reach them as they're wired today. See the PR body for the full
 * adopted/skipped table.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

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

import {
  getAgcArmed, getFilterArmed, getPreampArmed, getAttenuatorArmed,
  getDataModeArmed, getAutoNotchArmed, getManualNotchArmed,
} from '../panel-adapters';

afterEach(() => { runtimeState.state = null; });

type Fixture = {
  label: string;
  accessor: () => { armed: boolean; value: unknown };
  intentName: string;
  paramKey: string;
  confirmedField: string;
  target: unknown;
  otherTarget: unknown;
};

const fixtures: Fixture[] = [
  { label: 'getAgcArmed', accessor: getAgcArmed, intentName: 'set_agc', paramKey: 'mode', confirmedField: 'agc', target: 2, otherTarget: 1 },
  { label: 'getFilterArmed', accessor: getFilterArmed, intentName: 'set_filter', paramKey: 'filter', confirmedField: 'filter', target: 2, otherTarget: 1 },
  { label: 'getPreampArmed', accessor: getPreampArmed, intentName: 'set_preamp', paramKey: 'level', confirmedField: 'preamp', target: 1, otherTarget: 0 },
  { label: 'getAttenuatorArmed', accessor: getAttenuatorArmed, intentName: 'set_attenuator', paramKey: 'db', confirmedField: 'att', target: 20, otherTarget: 0 },
  { label: 'getDataModeArmed', accessor: getDataModeArmed, intentName: 'set_data_mode', paramKey: 'mode', confirmedField: 'dataMode', target: 1, otherTarget: 0 },
  { label: 'getAutoNotchArmed', accessor: getAutoNotchArmed, intentName: 'set_auto_notch', paramKey: 'on', confirmedField: 'autoNotch', target: true, otherTarget: false },
  { label: 'getManualNotchArmed', accessor: getManualNotchArmed, intentName: 'set_manual_notch', paramKey: 'on', confirmedField: 'manualNotch', target: true, otherTarget: false },
];

// MOR-1541: pin `ControlButton.svelte`'s import of the shared armed-state
// CSS seat by its literal source string — a regression here (import
// dropped, or the MOR-1541 rename to `control-button-armed.css` reverted
// without updating the import) means the `data-armed` visual channel
// (`control-button-armed.css`) silently stops loading.
describe('ControlButton armed-state CSS import (MOR-1541)', () => {
  it("imports './control-button-armed.css'", () => {
    const source = readFileSync(
      path.resolve(process.cwd(), 'src/lib/Button/ControlButton.svelte'),
      'utf-8',
    );
    expect(source).toContain("import './control-button-armed.css'");
  });
});

for (const fx of fixtures) {
  describe(`${fx.label} (MOR-1536)`, () => {
    const cmd = (over: Partial<FakeCommand> = {}): FakeCommand => ({
      name: fx.intentName,
      status: 'pending',
      createdAt: 0,
      params: { [fx.paramKey]: fx.target, receiver: 0 },
      ...over,
    });

    it('returns unarmed with no state at all', () => {
      state.commands = [cmd()];
      expect(fx.accessor()).toEqual({ armed: false, value: null });
    });

    it(`arms on a pending ${fx.intentName} command targeting the ACTIVE (MAIN) receiver`, () => {
      runtimeState.state = { active: 'MAIN', main: { [fx.confirmedField]: fx.otherTarget }, sub: {} };
      state.commands = [cmd()];
      expect(fx.accessor()).toEqual({ armed: true, value: fx.target });
    });

    it('reads the SUB receiver when SUB is active, not MAIN', () => {
      runtimeState.state = { active: 'SUB', main: {}, sub: { [fx.confirmedField]: fx.otherTarget } };
      state.commands = [
        cmd({ params: { [fx.paramKey]: fx.otherTarget, receiver: 0 } }), // MAIN pending — irrelevant, SUB is active
        cmd({ params: { [fx.paramKey]: fx.target, receiver: 1 } }),
      ];
      expect(fx.accessor()).toEqual({ armed: true, value: fx.target });
    });

    it('stays unarmed when nothing is pending for the active receiver', () => {
      runtimeState.state = { active: 'MAIN', main: {}, sub: {} };
      state.commands = [];
      expect(fx.accessor()).toEqual({ armed: false, value: null });
    });

    // THE kill: a resolved-and-final command must never still read armed.
    it.each(['failed', 'cancelled', 'timed-out'])('clears armed for a %s command', (status) => {
      runtimeState.state = { active: 'MAIN', main: {}, sub: {} };
      state.commands = [cmd({ status })];
      expect(fx.accessor()).toEqual({ armed: false, value: null });
    });

    it('clears armed once the confirmed reading matches the target', () => {
      runtimeState.state = { active: 'MAIN', main: { [fx.confirmedField]: fx.target }, sub: {} };
      state.commands = [cmd({ status: 'acknowledged' })];
      expect(fx.accessor()).toEqual({ armed: false, value: null });
    });

    it('ignores commands for a different intent entirely', () => {
      runtimeState.state = { active: 'MAIN', main: {}, sub: {} };
      state.commands = [cmd({ name: 'set_mode', params: { mode: 'CW', receiver: 0 } })];
      expect(fx.accessor()).toEqual({ armed: false, value: null });
    });
  });
}
