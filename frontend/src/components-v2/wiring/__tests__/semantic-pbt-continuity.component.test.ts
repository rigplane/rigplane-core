import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  session: { state: 'connected', epoch: 1 }, listeners: new Set<(next: { state: string; epoch: number }) => void>(),
  commands: vi.fn(), tx: vi.fn(),
}));
vi.mock('$lib/transport/ws-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/transport/ws-client')>();
  return {
    ...actual, sendCommand: h.commands, getControlSession: () => h.session,
    onControlSessionTransition: (listener: (next: { state: string; epoch: number }) => void) => { h.listeners.add(listener); return () => h.listeners.delete(listener); },
    onCommandDelivery: () => () => undefined,
  };
});
vi.mock('$lib/runtime/tx-controller/app-host', () => ({ getAppTxController: () => ({ snapshot: () => ({ phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null }), subscribe: () => () => undefined, start: h.tx, release: h.tx, setIntent: vi.fn(), resetFault: vi.fn() }) }));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({ deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }), getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }) }));

import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

const fresh = (marker: number) => ({ storePath: 'x', observed: true, freshness: 'fresh' as const, availability: 'available' as const, lastObservedMonotonic: marker });
const caps = (generation = 7): Capabilities => ({ model: 'fixture', scope: false, audio: false, tx: false, capabilities: ['pbt', 'dual_rx'], receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: ['USB'], filters: [1], controls: { pbt_inner: { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200 } }, audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] }, webrtc: { available: false, enabled: false }, txBands: [], scopeSource: null, audioFftAvailable: false, stateContractVersion: 1, providerGeneration: generation } as unknown as Capabilities);
const state = (inner: number | null, outer: number | null, marker = 1, generation = 7, active: 'MAIN' | 'SUB' = 'MAIN', freshness: 'fresh' | 'stale' = 'fresh'): ServerState => {
  const receiver = (pbtInner: number | null, pbtOuter: number | null) => ({ freqHz: 14250000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0, att: 0, preamp: 0, nb: false, nr: false, afLevel: 0, rfGain: 0, squelch: 0, activeSlot: 'A', vfoA: { freqHz: 14250000, mode: 'USB', filterNum: 1 }, vfoB: { freqHz: 14300000, mode: 'USB', filterNum: 1 }, pbtInner, pbtOuter });
  const field = (name: string) => inner === null && name.endsWith('pbtInner') ? { ...fresh(marker), freshness } : outer === null && name.endsWith('pbtOuter') ? { ...fresh(marker), freshness } : { ...fresh(marker), freshness };
  return { revision: marker, stateRevision: marker, freshnessRevision: marker, observationSeq: marker, updatedAt: '2026-08-15T00:00:00Z', stateContractVersion: 1, providerGeneration: generation, active, split: false, dualWatch: false, ptt: false, tunerStatus: 0, connection: { rigConnected: true, radioReady: true, controlConnected: true }, txTarget: { status: 'unknown', reason: 'fixture' }, main: receiver(inner, outer), sub: receiver(inner, outer), fieldStatus: Object.fromEntries(['active', 'main.freqHz', 'main.mode', 'main.filter', 'main.activeSlot', 'sub.freqHz', 'sub.mode', 'sub.filter', 'sub.activeSlot', 'main.pbtInner', 'main.pbtOuter', 'sub.pbtInner', 'sub.pbtOuter'].map((name) => [name, field(name)])) } as unknown as ServerState;
};
let target: HTMLDivElement; let component: ReturnType<typeof mount> | null = null;
const render = () => { target = document.createElement('div'); document.body.append(target); component = mount(SemanticRadioSurfaces, { target }); flushSync(); };
const value = (field: 'pbtInner' | 'pbtOuter') => target.querySelector<HTMLInputElement>(`[data-testid="filter-${field}"] input`)?.value ?? null;
const transition = (state: string, epoch: number, flush = true) => { h.session = { state, epoch }; for (const listener of h.listeners) listener(h.session); if (flush) flushSync(); };
const accept = (next: ServerState) => expect(setRadioState(next)).toBe(true);

beforeEach(() => { h.session = { state: 'connected', epoch: 1 }; h.listeners.clear(); h.commands.mockClear(); h.tx.mockClear(); resetRadioState(); clearCapabilities(); expect(setCapabilities(caps())).toBe(true); accept(state(100, -200)); });
afterEach(() => { component && unmount(component); component = null; expect(h.listeners.size).toBe(0); document.body.innerHTML = ''; resetRadioState(); clearCapabilities(); });

describe('mounted PBT continuity is fenced by the live control session (MOR-1706)', () => {
  it('retains independent fresh values through transient loss, then synchronously clears coalesced same-epoch reconnects', () => {
    render(); const initialInner = value('pbtInner'), initialOuter = value('pbtOuter'); expect(initialInner).not.toBeNull(); expect(initialOuter).not.toBeNull(); expect(h.commands).not.toHaveBeenCalled();
    const staleInput = target.querySelector<HTMLInputElement>('[data-testid="filter-pbtInner"] input'); expect(staleInput).not.toBeNull();
    accept(state(null, -200, 2)); flushSync(); expect(value('pbtInner')).toBe(initialInner); expect(value('pbtOuter')).toBe(initialOuter);
    expect(h.listeners.size).toBe(1); transition('disconnected', 1, false); transition('connected', 1, false); flushSync(); expect(value('pbtInner')).toBeNull(); expect(value('pbtOuter')).toBeNull();
    staleInput!.value = '500'; staleInput!.dispatchEvent(new Event('input', { bubbles: true })); flushSync(); expect(h.commands).not.toHaveBeenCalled(); expect(h.tx).not.toHaveBeenCalled();
    transition('connected', 1); expect(value('pbtInner')).toBeNull(); accept(state(300, -400, 3)); flushSync(); expect(value('pbtInner')).not.toBeNull();
    transition('connected', 2); expect(value('pbtInner')).toBeNull(); accept(state(500, -600, 4)); flushSync(); expect(value('pbtOuter')).not.toBeNull();
    accept(state(null, null, 5, 7, 'SUB', 'stale')); flushSync(); expect(value('pbtInner')).toBeNull();
    accept(state(700, -800, 6, 7, 'SUB')); flushSync(); expect(value('pbtInner')).not.toBeNull(); expect(h.commands).not.toHaveBeenCalled(); expect(h.tx).not.toHaveBeenCalled();
  });

  it('clears explicitly unsupported fields and cannot revive delayed old generation evidence', () => {
    render(); const initial = value('pbtInner'); expect(initial).not.toBeNull(); accept(state(null, null, 2)); flushSync(); expect(value('pbtInner')).toBe(initial);
    expect(setCapabilities({ ...caps(), capabilities: ['dual_rx'] } as Capabilities)).toBe(true); accept(state(null, null, 3)); flushSync(); expect(value('pbtInner')).toBeNull();
    expect(setCapabilities(caps(8))).toBe(true); accept(state(900, -900, 1, 8)); flushSync(); const current = value('pbtInner'); expect(current).not.toBeNull();
    accept(state(null, null, 2, 8)); flushSync(); expect(value('pbtInner')).toBe(current);
  });
});
