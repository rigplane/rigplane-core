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
  indicatorCount: 1,
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
      receiverIndicators: Array.from({ length: h.indicatorCount }, () => ({
        receiver: h.active,
        availability: { structural: true, operational: h.operational },
      })),
    },
}));

import { DSP_COMMAND_DESCRIPTORS, type DspCommandFeedbackField } from '$lib/stores/commands.svelte';
import {
  getDspControlFeedback,
  projectDspControlFeedbackToDisplay,
  type ControlFeedback,
} from '../panel-adapters';

const FIELDS = [
  'nbLevel', 'nbWidth', 'nrLevel', 'nbDepth',
  'notchFilter', 'manualNotchWidth', 'agcTimeConstant',
] as const satisfies readonly DspCommandFeedbackField[];
const VALUES: Readonly<Record<DspCommandFeedbackField, number>> = {
  nbLevel: 60, nbWidth: 70, nrLevel: 128, nbDepth: 5,
  notchFilter: -80, manualNotchWidth: 3, agcTimeConstant: 900,
};
const fresh = (marker = 5) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
const state = (over: Record<string, unknown> = {}): ServerState => ({
  stateContractVersion: 1, providerGeneration: 3, active: 'MAIN',
  nbWidth: VALUES.nbWidth, nbDepth: VALUES.nbDepth,
  main: {
    nbLevel: VALUES.nbLevel, nrLevel: VALUES.nrLevel, notchFilter: VALUES.notchFilter,
    manualNotchWidth: VALUES.manualNotchWidth, agcTimeConstant: VALUES.agcTimeConstant,
  },
  sub: {
    nbLevel: 61, nrLevel: 129, notchFilter: -81,
    manualNotchWidth: 4, agcTimeConstant: 901,
  },
  fieldStatus: {
    nbWidth: fresh(), nbDepth: fresh(),
    'main.nbLevel': fresh(), 'main.nrLevel': fresh(), 'main.notchFilter': fresh(),
    'main.manualNotchWidth': fresh(), 'main.agcTimeConstant': fresh(),
    'sub.nbLevel': fresh(), 'sub.nrLevel': fresh(), 'sub.notchFilter': fresh(),
    'sub.manualNotchWidth': fresh(), 'sub.agcTimeConstant': fresh(),
  },
  ...over,
} as unknown as ServerState);
const caps = (over: Record<string, unknown> = {}): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 3, receivers: 1, vfoScheme: 'single',
  capabilities: ['nb', 'nr', 'notch', 'agc'],
  controls: { nb_depth: { raw_min: 0, raw_max: 9, display_min: 1, display_max: 10 } },
  ...over,
} as unknown as Capabilities);
const connected = { state: 'connected' as const, epoch: 7 };
const command = (
  field: DspCommandFeedbackField, target: number, over: Partial<CommandLifecycle> = {},
): CommandLifecycle => {
  const descriptor = DSP_COMMAND_DESCRIPTORS[field];
  const receiver = h.active === 'SUB' ? 1 : 0;
  const key = field === 'nbLevel' || field === 'nbWidth'
    || field === 'nrLevel' || field === 'nbDepth' ? 'level' : 'value';
  const global = field === 'nbWidth' || field === 'nbDepth';
  return {
    id: field, name: descriptor.intentName,
    params: global ? { [key]: target } : { [key]: target, receiver },
    originalEpoch: 7, createdAt: 1, updatedAt: 1, timeoutMs: 5_000,
    status: 'pending', providerGeneration: 3, ...over,
  };
};

