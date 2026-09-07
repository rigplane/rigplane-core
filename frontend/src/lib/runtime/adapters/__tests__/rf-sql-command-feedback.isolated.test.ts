import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { CommandLifecycle } from '$lib/stores/commands.svelte';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  commands: [] as CommandLifecycle[],
  stateReads: 0,
  capsReads: 0,
  commandReads: 0,
  sessionReads: 0,
  session: { state: 'connected' as 'connected' | 'disconnected', epoch: 7 },
  active: 'MAIN' as 'MAIN' | 'SUB' | null,
  operational: true,
  project: vi.fn(),
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { h.stateReads += 1; return h.state; },
    get caps() { h.capsReads += 1; return h.caps; },
    get controlSession() { h.sessionReads += 1; return h.session; },
  },
}));
vi.mock('$lib/stores/commands.svelte', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/stores/commands.svelte')>(),
  getCommandLifecycles: () => { h.commandReads += 1; return h.commands; },
  isCommandLifecycleSuperseded: () => false,
}));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({
  toRadioViewModel: (state: ServerState | null, caps: Capabilities | null) => {
    h.project(state, caps);
    return h.active === null ? { activeReceiver: { status: 'unknown' }, receiverIndicators: [] } : {
      activeReceiver: { status: 'known', receiver: h.active },
      receiverIndicators: [{
        receiver: h.active,
        availability: { structural: true, operational: h.operational },
      }],
    };
  },
}));
vi.mock('$lib/runtime/commands/radio-intents', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/commands/radio-intents')>(),
  currentControlSessionEpoch: () => 99,
}));

import { getRfSqlControlFeedback } from '../panel-adapters';

const fresh = (marker = 5) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
const state = (over: Record<string, unknown> = {}): ServerState => ({
  stateContractVersion: 1,
  providerGeneration: 3,
  active: 'MAIN',
  main: { rfGain: 0.5, squelch: 0.2 },
  sub: { rfGain: 0.75, squelch: 0.4 },
  fieldStatus: {
    'main.rfGain': fresh(), 'main.squelch': fresh(),
    'sub.rfGain': fresh(), 'sub.squelch': fresh(),
  },
  ...over,
} as unknown as ServerState);
const caps = (over: Record<string, unknown> = {}): Capabilities => ({
  stateContractVersion: 1,
  providerGeneration: 3,
  capabilities: ['rf_gain', 'squelch'],
  receivers: 1,
  vfoScheme: 'single',
  ...over,
} as unknown as Capabilities);
const command = (name: 'set_rf_gain' | 'set_squelch', over: Partial<CommandLifecycle> = {}): CommandLifecycle => ({
  id: name, name, params: { level: 128, receiver: 0 }, originalEpoch: 7,
  createdAt: 1, updatedAt: 1, timeoutMs: 5_000, status: 'pending', providerGeneration: 3,
  ...over,
});
const connected = { state: 'connected' as const, epoch: 7 };

