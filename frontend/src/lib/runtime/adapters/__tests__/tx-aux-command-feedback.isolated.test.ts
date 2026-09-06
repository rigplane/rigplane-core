import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  session: { state: 'disconnected' as 'connected' | 'disconnected', epoch: -1 },
  stateReads: 0, capsReads: 0, sessionReads: 0,
  listeners: new Set<(state: ServerState | null) => void>(),
}));

// Only the runtime-owned state/capability/session inputs and radio-store
// subscription are replaceable test boundaries. Descriptor parsing,
// lifecycle storage, reconciliation, projection, and qualification stay real.
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { h.stateReads += 1; return h.state; },
    get caps() { h.capsReads += 1; return h.caps; },
    get controlSession() { h.sessionReads += 1; return h.session; },
  },
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: () => h.state,
  subscribeRadioState: (listener: (state: ServerState | null) => void) => {
    h.listeners.add(listener);
    listener(h.state);
    return () => h.listeners.delete(listener);
  },
}));

import {
  TX_AUX_COMMAND_DESCRIPTORS, acknowledgeCommand, beginCommand,
  cancelPendingCommands, failCommand, getCommandLifecycle, resetCommandLifecycle,
} from '$lib/stores/commands.svelte';
import {
  getTxAuxControlFeedback, type TxAuxControlFeedbackField,
} from '../panel-adapters';

const FIELDS = [
  'micGain', 'driveGain', 'voxGain', 'antiVoxGain', 'voxDelay',
  'compressorLevel', 'monitorGain',
] as const satisfies readonly TxAuxControlFeedbackField[];
const ALL_CAPABILITIES = ['tx', 'drive_gain', 'vox', 'compressor', 'monitor'];
const VALUES: Readonly<Record<TxAuxControlFeedbackField, number>> = {
  micGain: 120, driveGain: 121, voxGain: 122, antiVoxGain: 123,
  voxDelay: 10, compressorLevel: 124, monitorGain: 125,
};
const REQUIRED: Readonly<Record<TxAuxControlFeedbackField, readonly string[]>> = {
  micGain: ['tx'], driveGain: ['tx', 'drive_gain'],
  voxGain: ['tx', 'vox'], antiVoxGain: ['tx', 'vox'], voxDelay: ['tx', 'vox'],
  compressorLevel: ['tx', 'compressor'], monitorGain: ['tx', 'monitor'],
};
const connected = { state: 'connected' as const, epoch: 7 };

const fresh = (marker = 1) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
function state(over: Record<string, unknown> = {}): ServerState {
  return {
    stateContractVersion: 1, providerGeneration: 3, active: 'MAIN', main: {}, sub: {},
    ...VALUES,
    fieldStatus: Object.fromEntries(FIELDS.map(field => [field, fresh()])),
    ...over,
  } as unknown as ServerState;
}
function caps(over: Record<string, unknown> = {}): Capabilities {
  return {
    stateContractVersion: 1, providerGeneration: 3,
    capabilities: [...ALL_CAPABILITIES], ...over,
  } as unknown as Capabilities;
}
function emitState(next: ServerState): void {
  h.state = next;
  for (const listener of h.listeners) listener(next);
}
function begin(
  field: TxAuxControlFeedbackField, target: number, id: string = field, timeoutMs = 5_000,
) {
  const descriptor = TX_AUX_COMMAND_DESCRIPTORS[field];
  return beginCommand({
    id, name: descriptor.intentName, params: { level: target },
    originalEpoch: h.session.epoch, timeoutMs,
  });
}