describe('qualified raw DSP command feedback', () => {
  afterEach(() => {
    h.state = null; h.caps = null; h.commands = [];
    h.session = { state: 'connected', epoch: 7 }; h.active = 'MAIN'; h.operational = true;
    h.indicatorCount = 1;
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

  it('uses canonical SUB receiver fields while NB Width and NB Depth keep global identity', () => {
    h.active = 'SUB';
    h.state = state({ active: 'SUB' });
    h.caps = caps({
      receivers: 2, vfoScheme: 'main_sub',
      capabilities: ['dual_rx', 'nb', 'nr', 'notch', 'agc'],
    });
    expect(getDspControlFeedback('nrLevel', connected)).toMatchObject({
      confirmed: 129, scope: { control: 'nr-level', receiver: 1 },
    });
    expect(getDspControlFeedback('notchFilter', connected)).toMatchObject({
      confirmed: -81, scope: { control: 'notch-position', receiver: 1 },
    });
    expect(getDspControlFeedback('nbWidth', connected)).toMatchObject({
      confirmed: 70, scope: { control: 'nb-width', receiver: 0 },
    });
    expect(getDspControlFeedback('nbDepth', connected)).toMatchObject({
      confirmed: 5, scope: { control: 'nb-depth', receiver: 0 },
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
    expect(getDspControlFeedback('nrLevel', connected).availability).toBe('unavailable');
    h.commands = [command('nbDepth', 7)];
    expect(getDspControlFeedback('nbDepth', connected)).toMatchObject({
      availability: 'unavailable', phase: 'unavailable', target: null,
    });
    expect(getDspControlFeedback('notchFilter', connected).availability).toBe('available');
  });

  it('fails closed for ambiguous and missing or stale transformed observations', () => {
    h.state = state(); h.caps = caps(); h.indicatorCount = 2;
    expect(getDspControlFeedback('nrLevel', connected).availability).toBe('unavailable');
    h.indicatorCount = 1;
    h.state = state({
      fieldStatus: { ...state().fieldStatus, 'main.nrLevel': undefined },
    });
    expect(getDspControlFeedback('nrLevel', connected).availability).toBe('unavailable');
    h.state = state({
      fieldStatus: { ...state().fieldStatus, nbDepth: { ...fresh(), freshness: 'stale' } },
    });
    expect(getDspControlFeedback('nbDepth', connected).availability).toBe('unavailable');
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

const LEGACY_NR_CAPS = caps({
  controls: {
    nr_level: { raw_min: 0, raw_max: 255, display_min: 0, display_max: 15 },
    nb_depth: { raw_min: 0, raw_max: 9, display_min: 1, display_max: 10 },
  },
});
const EXACT_NR_CAPS = caps({
  controls: {
    nr_level: {
      mapping: 'identity', raw_min: 0, raw_max: 10, raw_step: 1, raw_origin: 0,
      display_min: '0', display_max: '10', display_step: '1', display_origin: '0',
      display_unit: 'level', quantization: 'reject', restoration: 'exact',
    },
    nb_depth: { raw_min: 0, raw_max: 9, display_min: 1, display_max: 10 },
  },
});

function rawFeedback(over: Partial<ControlFeedback<number>> = {}): Readonly<ControlFeedback<number>> {
  return Object.freeze({
    confirmed: 128, target: 136, requestedTarget: 136,
    phase: 'awaiting-confirmation', busy: true, availability: 'available',
    outcome: null, lifecycleId: '[7,"nr"]', transitionId: '[7,"nr","acknowledged"]',
    providerGeneration: 3, sessionEpoch: 7,
    scope: Object.freeze({ control: 'nr-level', receiver: 0 as const }),
    repeatPolicy: 'latest-target-wins', ...over,
  });
}

describe('pure transformed DSP feedback projection', () => {
  afterEach(() => {
    h.state = null; h.caps = null; h.commands = [];
    h.session = { state: 'connected', epoch: 7 }; h.active = 'MAIN'; h.operational = true;
    h.indicatorCount = 1;
    h.stateReads = 0; h.capsReads = 0; h.commandReads = 0; h.sessionReads = 0;
  });

  it('keeps a raw NR alias busy until exact confirmation while all display slots equal 8', () => {
    h.state = state({ main: { ...state().main, nrLevel: 128 } });
    h.caps = LEGACY_NR_CAPS;
    h.commands = [command('nrLevel', 136, { status: 'acknowledged' })];
    const raw = getDspControlFeedback('nrLevel', connected);
    const display = projectDspControlFeedbackToDisplay('nrLevel', raw, LEGACY_NR_CAPS);
    expect(display).toMatchObject({
      confirmed: 8, target: 8, requestedTarget: 8,
      phase: 'awaiting-confirmation', busy: true, availability: 'available',
    });
    expect(raw).toMatchObject({ confirmed: 128, target: 136, requestedTarget: 136, busy: true });
  });

  it('reuses exact-domain NR and explicit-capability NB Depth mappings', () => {
    expect(projectDspControlFeedbackToDisplay(
      'nrLevel', rawFeedback({ confirmed: 4, target: 7, requestedTarget: 7 }), EXACT_NR_CAPS,
    )).toMatchObject({ confirmed: 4, target: 7, requestedTarget: 7 });
    expect(projectDspControlFeedbackToDisplay(
      'nbDepth', rawFeedback({ confirmed: 0, target: 5, requestedTarget: 9 }), LEGACY_NR_CAPS,
    )).toMatchObject({ confirmed: 1, target: 6, requestedTarget: 10 });
  });

  it.each([
    ['nr below exact domain', 'nrLevel', -1, EXACT_NR_CAPS],
    ['nb below explicit range', 'nbDepth', -1, LEGACY_NR_CAPS],
    ['nb above explicit range', 'nbDepth', 10, LEGACY_NR_CAPS],
    ['nb fractional raw', 'nbDepth', 4.5, LEGACY_NR_CAPS],
    ['nb malformed selected range', 'nbDepth', 5, caps({
      controls: { nb_depth: { raw_min: 0, raw_max: 9, display_min: 1, display_max: Infinity } },
    })],
    ['nb empty selected range', 'nbDepth', 5, caps({
      controls: { nb_depth: { raw_min: 5, raw_max: 5, display_min: 1, display_max: 10 } },
    })],
  ] as const)('fails closed for %s without clamping invalid raw evidence', (
    _case, field, confirmed, currentCaps,
  ) => {
    const raw = rawFeedback({ confirmed, target: null, requestedTarget: null });
    const display = projectDspControlFeedbackToDisplay(field, raw, currentCaps);
    expect(display).toMatchObject({
      confirmed: null, target: null, requestedTarget: null,
      phase: 'unavailable', busy: false, availability: 'unavailable',
      outcome: null, lifecycleId: null, transitionId: null,
      providerGeneration: 3, sessionEpoch: 7,
      scope: raw.scope, repeatPolicy: raw.repeatPolicy,
    });
    expect(raw.confirmed).toBe(confirmed);
  });

  it.each(['confirmed', 'target', 'requestedTarget'] as const)(
    'fails closed when the present %s slot cannot be projected',
    slot => {
      const raw = rawFeedback({ confirmed: 4, target: 5, requestedTarget: 6, [slot]: 10 });
      expect(projectDspControlFeedbackToDisplay('nbDepth', raw, LEGACY_NR_CAPS)).toMatchObject({
        confirmed: null, target: null, requestedTarget: null,
        phase: 'unavailable', busy: false, availability: 'unavailable',
      });
      expect(raw[slot]).toBe(10);
    },
  );

  it('maps nullable slots independently and preserves every metadata field and reference', () => {
    const outcome = Object.freeze({ phase: 'failed' as const, error: 'radio refused' });
    const scope = Object.freeze({ control: 'nb-depth', receiver: 0 as const });
    const raw = rawFeedback({
      confirmed: 5, target: null, requestedTarget: 9,
      phase: 'failed', busy: false, outcome, scope,
    });
    const display = projectDspControlFeedbackToDisplay('nbDepth', raw, LEGACY_NR_CAPS);
    expect(display).toEqual({ ...raw, confirmed: 6, target: null, requestedTarget: 10 });
    expect(display.outcome).toBe(outcome);
    expect(display.scope).toBe(scope);
    expect(Object.isFrozen(display)).toBe(true);
    expect([h.stateReads, h.capsReads, h.commandReads, h.sessionReads]).toEqual([0, 0, 0, 0]);
  });
});
