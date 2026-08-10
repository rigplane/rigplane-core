import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  calls: [] as Array<{ name: string; params: Record<string, unknown>; id: string; options: { optimistic: boolean } }>,
  throwOnCall: 0,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: () => h.state,
  getActiveReceiver: () => h.state?.active === 'SUB' ? h.state.sub : h.state?.main,
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: () => h.caps,
  capabilitiesMatchGeneration: (generation: number) => generation === h.caps?.providerGeneration,
  getControlRange: () => null,
}));
vi.mock('$lib/state/field-status', () => ({
  getFieldStatus: (state: ServerState | null, path: string) => state?.fieldStatus?.[path],
  isFieldAvailable: (state: ServerState | null, path: string) => state?.fieldStatus?.[path]?.availability === 'available',
}));
vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: () => ({ state: 'connected', epoch: 7 }),
  onCommandDelivery: () => () => undefined,
  onControlSessionTransition: () => () => undefined,
  sendCommand: (name: string, params: Record<string, unknown>, id: string, options: { optimistic: boolean }) => {
    if (h.throwOnCall === h.calls.length + 1) throw new Error('injected dispatcher failure');
    h.calls.push({ name, params, id, options });
    return true;
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({ runtime: {} }));
vi.mock('$lib/audio/audio-manager', () => ({ audioManager: {} }));
vi.mock('$lib/stores/tuning.svelte', () => ({ getTuningStep: () => 1_000 }));

import { makeMemoryHandlers } from '../panel-commands';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' } as const;

function state(scheme: Capabilities['vfoScheme'] = 'main_sub'): ServerState {
  const receiver = { freqHz: 14_074_000, mode: 'USB', activeSlot: 'A', vfoA: { freqHz: 14_074_000, mode: 'USB' } };
  const fieldStatus: Record<string, typeof fresh> = {
    active: fresh, 'main.activeSlot': fresh, 'main.vfoA.freqHz': fresh, 'main.vfoA.mode': fresh,
    'main.freqHz': fresh, 'main.mode': fresh, 'sub.activeSlot': fresh, 'sub.vfoA.freqHz': fresh, 'sub.vfoA.mode': fresh,
    'sub.freqHz': fresh, 'sub.mode': fresh,
  };
  return { stateContractVersion: 1, providerGeneration: 7, active: 'MAIN', main: receiver, sub: receiver, fieldStatus } as unknown as ServerState;
}

function caps(scheme: Capabilities['vfoScheme']): Capabilities {
  const receivers = scheme === 'single' || scheme === 'ab' ? 1 : 2;
  return { stateContractVersion: 1, providerGeneration: 7, vfoScheme: scheme, receivers,
    capabilities: receivers === 2 ? ['dual_rx'] : [], modes: ['USB'] } as unknown as Capabilities;
}