describe('qualified RF/SQL command feedback', () => {
  afterEach(() => {
    h.state = null; h.caps = null; h.commands = [];
    h.stateReads = 0; h.capsReads = 0; h.commandReads = 0; h.sessionReads = 0;
    h.session = { state: 'connected', epoch: 7 };
    h.active = 'MAIN'; h.operational = true; h.project.mockClear();
  });

  it('defaults to runtime session after capturing every reactive dependency before guards', () => {
    h.session = { state: 'disconnected', epoch: -1 };
    expect(getRfSqlControlFeedback()).toBeNull();
    expect([h.stateReads, h.capsReads, h.commandReads, h.sessionReads]).toEqual([1, 1, 1, 1]);

    h.state = state(); h.caps = caps();
    h.session = { state: 'connected', epoch: 7 };
    expect(getRfSqlControlFeedback()).toMatchObject({
      rf: { feedback: { confirmed: 0.5, sessionEpoch: 7 } },
      sql: { feedback: { confirmed: 0.2, sessionEpoch: 7 } },
    });
    expect([h.stateReads, h.capsReads, h.commandReads, h.sessionReads]).toEqual([2, 2, 2, 2]);
  });

  it('captures one state/caps/list/session authority and freezes normalized lanes', () => {
    h.state = state(); h.caps = caps();
    h.commands = [command('set_rf_gain'), command('set_squelch', {
      params: { level: 51, receiver: 0 }, createdAt: 2,
    })];

    const pair = getRfSqlControlFeedback(connected)!;

    expect([h.stateReads, h.capsReads, h.commandReads]).toEqual([1, 1, 1]);
    expect(h.project).toHaveBeenCalledExactlyOnceWith(h.state, h.caps);
    expect(pair.rf).toMatchObject({ command: 'set_rf_gain', feedback: {
      confirmed: 0.5, target: 128 / 255, requestedTarget: 128 / 255,
      phase: 'submitted', providerGeneration: 3, sessionEpoch: 7,
      scope: { control: 'rf-gain', receiver: 0 },
    } });
    expect(pair.sql).toMatchObject({ command: 'set_squelch', feedback: {
      confirmed: 0.2, target: 51 / 255, phase: 'submitted',
      scope: { control: 'squelch', receiver: 0 },
    } });
    expect(Object.isFrozen(pair)).toBe(true);
    expect(Object.isFrozen(pair.rf)).toBe(true);
    expect(Object.isFrozen(pair.rf.feedback)).toBe(true);
    expect(Object.isFrozen(pair.sql.feedback)).toBe(true);
  });

  it.each([
    ['disconnected', { state: 'disconnected' as const, epoch: 7 }, state(), caps()],
    ['invalid epoch', { state: 'connected' as const, epoch: -1 }, state(), caps()],
    ['missing state', connected, null, caps()],
    ['missing caps', connected, state(), null],
    ['state contract', connected, state({ stateContractVersion: 2 }), caps()],
    ['caps contract', connected, state(), caps({ stateContractVersion: 2 })],
    ['provider mismatch', connected, state(), caps({ providerGeneration: 4 })],
  ])('returns null for unresolved %s authority', (_name, session, currentState, currentCaps) => {
    h.state = currentState; h.caps = currentCaps;
    expect(getRfSqlControlFeedback(session)).toBeNull();
  });

  it('uses canonical SUB and rejects unknown or non-operational receiver identity', () => {
    h.state = state({ active: 'SUB' });
    h.caps = caps({ receivers: 2, vfoScheme: 'main_sub', capabilities: ['dual_rx', 'rf_gain', 'squelch'] });
    h.active = 'SUB';
    expect(getRfSqlControlFeedback(connected)).toMatchObject({
      rf: { feedback: { confirmed: 0.75, scope: { receiver: 1 } } },
      sql: { feedback: { confirmed: 0.4, scope: { receiver: 1 } } },
    });
    h.active = null;
    expect(getRfSqlControlFeedback(connected)).toBeNull();
    h.active = 'SUB'; h.operational = false;
    expect(getRfSqlControlFeedback(connected)).toBeNull();
  });

  it('keeps qualified and unavailable lanes independent', () => {
    h.state = state(); h.caps = caps({ capabilities: ['rf_gain'] });
    const pair = getRfSqlControlFeedback(connected)!;
    expect(pair.rf.feedback).toMatchObject({ availability: 'available', confirmed: 0.5, phase: 'idle' });
    expect(pair.sql.feedback).toMatchObject({
      availability: 'unavailable', confirmed: null, target: null,
      requestedTarget: null, phase: 'unavailable', busy: false,
      providerGeneration: 3, sessionEpoch: 7, scope: { control: 'squelch', receiver: 0 },
    });
  });

  it.each([
    ['negative leaf marker', { 'main.rfGain': fresh(-1), 'main.squelch': fresh() }],
    ['missing present parent', { main: { ...fresh(), observed: false }, 'main.rfGain': fresh(), 'main.squelch': fresh() }],
  ])('masks RF when evidence has a %s', (_name, fieldStatus) => {
    h.state = state({ fieldStatus }); h.caps = caps();
    const pair = getRfSqlControlFeedback(connected)!;
    expect(pair.rf.feedback).toMatchObject({ availability: 'unavailable', phase: 'unavailable', confirmed: null });
    expect(pair.sql.feedback).toMatchObject(_name === 'negative leaf marker'
      ? { availability: 'available', confirmed: 0.2 }
      : { availability: 'unavailable', confirmed: null });
  });

  it('keeps both lanes available through a stale present parent (MOR-2425/R29)', () => {
    h.state = state({
      fieldStatus: {
        main: { ...fresh(), freshness: 'stale' as const }, 'main.rfGain': fresh(), 'main.squelch': fresh(),
      },
    });
    h.caps = caps();
    const pair = getRfSqlControlFeedback(connected)!;
    expect(pair.rf.feedback).toMatchObject({ availability: 'available', confirmed: 0.5 });
    expect(pair.sql.feedback).toMatchObject({ availability: 'available', confirmed: 0.2 });
  });

  it('preserves the accepted absent-ancestor skip', () => {
    h.state = state(); h.caps = caps();
    expect(getRfSqlControlFeedback(connected)).toMatchObject({
      rf: { feedback: { availability: 'available', confirmed: 0.5 } },
      sql: { feedback: { availability: 'available', confirmed: 0.2 } },
    });
  });

  it('rejects stale-provider outcomes while retaining current provider identity', () => {
    h.state = state(); h.caps = caps();
    h.commands = [command('set_rf_gain', {
      providerGeneration: 2, status: 'failed', error: 'old provider',
    })];
    expect(getRfSqlControlFeedback(connected)?.rf.feedback).toMatchObject({
      providerGeneration: 3, confirmed: 0.5, target: null, phase: 'idle', outcome: null,
    });
  });
});
