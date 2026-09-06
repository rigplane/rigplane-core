import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { CommandLifecycle } from '$lib/stores/commands.svelte';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  session: { state: 'disconnected' as 'connected' | 'disconnected', epoch: -1 },
  commands: [] as CommandLifecycle[],
  stateReads: 0, capsReads: 0, sessionReads: 0, commandReads: 0,
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
vi.mock('$lib/runtime/commands/radio-intents', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/commands/radio-intents')>(),
  currentControlSessionEpoch: () => h.session.epoch,
}));

import {
  getCwPitchControlFeedback, getKeySpeedControlFeedback,
} from '../panel-adapters';

const fresh = (marker = 5) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
const state = (over: Record<string, unknown> = {}): ServerState => ({
  stateContractVersion: 1, providerGeneration: 3, active: 'MAIN',
  cwPitch: 640, keySpeed: 27, main: {}, sub: {},
  fieldStatus: { cwPitch: fresh(), keySpeed: fresh() }, ...over,
} as unknown as ServerState);
const caps = (over: Record<string, unknown> = {}): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 3, capabilities: ['cw'], ...over,
} as unknown as Capabilities);
const command = (name: 'set_cw_pitch' | 'set_key_speed', over: Partial<CommandLifecycle> = {}): CommandLifecycle => ({
  id: name, name, params: name === 'set_cw_pitch' ? { value: 650 } : { speed: 28 },
  originalEpoch: 7, createdAt: 1, updatedAt: 1, timeoutMs: 5_000,
  status: 'pending', providerGeneration: 3, ...over,
});
const connected = { state: 'connected' as const, epoch: 7 };

describe('qualified global CW command feedback', () => {
  afterEach(() => {
    h.state = null; h.caps = null; h.commands = [];
    h.session = { state: 'disconnected', epoch: -1 };
    h.stateReads = 0; h.capsReads = 0; h.sessionReads = 0; h.commandReads = 0;
  });

  it('reads canonical stores and lifecycles before failing closed on the default session', () => {
    const pitch = getCwPitchControlFeedback();
    expect([h.stateReads, h.capsReads, h.commandReads, h.sessionReads]).toEqual([1, 1, 1, 1]);
    expect(pitch).toMatchObject({
      confirmed: null, target: null, requestedTarget: null, phase: 'unavailable',
      availability: 'unavailable', busy: false, sessionEpoch: -1,
      scope: { control: 'cw-pitch', receiver: 0 },
    });
  });

  it('captures each explicit invocation consistently without reading the default session', () => {
    h.state = state(); h.caps = caps();
    h.commands = [command('set_cw_pitch'), command('set_key_speed')];
    expect(getCwPitchControlFeedback(connected)).toMatchObject({
      confirmed: 640, target: 650, requestedTarget: 650, phase: 'submitted',
      providerGeneration: 3, sessionEpoch: 7, scope: { control: 'cw-pitch', receiver: 0 },
    });
    expect(getKeySpeedControlFeedback(connected)).toMatchObject({
      confirmed: 27, target: 28, requestedTarget: 28, phase: 'submitted',
      providerGeneration: 3, sessionEpoch: 7, scope: { control: 'keyer-speed', receiver: 0 },
    });
    expect([h.stateReads, h.capsReads, h.commandReads, h.sessionReads]).toEqual([2, 2, 2, 0]);
  });

  it.each([
    ['disconnected', { state: 'disconnected' as const, epoch: 7 }, state(), caps()],
    ['invalid epoch', { state: 'connected' as const, epoch: -1 }, state(), caps()],
    ['missing state', connected, null, caps()],
    ['missing caps', connected, state(), null],
    ['state contract', connected, state({ stateContractVersion: 2 }), caps()],
    ['caps contract', connected, state(), caps({ stateContractVersion: 2 })],
    ['provider mismatch', connected, state(), caps({ providerGeneration: 4 })],
    ['missing capability', connected, state(), caps({ capabilities: [] })],
  ])('returns explicit unavailable feedback for %s authority', (_name, session, currentState, currentCaps) => {
    h.state = currentState; h.caps = currentCaps;
    const feedback = getCwPitchControlFeedback(session);
    expect([h.stateReads, h.capsReads, h.commandReads]).toEqual([1, 1, 1]);
    expect(feedback).toMatchObject({
      confirmed: null, target: null, requestedTarget: null,
      phase: 'unavailable', availability: 'unavailable', busy: false, outcome: null,
    });
  });

  it('qualifies pitch and speed independently from exact top-level current evidence', () => {
    h.state = state({ fieldStatus: {
      cwPitch: { ...fresh(), freshness: 'stale' }, keySpeed: fresh(),
    } });
    h.caps = caps();
    expect(getCwPitchControlFeedback(connected)).toMatchObject({
      availability: 'unavailable', confirmed: null,
    });
    expect(getKeySpeedControlFeedback(connected)).toMatchObject({
      availability: 'available', confirmed: 27,
    });
  });

  it.each([
    ['not observed', { ...fresh(), observed: false }],
    ['negative marker', fresh(-1)],
    ['unavailable evidence', { ...fresh(), availability: 'unavailable' }],
  ])('rejects %s without retaining canonical pitch', (_name, evidence) => {
    h.state = state({ fieldStatus: { cwPitch: evidence, keySpeed: fresh() } });
    h.caps = caps();
    expect(getCwPitchControlFeedback(connected)).toMatchObject({
      availability: 'unavailable', confirmed: null, target: null, phase: 'unavailable',
    });
  });

  it('rejects non-integer canonical truth even when observation metadata is current', () => {
    h.state = state({ cwPitch: 640.5, keySpeed: Number.NaN }); h.caps = caps();
    expect(getCwPitchControlFeedback(connected).availability).toBe('unavailable');
    expect(getKeySpeedControlFeedback(connected).availability).toBe('unavailable');
  });

  it('drops stale-provider outcomes while retaining current provider identity', () => {
    h.state = state(); h.caps = caps();
    h.commands = [command('set_cw_pitch', {
      providerGeneration: 2, status: 'failed', error: 'old provider',
    })];
    expect(getCwPitchControlFeedback(connected)).toMatchObject({
      providerGeneration: 3, confirmed: 640, target: null, phase: 'idle', outcome: null,
    });
  });
});
