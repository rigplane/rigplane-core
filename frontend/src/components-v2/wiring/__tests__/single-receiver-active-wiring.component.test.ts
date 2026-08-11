/**
 * MOR-1421 — single-receiver active-receiver tautology, wired end to end.
 *
 * Companion to `semantic-band-wiring.component.test.ts` (dual-receiver band
 * gating, unchanged) and the adapter-level pins in
 * `radio-view-model-adapter.test.ts` / `scope-adapter.authority.isolated.test.ts`.
 * This file proves, through the REAL command bus / adapter / surfaces, the
 * two consumers the capability-aware `activeReceiverId` fix revives on a
 * single-receiver (IC-7300-shaped) fixture whose `active` field is NEVER
 * observed — exactly the live stand:
 *
 *   (a) band select and typed-frequency entry now DISPATCH.
 *       `SemanticRadioSurfaces`'s own `selectBand`/`enterFrequency` guard
 *       (both hard-gated on a KNOWN active receiver) used to block both
 *       forever on this class of radio, because `active` never resolves via
 *       the old state-only `seen()` gate.
 *   (b) the dual-watch toggle and the active-receiver readout are ABSENT —
 *       the presentation half of MOR-1421 (operator preference: hide, not
 *       show a permanent "MAIN" / permanently-disabled OFF toggle).
 *   (c) a dual-receiver fixture carrying the SAME unobserved-active shape
 *       stays byte-identical to pre-MOR-1421: both intents still blocked,
 *       both elements still rendered.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  listeners: new Set<(next: unknown) => void>(),
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return {
    dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params),
    currentControlSessionEpoch: () => 0,
  };
});
vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: vi.fn(() => h.state),
  getActiveReceiver: vi.fn(() => {
    const state = h.state as ServerState | null;
    return state?.active === 'SUB' ? state.sub ?? null : state?.main ?? null;
  }),
  patchActiveReceiver: vi.fn(), patchRadioState: vi.fn(), patchReceiver: vi.fn(),
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  capabilitiesMatchGeneration: vi.fn(() => true),
  getCapabilities: vi.fn(() => h.caps),
  getControlRange: vi.fn(() => null),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    get rxEnabled() { return true; },
    startRx: vi.fn(), stopRx: vi.fn(), setRxVolume: vi.fn(), setAudioConfig: vi.fn(),
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get audio() { return { muted: false, rxEnabled: true, volume: 42 }; },
    get connectionAudio() { return true; },
    get rxEnabled() { return true; },
    setVolume: vi.fn(), setMuted: vi.fn(), setRxLive: vi.fn(), setRxVolume: vi.fn(),
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime', async () => ({
  runtime: (await import('$lib/runtime/frontend-runtime')).runtime,
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => h.snapshot,
    subscribe: (listener: (next: unknown) => void) => {
      h.listeners.add(listener);
      return () => { h.listeners.delete(listener); };
    },
    start: vi.fn(), setIntent: vi.fn(), release: vi.fn(), resetFault: vi.fn(),
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: 'MIC' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { sendCommand } from '$lib/transport/ws-client';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };

/** The BAND PLAN this file's fixtures declare — `freqRanges` is the band
 *  group's whole evidence gate, so it must be real for the dispatch pins to
 *  be non-vacuous. */
const BAND_PLAN = [{
  start: 30000, end: 60000000,
  bands: [
    { name: '20m', start: 14000000, end: 14350000, default: 14195000, bsrCode: 5 },
    // No BSR — exercises the `set_freq` fallback path.
    { name: 'MW', start: 520000, end: 1710000, default: 1000000 },
  ],
}];

/**
 * The live IC-7300 stand shape (probed 2026-08-10): `active` is observed:
 * false/availability:'missing' FOREVER, `main.activeSlot` is equally
 * unobserved, `main.freqHz`/`main.mode`/`main.filter` are fresh. `active`'s
 * RAW value is still `'MAIN'` (the backend's own literal default) — only its
 * field-status entry is absent.
 */
