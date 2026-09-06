import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { CommandLifecycle } from '$lib/stores/commands.svelte';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  commands: [] as CommandLifecycle[],
  session: { state: 'connected' as 'connected' | 'disconnected', epoch: 7 },
  active: 'MAIN' as 'MAIN' | 'SUB' | null,
  operational: true,
  stateReads: 0, capsReads: 0, commandReads: 0, sessionReads: 0,
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
  isCommandLifecycleSuperseded: (command: CommandLifecycle) => command.id.endsWith('-old'),
}));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({
  toRadioViewModel: () => h.active === null
    ? { activeReceiver: { status: 'unknown' }, receiverIndicators: [] }
    : {
      activeReceiver: { status: 'known', receiver: h.active },
      receiverIndicators: [{
        receiver: h.active,
        availability: { structural: true, operational: h.operational },
      }],
    },
}));

import { DSP_COMMAND_DESCRIPTORS, type DspCommandFeedbackField } from '$lib/stores/commands.svelte';
import { getDspControlFeedback } from '../panel-adapters';

const FIELDS = [
  'nbLevel', 'nbWidth', 'notchFilter', 'manualNotchWidth', 'agcTimeConstant',
] as const satisfies readonly DspCommandFeedbackField[];
const VALUES: Readonly<Record<DspCommandFeedbackField, number>> = {
  nbLevel: 60, nbWidth: 70, notchFilter: -80, manualNotchWidth: 3, agcTimeConstant: 900,
};
const fresh = (marker = 5) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
const state = (over: Record<string, unknown> = {}): ServerState => ({
  stateContractVersion: 1, providerGeneration: 3, active: 'MAIN', nbWidth: VALUES.nbWidth,
  main: {
    nbLevel: VALUES.nbLevel, notchFilter: VALUES.notchFilter,
    manualNotchWidth: VALUES.manualNotchWidth, agcTimeConstant: VALUES.agcTimeConstant,
  },
  sub: { nbLevel: 61, notchFilter: -81, manualNotchWidth: 4, agcTimeConstant: 901 },
  fieldStatus: {
    nbWidth: fresh(), 'main.nbLevel': fresh(), 'main.notchFilter': fresh(),
    'main.manualNotchWidth': fresh(), 'main.agcTimeConstant': fresh(),
    'sub.nbLevel': fresh(), 'sub.notchFilter': fresh(),
    'sub.manualNotchWidth': fresh(), 'sub.agcTimeConstant': fresh(),
  },
  ...over,
} as unknown as ServerState);
const caps = (over: Record<string, unknown> = {}): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 3, receivers: 1, vfoScheme: 'single',
  capabilities: ['nb', 'notch', 'agc'], controls: { nb_depth: { min: 0, max: 255, step: 1 } },
  ...over,
} as unknown as Capabilities);
const connected = { state: 'connected' as const, epoch: 7 };
const command = (
  field: DspCommandFeedbackField, target: number, over: Partial<CommandLifecycle> = {},
): CommandLifecycle => {
  const descriptor = DSP_COMMAND_DESCRIPTORS[field];
  const receiver = h.active === 'SUB' ? 1 : 0;
  const key = field === 'nbLevel' || field === 'nbWidth' ? 'level' : 'value';
  return {
    id: field, name: descriptor.intentName,
    params: field === 'nbWidth' ? { [key]: target } : { [key]: target, receiver },
    originalEpoch: 7, createdAt: 1, updatedAt: 1, timeoutMs: 5_000,
    status: 'pending', providerGeneration: 3, ...over,
  };
};