describe('imperative qualified raw TX/VOX command feedback', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetCommandLifecycle();
    h.state = state(); h.caps = caps(); h.session = connected;
    h.stateReads = 0; h.capsReads = 0; h.sessionReads = 0;
  });

  afterEach(() => {
    resetCommandLifecycle();
    vi.useRealTimers();
  });

  it('reads all reactive authority inputs before failing closed', () => {
    h.state = null; h.caps = null;
    h.session = { state: 'disconnected', epoch: -1 };
    const feedback = getTxAuxControlFeedback('micGain');
    expect([h.stateReads, h.capsReads, h.sessionReads]).toEqual([1, 1, 1]);
    expect(feedback).toMatchObject({
      confirmed: null, target: null, requestedTarget: null,
      phase: 'unavailable', availability: 'unavailable', busy: false,
      sessionEpoch: -1, scope: { control: 'mic-gain', receiver: 0 },
    });
  });

  it.each(FIELDS)('projects exact raw submitted feedback for %s', field => {
    const target = field === 'voxDelay' ? 20 : VALUES[field] + 10;
    begin(field, target);
    const feedback = getTxAuxControlFeedback(field, connected);
    expect(feedback).toMatchObject({
      confirmed: VALUES[field], target, requestedTarget: target,
      phase: 'submitted', availability: 'available', busy: true,
      providerGeneration: 3, sessionEpoch: 7,
      scope: { control: TX_AUX_COMMAND_DESCRIPTORS[field].scope({ params: { level: 0 } })!.control,
        receiver: 0 },
    });
    expect(h.sessionReads).toBe(0);
  });

  it('keeps ACK awaiting across active-receiver, other-field, stale, and mismatch observations', () => {
    h.state = state({ micGain: 100 });
    const command = begin('micGain', 128, 'mic');
    expect(getTxAuxControlFeedback('micGain', connected).phase).toBe('submitted');
    acknowledgeCommand(command.id, 7, 7);
    expect(getTxAuxControlFeedback('micGain', connected)).toMatchObject({
      phase: 'awaiting-confirmation', target: 128, scope: { control: 'mic-gain', receiver: 0 },
    });

    emitState(state({
      active: 'SUB', micGain: 100,
      fieldStatus: { ...state().fieldStatus, micGain: fresh(1), driveGain: fresh(2) },
    }));
    expect(getCommandLifecycle(command.id, 7)?.status).toBe('acknowledged');
    expect(getTxAuxControlFeedback('micGain', connected).scope.receiver).toBe(0);

    emitState(state({ micGain: 127, fieldStatus: { ...state().fieldStatus, micGain: fresh(2) } }));
    emitState(state({
      micGain: 128,
      fieldStatus: { ...state().fieldStatus, micGain: { ...fresh(3), freshness: 'stale' } },
    }));
    expect(getCommandLifecycle(command.id, 7)?.status).toBe('acknowledged');

    emitState(state({ micGain: 128, fieldStatus: { ...state().fieldStatus, micGain: fresh(4) } }));
    expect(getCommandLifecycle(command.id, 7)?.status).toBe('confirmed');
    expect(getTxAuxControlFeedback('micGain', connected)).toMatchObject({
      confirmed: 128, target: null, requestedTarget: 128,
      phase: 'confirmed', outcome: { phase: 'confirmed' }, busy: false,
    });
  });

  it('selects the latest target per control while independent controls coexist', () => {
    const micA = begin('micGain', 100, 'mic-a');
    begin('driveGain', 200, 'drive');
    begin('micGain', 150, 'mic-b');
    expect(getTxAuxControlFeedback('micGain', connected)).toMatchObject({
      target: 150, requestedTarget: 150,
    });
    expect(getTxAuxControlFeedback('driveGain', connected)).toMatchObject({
      target: 200, requestedTarget: 200,
    });
    expect(getTxAuxControlFeedback('micGain', connected).lifecycleId)
      .not.toContain(micA.id);
  });

  it.each([
    ['failed', 'micGain', (id: string) => failCommand(id, 7, 7, 'radio refused')],
    ['timed-out', 'driveGain', () => vi.advanceTimersByTime(26)],
    ['cancelled', 'voxGain', () => cancelPendingCommands(7, 'session-disconnected')],
  ] as const)('projects the real %s terminal outcome', (phase, field, complete) => {
    const command = begin(field, VALUES[field] + 1, phase, 25);
    complete(command.id);
    expect(getTxAuxControlFeedback(field, connected)).toMatchObject({
      target: null, requestedTarget: VALUES[field] + 1,
      phase, outcome: { phase }, busy: false,
    });
  });

  it.each(FIELDS)('requires the declared capability set for %s', field => {
    for (const missing of REQUIRED[field]) {
      h.caps = caps({ capabilities: ALL_CAPABILITIES.filter(tag => tag !== missing) });
      expect(getTxAuxControlFeedback(field, connected)).toMatchObject({
        availability: 'unavailable', confirmed: null, target: null,
      });
    }
  });

  it.each([
    ['not observed', { ...fresh(), observed: false }, VALUES.micGain],
    ['stale', { ...fresh(), freshness: 'stale' as const }, VALUES.micGain],
    ['unavailable', { ...fresh(), availability: 'unavailable' as const }, VALUES.micGain],
    ['missing marker', { ...fresh(), lastObservedMonotonic: undefined }, VALUES.micGain],
    ['fractional truth', fresh(), 120.5],
    ['boolean truth', fresh(), true],
  ])('rejects %s field evidence without retaining canonical truth', (_name, evidence, value) => {
    h.state = state({
      micGain: value,
      fieldStatus: { ...state().fieldStatus, micGain: evidence },
    });
    expect(getTxAuxControlFeedback('micGain', connected)).toMatchObject({
      availability: 'unavailable', confirmed: null, target: null,
      requestedTarget: null, phase: 'unavailable', outcome: null,
    });
  });

  it.each([
    ['disconnected', { state: 'disconnected' as const, epoch: 7 }, state(), caps()],
    ['invalid epoch', { state: 'connected' as const, epoch: -1 }, state(), caps()],
    ['missing state', connected, null, caps()],
    ['missing caps', connected, state(), null],
    ['state contract', connected, state({ stateContractVersion: 2 }), caps()],
    ['caps contract', connected, state(), caps({ stateContractVersion: 2 })],
    ['provider mismatch', connected, state(), caps({ providerGeneration: 4 })],
  ])('returns explicit unavailable feedback for %s authority', (
    _name, session, currentState, currentCaps,
  ) => {
    h.state = currentState; h.caps = currentCaps;
    expect(getTxAuxControlFeedback('monitorGain', session)).toMatchObject({
      confirmed: null, target: null, requestedTarget: null,
      phase: 'unavailable', availability: 'unavailable', busy: false,
    });
  });

  it('imperatively projects current authority after provider and session replacement', () => {
    begin('compressorLevel', 200, 'old');
    expect(getTxAuxControlFeedback('compressorLevel', connected).phase).toBe('submitted');

    h.state = state({ providerGeneration: 4 });
    h.caps = caps({ providerGeneration: 4 });
    expect(getTxAuxControlFeedback('compressorLevel', connected)).toMatchObject({
      providerGeneration: 4, confirmed: VALUES.compressorLevel,
      phase: 'idle', target: null, outcome: null,
    });

    const epoch8 = { state: 'connected' as const, epoch: 8 };
    h.session = epoch8;
    expect(getTxAuxControlFeedback('compressorLevel')).toMatchObject({
      sessionEpoch: 8, phase: 'idle', target: null,
    });
    h.session = { state: 'disconnected', epoch: 8 };
    expect(getTxAuxControlFeedback('compressorLevel').availability).toBe('unavailable');

    h.session = epoch8;
    begin('compressorLevel', 210, 'current');
    expect(getTxAuxControlFeedback('compressorLevel')).toMatchObject({
      providerGeneration: 4, sessionEpoch: 8, target: 210, phase: 'submitted',
    });
  });
});