function singleRxLiveState(over: Partial<ServerState> = {}): ServerState {
  const paths = ['split', 'dualWatch', 'txTarget', 'main.freqHz', 'main.mode', 'main.filter'];
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14250000 },
    main: {
      freqHz: 14250000, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0,
    },
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const singleRxCaps = (freqRanges: unknown[]): Capabilities => ({
  model: 'IC-7300', scope: false, audio: true, tx: true,
  capabilities: ['audio', 'tx'],
  receivers: 1, vfoScheme: 'ab', vfoReadback: 'selected_unselected',
  freqRanges, modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

/** A dual-receiver fixture carrying the SAME "active unobserved" shape, to
 *  prove the fix does not touch multi-receiver radios (byte-identical). */
function dualRxLiveState(over: Partial<ServerState> = {}): ServerState {
  const paths = ['split', 'dualWatch', 'txTarget', 'main.freqHz', 'main.mode', 'main.filter'];
  const slot = (hz: number) => ({ freqHz: hz, mode: 'USB', filterNum: 1, dataMode: 0 });
  const receiver = (hz: number) => ({
    freqHz: hz, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
    nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0,
    vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A',
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(7100000),
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const dualRxCaps = (freqRanges: unknown[]): Capabilities => ({
  model: 'IC-7610', scope: false, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'dual_rx'],
  receivers: 2, vfoScheme: 'main_sub', freqRanges, modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const btn = (id: string) => q<HTMLButtonElement>(`[data-testid="band-${id}"]`);
const setFreqCalls = () => vi.mocked(sendCommand).mock.calls.filter(([n]) => n === 'set_freq');

function typeFrequency(value: string): void {
  const input = q<HTMLInputElement>('[data-testid="band-entry-input"]')!;
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  flushSync();
}

beforeEach(() => {
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  vi.mocked(sendCommand).mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

describe('single-receiver band select / frequency entry dispatch though active was never observed (MOR-1421)', () => {
  beforeEach(() => {
    h.state = singleRxLiveState();
    h.caps = singleRxCaps(BAND_PLAN);
  });

  it('sends set_band for a band WITH a stacking register', () => {
    render();
    expect(q('[data-testid="band-surface"]')).not.toBeNull();
    btn('choice-20m')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_band', { band: 5 });
  });

  it('sends set_freq {receiver: 0} for a no-BSR band pick — MAIN is the only receiver', () => {
    render();
    btn('choice-MW')!.click();
    flushSync();
    expect(setFreqCalls()).toEqual([['set_freq', { freq: 1000000, receiver: 0 }]]);
  });

  it('sends set_freq for a typed frequency', () => {
    render();
    typeFrequency('14100000');
    btn('entry-set')!.click();
    flushSync();
    expect(setFreqCalls()).toEqual([['set_freq', { freq: 14100000, receiver: 0 }]]);
  });
});

describe('single-receiver VfoSurface hides dual-receiver chrome (MOR-1421 presentation)', () => {
  beforeEach(() => {
    h.state = singleRxLiveState();
    h.caps = singleRxCaps(BAND_PLAN);
  });

  it('renders no active-receiver readout', () => {
    render();
    expect(q('[data-testid="vfo-active-receiver"]')).toBeNull();
  });

  it('renders no dual-watch toggle', () => {
    render();
    expect(q('[data-vfo-dual-watch]')).toBeNull();
  });

  it('keeps the split toggle — split is not a dual-receiver-only control', () => {
    render();
    expect(q('[data-vfo-split]')).not.toBeNull();
  });

  it('marks the selected VFO tile is-active', () => {
    render();
    const tile = q<HTMLElement>('[data-vfo-tile][data-vfo-active="true"]');
    expect(tile).not.toBeNull();
    expect(tile!.classList.contains('is-active')).toBe(true);
  });
});

describe('dual-receiver radio stays byte-identical under the same unobserved-active shape (MOR-1421 guard)', () => {
  function unobservedActiveDualState(): ServerState {
    const state = dualRxLiveState() as unknown as Record<string, unknown>;
    const status = { ...(state.fieldStatus as Record<string, unknown>) };
    delete status.active;
    return { ...state, fieldStatus: status } as unknown as ServerState;
  }

  beforeEach(() => {
    h.state = unobservedActiveDualState();
    h.caps = dualRxCaps(BAND_PLAN);
  });

  it('still blocks band select — no known active receiver to scope it to', () => {
    render();
    expect(btn('choice-20m')!.disabled).toBe(true);
    btn('choice-20m')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('still blocks typed frequency entry', () => {
    render();
    btn('entry-set')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('still renders the active-receiver readout and the dual-watch toggle', () => {
    render();
    expect(q('[data-testid="vfo-active-receiver"]')).not.toBeNull();
    expect(q('[data-vfo-dual-watch]')).not.toBeNull();
  });
});
