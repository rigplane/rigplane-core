/**
 * MOR-1687 F2 — qualified AF/RF-power command feedback lanes. Pins the
 * `panel-adapters` accessors against the real descriptor registry, lifecycle
 * store, and reconciliation: confirmation requires the server-admitted
 * target, a fresh post-ack same-field readback, the session epoch, and the
 * declared capability; an old server leaves the lane idle.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  session: { state: 'disconnected' as 'connected' | 'disconnected', epoch: -1 },
  listeners: new Set<(state: ServerState | null) => void>(),
}));

// Only the runtime-owned state/capability/session inputs and the radio-store
// subscription are replaceable boundaries. Descriptor parsing, lifecycle
// storage, reconciliation, and projection stay real.
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return h.session; },
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
  acknowledgeCommand, beginCommand, getCommandLifecycle, resetCommandLifecycle,
} from '$lib/stores/commands.svelte';
import { getAfLevelControlFeedback, getRfPowerControlFeedback } from '../panel-adapters';

const connected = { state: 'connected' as const, epoch: 7 };
const fresh = (marker = 1) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
const caps = (over: Record<string, unknown> = {}): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 3, receivers: 1, vfoScheme: 'single',
  capabilities: ['af_level', 'tx'], ...over,
} as unknown as Capabilities);
function stateWith(paths: string[], values: Record<string, unknown>, marker: number) {
  return {
    stateContractVersion: 1, providerGeneration: 3, active: 'MAIN',
    main: {}, sub: {}, ...values,
    fieldStatus: Object.fromEntries(paths.map((path) => [path, fresh(marker)])),
  } as unknown as ServerState;
}
const afState = (value: number, marker: number) =>
  stateWith(['main.afLevel'], { main: { afLevel: value } }, marker);
const powerState = (value: number, marker: number) =>
  stateWith(['powerLevel'], { powerLevel: value }, marker);
function emitState(next: ServerState): void {
  h.state = next;
  for (const listener of h.listeners) listener(next);
}
const begin = (id: string, name: string, originalEpoch = 7) =>
  beginCommand({ id, name, params: name === 'set_rf_power' ? { level: 0.5 } : { level: 0.5, receiver: 0 }, originalEpoch });

describe('admitted-target AF/RF command feedback lanes', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetCommandLifecycle();
    h.state = afState(0.2, 1);
    h.caps = caps();
    h.session = connected;
  });
  afterEach(() => {
    resetCommandLifecycle();
    vi.useRealTimers();
  });

  it('fails closed without authority inputs', () => {
    h.state = null; h.caps = null;
    h.session = { state: 'disconnected', epoch: -1 };
    for (const feedback of [getAfLevelControlFeedback(), getRfPowerControlFeedback()]) {
      expect(feedback).toMatchObject({
        phase: 'unavailable', availability: 'unavailable', confirmed: null, target: null,
      });
    }
  });

  it('stays idle for a session the command did not originate in', () => {
    acknowledgeCommand(begin('af-old-epoch', 'set_af_level', 6).id, 6, 6, 128 / 255);
    expect(getAfLevelControlFeedback(connected)).toMatchObject({
      phase: 'idle', target: null, confirmed: 0.2, sessionEpoch: 7,
    });
  });

  it('keeps an old-server AF command idle and honest without an admitted target', () => {
    begin('af-old-server', 'set_af_level');
    acknowledgeCommand('af-old-server', 7, 7);
    expect(getAfLevelControlFeedback(connected)).toMatchObject({
      phase: 'idle', availability: 'available', target: null, requestedTarget: null,
      confirmed: 0.2,
    });
    emitState(afState(128 / 255, 2));
    expect(getCommandLifecycle('af-old-server', 7)?.status).toBe('acknowledged');
    expect(getAfLevelControlFeedback(connected).phase).toBe('idle');
  });

  it('projects awaiting, mismatch-hold, and exact admitted confirmation for AF', () => {
    const command = begin('af-lane', 'set_af_level');
    expect(getAfLevelControlFeedback(connected)).toMatchObject({
      phase: 'idle', target: null, requestedTarget: null, confirmed: 0.2,
      scope: { control: 'af-level', receiver: 0 },
    });
    acknowledgeCommand(command.id, 7, 7, 128 / 255);
    expect(getAfLevelControlFeedback(connected)).toMatchObject({
      phase: 'awaiting-confirmation', target: 128 / 255, requestedTarget: 128 / 255,
    });
    emitState(afState(0.9, 2));
    expect(getCommandLifecycle(command.id, 7)?.status).toBe('acknowledged');
    expect(getAfLevelControlFeedback(connected)).toMatchObject({
      phase: 'awaiting-confirmation', confirmed: 0.9,
    });
    emitState(afState(128 / 255, 3));
    expect(getCommandLifecycle(command.id, 7)?.status).toBe('confirmed');
    expect(getAfLevelControlFeedback(connected)).toMatchObject({
      phase: 'confirmed', confirmed: 128 / 255, target: null,
      requestedTarget: 128 / 255, outcome: { phase: 'confirmed' }, busy: false,
    });
  });

  it('projects the RF-power lane on the exact watts-normalized admitted target', () => {
    h.state = powerState(0.5, 1);
    const command = begin('rf-lane', 'set_rf_power');
    expect(getRfPowerControlFeedback(connected)).toMatchObject({
      phase: 'idle', confirmed: 0.5, scope: { control: 'rf-power', receiver: 0 },
    });
    acknowledgeCommand(command.id, 7, 7, 0.5);
    expect(getRfPowerControlFeedback(connected)).toMatchObject({
      phase: 'awaiting-confirmation', target: 0.5,
    });
    emitState(powerState(0.75, 2));
    expect(getCommandLifecycle(command.id, 7)?.status).toBe('acknowledged');
    emitState(powerState(0.5, 3));
    expect(getCommandLifecycle(command.id, 7)?.status).toBe('confirmed');
    expect(getRfPowerControlFeedback(connected)).toMatchObject({
      phase: 'confirmed', confirmed: 0.5, requestedTarget: 0.5,
    });
  });

  it('keeps each lane unavailable without its own field evidence or capability', () => {
    h.state = powerState(0.5, 1);
    expect(getAfLevelControlFeedback(connected).availability).toBe('unavailable');
    h.state = afState(0.2, 1);
    expect(getRfPowerControlFeedback(connected).availability).toBe('unavailable');
    h.caps = caps({ capabilities: ['tx'] });
    expect(getAfLevelControlFeedback(connected).availability).toBe('unavailable');
    h.caps = caps({ capabilities: ['af_level'] });
    expect(getRfPowerControlFeedback(connected).availability).toBe('unavailable');
  });
});