describe('MOR-1409 A05a memory command authority', () => {
  beforeEach(() => { h.calls.length = 0; h.throwOnCall = 0; h.state = state(); h.caps = caps('main_sub'); });
  afterEach(() => resetCommandLifecycle());

  it('uses strict observed state and starts ordered non-optimistic intents with distinct lifecycle ids', () => {
    const handlers = makeMemoryHandlers();
    expect(handlers.onRecall(1)).toBe(true);
    expect(handlers.onStore(2, 14_074_000, 'USB')).toBe(true);
    expect(handlers.onClear(99)).toBe(true);
    expect(h.calls.map(({ name, params }) => ({ name, params }))).toEqual([
      { name: 'set_memory_mode', params: { channel: 1 } }, { name: 'memory_to_vfo', params: { channel: 1 } },
      { name: 'set_memory_mode', params: { channel: 2 } }, { name: 'memory_write', params: {} },
      { name: 'memory_clear', params: { channel: 99 } },
    ]);
    expect(new Set(h.calls.map((call) => call.id)).size).toBe(h.calls.length);
    expect(h.calls.every((call) => call.options.optimistic === false)).toBe(true);
  });

  it('accepts real relative and unslotted topology identities without inventing A or B', () => {
    for (const scheme of ['ab', 'single', 'ab_shared'] as const) {
      h.state = state(scheme);
      h.caps = caps(scheme);
      if (scheme === 'ab') {
        h.caps = { ...h.caps, vfoReadback: 'selected_unselected' };
        delete (h.state.fieldStatus as Record<string, unknown>)['main.activeSlot'];
      }
      expect(makeMemoryHandlers().onRecall(3)).toBe(true);
    }
  });

  it('rejects all invalid channels and stale/default/unavailable authority before dispatch', () => {
    const handlers = makeMemoryHandlers();
    for (const invalid of [0, 100, NaN, Infinity, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
      expect(handlers.onRecall(invalid)).toBe(false);
    }
    (h.state!.fieldStatus as Record<string, unknown>)['main.vfoA.freqHz'] = { ...fresh, freshness: 'stale' };
    expect(handlers.onStore(1, 14_074_000, 'USB')).toBe(false);
    h.state = state();
    (h.state.main.vfoA as { freqHz: number }).freqHz = 0;
    expect(handlers.onStore(1, 0, 'USB')).toBe(false);
    expect(h.calls).toHaveLength(0);
  });

  it('rejects each supplied store value that no longer equals the observed snapshot', () => {
    const handlers = makeMemoryHandlers();
    expect(handlers.onStore(1, 7_100_000, 'USB')).toBe(false);
    expect(handlers.onStore(1, 14_074_000, 'LSB')).toBe(false);
    expect(h.calls).toHaveLength(0);
  });

  it('requires a non-empty, observed, fresh, and available mode leaf before store dispatch', () => {
    const handlers = makeMemoryHandlers();
    (h.state!.main.vfoA as { mode: string }).mode = '';
    expect(handlers.onStore(1, 14_074_000, '')).toBe(false);

    for (const modeStatus of [
      undefined,
      { ...fresh, observed: false },
      { ...fresh, freshness: 'stale' },
      { ...fresh, availability: 'unavailable' },
    ]) {
      h.state = state();
      (h.state!.fieldStatus as Record<string, unknown>)['main.vfoA.mode'] = modeStatus;
      expect(handlers.onStore(1, 14_074_000, 'USB')).toBe(false);
    }
    expect(h.calls).toHaveLength(0);
  });

  it('rejects absent active identity, generation drift, and impossible physical topology before dispatch', () => {
    const handlers = makeMemoryHandlers();
    h.state = { ...h.state!, active: undefined } as unknown as ServerState;
    expect(handlers.onRecall(1)).toBe(false);
    h.state = state();
    h.caps = { ...caps('main_sub'), providerGeneration: 8 };
    expect(handlers.onRecall(1)).toBe(false);
    h.caps = { ...caps('main_sub'), capabilities: [] };
    h.state = { ...state(), active: 'SUB' };
    expect(handlers.onRecall(1)).toBe(false);
    h.state = state();
    h.caps = caps('main_sub');
    delete (h.state.fieldStatus as Record<string, unknown>)['main.activeSlot'];
    expect(handlers.onRecall(1)).toBe(false);
    expect(h.calls).toHaveLength(0);
  });

  it('propagates member exceptions without false success or a rollback transaction', () => {
    h.throwOnCall = 1;
    expect(() => makeMemoryHandlers().onRecall(1)).toThrow('injected dispatcher failure');
    expect(h.calls).toHaveLength(0);

    h.throwOnCall = 2;
    expect(() => makeMemoryHandlers().onRecall(1)).toThrow('injected dispatcher failure');
    expect(h.calls.map((call) => call.name)).toEqual(['set_memory_mode']);
  });

  it('keeps memory ownership out of raw transport, optimistic state, and Store writers', () => {
    const source = readFileSync('src/lib/runtime/commands/panel-commands.ts', 'utf8');
    const memory = source.slice(source.indexOf('/* ── Memory Handlers'));
    expect(memory).not.toMatch(/\b(?:sendCommand|patchRadioState|patchReceiver|patchActiveReceiver|optimistic)\b/);
  });
});