describe('qualified raw DSP command feedback', () => {
  afterEach(() => {
    h.state = null; h.caps = null; h.commands = [];
    h.session = { state: 'connected', epoch: 7 }; h.active = 'MAIN'; h.operational = true;
    h.stateReads = 0; h.capsReads = 0; h.commandReads = 0; h.sessionReads = 0;
  });

  it('reads every reactive authority before unavailable guards and recovers on replacement', () => {
    h.session = { state: 'disconnected', epoch: -1 };
    expect(getDspControlFeedback('nbLevel')).toMatchObject({
      confirmed: null, target: null, requestedTarget: null,
      phase: 'unavailable', availability: 'unavailable', sessionEpoch: -1,
      scope: { control: 'nb-level', receiver: 0 },
    });
    expect([h.stateReads, h.capsReads, h.commandReads, h.sessionReads]).toEqual([1, 1, 1, 1]);

    h.state = state(); h.caps = caps(); h.session = connected;
    expect(getDspControlFeedback('nbLevel')).toMatchObject({
      confirmed: 60, phase: 'idle', availability: 'available', sessionEpoch: 7,
    });
    expect([h.stateReads, h.capsReads, h.commandReads, h.sessionReads]).toEqual([2, 2, 2, 2]);
  });

  it.each(FIELDS)('projects exact raw submitted feedback for %s', field => {
    h.state = state(); h.caps = caps();
    const target = VALUES[field] + 10;
    h.commands = [command(field, target)];
    const feedback = getDspControlFeedback(field, connected);
    expect(feedback).toMatchObject({
      confirmed: VALUES[field], target, requestedTarget: target,
      phase: 'submitted', availability: 'available', busy: true,
      providerGeneration: 3, sessionEpoch: 7,
      scope: DSP_COMMAND_DESCRIPTORS[field].scope({ params: h.commands[0].params }),
    });
    expect(h.sessionReads).toBe(0);
  });

  it('uses canonical SUB receiver fields while NB Width keeps global receiver-zero identity', () => {
    h.active = 'SUB';
    h.state = state({ active: 'SUB' });
    h.caps = caps({ receivers: 2, vfoScheme: 'main_sub', capabilities: ['dual_rx', 'nb', 'notch', 'agc'] });
    expect(getDspControlFeedback('notchFilter', connected)).toMatchObject({
      confirmed: -81, scope: { control: 'notch-position', receiver: 1 },
    });
    expect(getDspControlFeedback('nbWidth', connected)).toMatchObject({
      confirmed: 70, scope: { control: 'nb-width', receiver: 0 },
    });
  });

  it('keeps fields independent and selects only the latest non-superseded lifecycle', () => {
    h.state = state(); h.caps = caps();
    h.commands = [
      command('nbLevel', 90, { id: 'level-old', createdAt: 1 }),
      command('notchFilter', -100, { id: 'notch' }),
      command('nbLevel', 100, { id: 'level-new', createdAt: 2 }),
    ];
    expect(getDspControlFeedback('nbLevel', connected)).toMatchObject({ target: 100, requestedTarget: 100 });
    expect(getDspControlFeedback('notchFilter', connected)).toMatchObject({ target: -100, confirmed: -80 });

    h.state = state({
      fieldStatus: { ...state().fieldStatus, 'main.nbLevel': { ...fresh(), freshness: 'stale' } },
    });
    expect(getDspControlFeedback('nbLevel', connected)).toMatchObject({
      phase: 'unavailable', confirmed: null, target: null, outcome: null,
    });
    expect(getDspControlFeedback('notchFilter', connected)).toMatchObject({
      phase: 'submitted', confirmed: -80, target: -100,
    });
  });

  it.each([
    ['failed', 'nbLevel', { status: 'failed', error: 'radio refused' }],
    ['timed-out', 'nbWidth', { status: 'timed-out' }],
    ['cancelled', 'agcTimeConstant', { status: 'cancelled' }],
  ] as const)('projects a real %s terminal outcome', (phase, field, outcome) => {
    h.state = state(); h.caps = caps();
    h.commands = [command(field, VALUES[field] + 1, outcome)];
    expect(getDspControlFeedback(field, connected)).toMatchObject({
      target: null, requestedTarget: VALUES[field] + 1,
      phase, outcome: { phase }, busy: false,
    });
  });

  it.each([
    ['disconnected', { state: 'disconnected' as const, epoch: 7 }, state(), caps()],
    ['invalid epoch', { state: 'connected' as const, epoch: -1 }, state(), caps()],
    ['state contract', connected, state({ stateContractVersion: 2 }), caps()],
    ['caps contract', connected, state(), caps({ stateContractVersion: 2 })],
    ['provider mismatch', connected, state(), caps({ providerGeneration: 4 })],
  ])('fails closed for %s authority', (_name, session, currentState, currentCaps) => {
    h.state = currentState; h.caps = currentCaps;
    expect(getDspControlFeedback('manualNotchWidth', session)).toMatchObject({
      availability: 'unavailable', confirmed: null, target: null, outcome: null,
    });
  });

  it('requires active-receiver admission and each field structural contract', () => {
    h.state = state(); h.caps = caps();
    h.active = null;
    expect(getDspControlFeedback('nbWidth', connected).availability).toBe('unavailable');
    h.active = 'MAIN'; h.operational = false;
    expect(getDspControlFeedback('nbLevel', connected).availability).toBe('unavailable');
    h.operational = true;
    h.caps = caps({ capabilities: ['notch', 'agc'], controls: {} });
    expect(getDspControlFeedback('nbLevel', connected).availability).toBe('unavailable');
    expect(getDspControlFeedback('nbWidth', connected).availability).toBe('unavailable');
    expect(getDspControlFeedback('notchFilter', connected).availability).toBe('available');
  });

  it('drops old provider and session lifecycles after authority replacement', () => {
    h.state = state({ providerGeneration: 4 });
    h.caps = caps({ providerGeneration: 4 });
    h.commands = [command('nbWidth', 100, {
      originalEpoch: 6, providerGeneration: 3, status: 'failed', error: 'old provider',
    })];
    expect(getDspControlFeedback('nbWidth', connected)).toMatchObject({
      providerGeneration: 4, sessionEpoch: 7, confirmed: 70,
      phase: 'idle', target: null, requestedTarget: null, outcome: null,
    });
  });
});
