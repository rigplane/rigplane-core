/**
 * MOR-1279 — the semantic RX-audio surface wired into `SemanticRadioSurfaces`.
 *
 * `semantic/__tests__/RxAudioSurface.test.ts` proves what the surface does
 * with a view model. This file proves the things only the composed tree can
 * prove, and it deliberately uses the REAL command bus, the REAL adapter and
 * the REAL surface — only the audio/transport/runtime SEAMS are spied:
 *
 *   (a) SAFETY: mounting this tree opens NO audio session and sends NO
 *       command. "A view opened the transport on mount" is the MOR-972 P0
 *       shape; audio lifetime is App-owned (MOR-1058). The seam spies are
 *       snapshotted at MODULE LOAD too, so an import-time side effect
 *       anywhere in the transitive closure is caught as well (the MOR-1274
 *       F1 lesson).
 *   (b) The AF unit crosses the wiring exactly ONCE: an `RxAudioSnapshot`
 *       volume of 42 must render as 0.42, and moving the slider back to 0.42
 *       must reach the runtime as 42 — through the real
 *       `makeRxAudioHandlers`. Any second divide/multiply breaks one half.
 *   (c) The routing facts stay `unknown`: this layer must not restore or
 *       invent the browser prefs (MOR-1274 carry-forward 2).
 *   (d) A MOD-input `mismatch` keeps a one-click remedy that fires the SAME
 *       command `ModInputTxWarning`'s "Set LAN" does — and the warning itself
 *       is neither moved out of the rx-tx zone nor duplicated.
 *   (e) The default path stays byte-identical for a radio with no audio chain.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine —
 * no `vite.config.ts` edit was needed.
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
  audio: { muted: false, rxEnabled: true, volume: 42 },
  audioConnected: true,
  rxEnabled: true,
  guardVisible: false,
  listeners: new Set<(next: unknown) => void>(),
  setVolume: vi.fn(),
  setMuted: vi.fn(),
  setRxLive: vi.fn(),
  setRxVolume: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});
vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: vi.fn(() => h.state),
  getActiveReceiver: vi.fn(() => {
    const state = h.state as ServerState | null;
    return state?.active === 'SUB' ? state.sub ?? null : state?.main ?? null;
  }),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    get rxEnabled() { return h.rxEnabled; },
    startRx: vi.fn(), stopRx: vi.fn(), setRxVolume: vi.fn(), setAudioConfig: vi.fn(),
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get audio() { return h.audio; },
    get connectionAudio() { return h.audioConnected; },
    get rxEnabled() { return h.rxEnabled; },
    setVolume: h.setVolume, setMuted: h.setMuted,
    setRxLive: h.setRxLive, setRxVolume: h.setRxVolume,
    // MOR-1312 slice 12B: the wiring now also hands the adapter a
    // scope-display snapshot (the FIFTH argument). This fixture declares no
    // scope capability, so this stays on its pre-1312 path regardless.
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get radioPowerOn() { return null; },
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
  deriveModInputTxGuardProps: () => ({ visible: h.guardVisible, sourceLabel: 'MIC' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { audioManager } from '$lib/audio/audio-manager';
import { sendCommand } from '$lib/transport/ws-client';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { makeAudioRoutingHandlers, makeModeHandlers, makeRxAudioHandlers } from '../command-bus';
import { desktopV2Layout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../../../presentation/workspace/resolution';

/**
 * (a), half one. Read BEFORE any `mockClear()` — the only pin that can see a
 * module-load side effect anywhere in the transitive import closure of the
 * wiring, the surface, the adapter and the real command bus.
 */
const LOAD_TIME_CALLS = [
  audioManager.startRx, audioManager.stopRx, audioManager.setRxVolume,
  audioManager.setAudioConfig, sendCommand, h.setRxLive, h.setVolume, h.setMuted,
].map((spy) => vi.mocked(spy).mock.calls.length);

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

function liveState(over: Partial<ServerState> = {}): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget', 'dataOffModInput'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`, `${rx}.afLevel`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1, afLevel: 0.31,
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false, dataOffModInput: 5,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (tags: readonly string[]): Capabilities => ({
  model: 'fixture', scope: false, audio: tags.includes('audio'), tx: true,
  capabilities: tags, audioTxRequiredModInputSource: 5,
  receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

const AUDIO_TAGS = ['audio', 'tx', 'dual_rx', 'af_level', 'mod_input_routing'] as const;
/** A radio with NO audio chain at all: no live audio, no AF control, no
 *  dual-RX routing, no MOD-input routing ⇒ the adapter emits no group. */
const SILENT_TAGS = ['tx'] as const;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(props: { strips?: 'single' | 'dual' } = {}, plan?: SurfacePlan): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = plan === undefined
    ? undefined
    : new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]);
  component = mount(SemanticRadioSurfaces, { target, props, context });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const el = (id: string) => q<HTMLElement>(`[data-testid="rx-audio-${id}"]`);
const text = (id: string) => el(id)?.textContent?.trim();

const SEAM_SPIES = () => [
  audioManager.startRx, audioManager.stopRx, audioManager.setRxVolume,
  audioManager.setAudioConfig, sendCommand, h.setRxLive, h.setVolume, h.setMuted,
];

beforeEach(() => {
  h.state = liveState();
  h.caps = liveCaps(AUDIO_TAGS);
  h.snapshot = { ...IDLE };
  h.audio = { muted: false, rxEnabled: true, volume: 42 };
  h.audioConnected = true;
  h.rxEnabled = true;
  h.guardVisible = false;
  h.listeners.clear();
  for (const spy of SEAM_SPIES()) vi.mocked(spy).mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

/* ── (a) mounting opens nothing ────────────────────────────────── */

describe('the composed tree owns no audio lifetime', () => {
  // MUTATION KILLED: any module in the closure calling the audio path at
  // import time — the shape MOR-1274's F1 round added this pin for.
  it('imports the wiring, the surface and the command bus with zero seam calls', () => {
    expect(LOAD_TIME_CALLS).toEqual([0, 0, 0, 0, 0, 0, 0, 0]);
  });

  // MUTATION KILLED: `onMount(() => runtime.setRxLive(true))` in the surface
  // or the wiring — "the RX-audio panel started the stream just by existing".
  it('mounts, renders the surface and still starts no stream and sends no command', () => {
    render();
    expect(el('surface')).not.toBeNull();
    for (const spy of SEAM_SPIES()) expect(spy).not.toHaveBeenCalled();
  });

  it('unmounts without touching the audio path either', () => {
    render();
    unmount(component!);
    component = null;
    for (const spy of SEAM_SPIES()) expect(spy).not.toHaveBeenCalled();
  });
});

/* ── (b) the AF unit crosses the wiring exactly once ───────────── */

describe('AF level: 0..100 becomes 0..1 exactly once, at the adapter seam', () => {
  // MUTATION KILLED: a second `/ 100` (renders 0.0042) or a missing one
  // (renders 42, clamped by the range to 1).
  it('renders a browser volume of 42 as an AF level of 0.42', () => {
    render();
    expect(q<HTMLInputElement>('[data-testid="rx-audio-af"] input')!.valueAsNumber)
      .toBeCloseTo(0.42, 10);
    expect(text('af-value')).toBe('0.42');
  });

  it.each([0, 7, 50, 100])('renders a browser volume of %i on the 0..1 scale', (volume) => {
    h.audio = { muted: false, rxEnabled: true, volume };
    render();
    expect(q<HTMLInputElement>('[data-testid="rx-audio-af"] input')!.valueAsNumber)
      .toBeCloseTo(volume / 100, 10);
  });

  // MUTATION KILLED: a rescale on the way OUT. Driven through the REAL
  // `makeRxAudioHandlers` the wiring composes, so the round trip 42 → 0.42 →
  // 42 is proven end to end rather than asserted about a stub.
  it('returns the same level to the runtime as a 0..100 volume, through the real bus', () => {
    render();
    const input = q<HTMLInputElement>('[data-testid="rx-audio-af"] input')!;
    input.value = '0.42';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.setRxVolume).toHaveBeenCalledExactlyOnceWith(0.42);
    expect(h.setVolume).toHaveBeenCalledExactlyOnceWith(42);
  });

  // The command bus this wiring composes IS the shipped one — a fork would
  // let the two vocabularies drift apart silently.
  it('composes the shipped RX-audio, routing and mode command factories', () => {
    for (const [factory, names] of [
      [makeRxAudioHandlers, ['onMonitorModeChange', 'onAfLevelChange']],
      [makeAudioRoutingHandlers, ['onFocusChange', 'onSplitStereoChange']],
      [makeModeHandlers, ['onModInputChange']],
    ] as const) {
      const handlers = factory() as Record<string, unknown>;
      for (const name of names) expect(typeof handlers[name]).toBe('function');
    }
  });
});

/* ── monitor mode + routing intents reach the real bus ─────────── */

describe('the surface intents reach the shipped command vocabulary', () => {
  it('routes a monitor-mode pick to the runtime RX-audio authority', () => {
    h.audio = { muted: false, rxEnabled: false, volume: 42 };
    render();
    el('monitor-live')!.click();
    flushSync();
    expect(h.setRxLive).toHaveBeenCalledExactlyOnceWith(true);
    expect(h.setMuted).toHaveBeenCalledWith(false);
  });

  it('routes a routing-focus pick to the shipped audio-config command', () => {
    render();
    el('focus-sub')!.click();
    flushSync();
    expect(audioManager.setAudioConfig).toHaveBeenCalledExactlyOnceWith({ focus: 'sub' });
  });

  it('routes a stereo-split pick to the shipped audio-config command', () => {
    render();
    el('split-on')!.click();
    flushSync();
    expect(audioManager.setAudioConfig).toHaveBeenCalledExactlyOnceWith({ split_stereo: true });
  });
});

/* ── (c) routing prefs are not restored or invented here ───────── */

describe('routing prefs stay unowned by this layer (MOR-1274 carry-forward 2)', () => {
  // MUTATION KILLED: seeding the snapshot from
  // `audioManager.getAudioConfig()` or `restoreFromStorage()` — both report
  // (or install) the RxPlayer's 'both'/false construction defaults as though
  // they had been observed, which is exactly the fabrication slice 3A removed.
  it('reports focus and split as unknown until someone else restores them', () => {
    render();
    expect(text('focus-value')).toBe('—');
    expect(text('split-value')).toBe('—');
    expect(el('focus')!.dataset.observed).toBe('false');
    expect(el('split')!.dataset.observed).toBe('false');
  });

  it('checks none of the focus or split choices while they are unrestored', () => {
    render();
    for (const id of ['focus-main', 'focus-sub', 'focus-both', 'split-on', 'split-off']) {
      expect(el(id)!.getAttribute('aria-checked')).toBe('false');
    }
  });

  // MUTATION KILLED: reading the browser prefs from localStorage in the
  // wiring (`restoreFromStorage` CALLS `setAudioConfig` — a transport touch).
  it('reads no routing prefs at mount, so nothing is pushed to the audio graph', () => {
    render();
    expect(audioManager.setAudioConfig).not.toHaveBeenCalled();
  });
});

/* ── (d) the MOD-input remedy, and the untouched warning ───────── */

describe('a MOD-input mismatch keeps exactly one one-click remedy', () => {
  const mismatched = () => { h.state = liveState({ dataOffModInput: 0 } as Partial<ServerState>); };

  it('states the mismatch the "web voice TX = noise" failure produces', () => {
    mismatched();
    render();
    expect(el('mod-input')!.dataset.readiness).toBe('mismatch');
    expect(text('mod-source')).toBe('MOD: MIC');
  });

  // MUTATION KILLED: a remedy button that fires a different command, or none.
  // `set_data_off_mod_input` with source 5 (LAN) is exactly what
  // `ModInputTxWarning`'s "Set LAN" fires via the same `makeModeHandlers`.
  it('fires the same LAN command the shipped warning fires', () => {
    mismatched();
    render();
    el('mod-set-lan')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_data_off_mod_input', { source: 5 });
  });

  it('offers no remedy while the MOD input is already LAN', () => {
    render();
    expect(el('mod-input')!.dataset.readiness).toBe('ready');
    expect(el('mod-set-lan')).toBeNull();
  });

  // MUTATION KILLED: moving `ModInputTxWarning` into this surface, or
  // rendering a second copy of it. MOR-1258 put it in the rx-tx zone and it
  // must stay there — this surface is a standing readiness readout, not a
  // second preflight banner.
  it('leaves ModInputTxWarning where MOR-1258 put it, exactly once', () => {
    h.guardVisible = true;
    mismatched();
    render({ strips: 'dual' });
    const warnings = target.querySelectorAll('[data-testid="mod-input-tx-warning"]');
    expect(warnings.length).toBe(1);
    expect(warnings[0].closest('[data-zone-id="rx-tx"]')).not.toBeNull();
    expect(warnings[0].closest('[data-testid="rx-audio-surface"]')).toBeNull();
  });
});

/* ── (e) the structural gate and the default path ──────────────── */

describe('the surface mounts only when the view model carries the group', () => {
  // MUTATION KILLED: mounting `RxAudioSurface` unconditionally — a radio with
  // no audio chain would gain an empty panel it never asked for.
  it('renders no rx-audio surface for a radio with no audio chain', () => {
    h.caps = liveCaps(SILENT_TAGS);
    render();
    expect(el('surface')).toBeNull();
  });

  it('renders it bare in the single composition, outside every zone', () => {
    render();
    const surface = el('surface')!;
    expect(surface).not.toBeNull();
    expect(surface.closest('[data-zone-id]')).toBeNull();
  });

  /**
   * MUTATION KILLED: mounting this surface bare in the cockpit. It is the
   * first semantic surface carrying interactive controls that no manifest
   * declares a zone for, and MOR-1069's cockpit rule is that every focusable
   * control lives inside a declared zone with rx-tx last in the tab order
   * (`skins/dual-receiver-cockpit/__tests__/DualReceiverCockpit.component.test.ts`
   * enforces it). Mounted bare it breaks both clauses; folded into the rx-tx
   * zone it would put an AF slider between the operator and the unkey button.
   * It waits for a declared zone — which this slice made possible.
   */
  it('renders NO rx-audio surface in the dual composition, zoned or unzoned', () => {
    render({ strips: 'dual' });
    expect(el('surface')).toBeNull();
    expect(target.innerHTML).not.toContain('rx-audio');
  });

  it('leaves the cockpit with no focusable control outside a declared zone', () => {
    render({ strips: 'dual' });
    const outside = [...target.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')]
      .filter((node) => node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });

  // MUTATION KILLED: the surface taking a TX-authority snapshot or growing a
  // key path. Nothing here is TX truth.
  it('never changes with the App TX authority or the raw transmit bit', () => {
    render();
    const before = el('surface')!.outerHTML;
    h.snapshot = { ...IDLE, phase: 'transmitting', radioTx: 'on', mayOwnKey: true };
    for (const listener of h.listeners) listener(h.snapshot);
    h.state = liveState({ ptt: true } as Partial<ServerState>);
    flushSync();
    expect(el('surface')!.outerHTML).toBe(before);
  });
});

describe('desktop-v2 declares a real rx-audio zone; the cockpit does not (MOR-1368, S9, F1)', () => {
  function planFor(layout: typeof desktopV2Layout, fields: Record<string, unknown>): SurfacePlan {
    return resolveSurfacePlan(layout, readWorkspace({ version: 1, ...fields }).workspace);
  }

  it('binds the rx-audio zone id against desktop-v2\'s real plan', () => {
    h.caps = liveCaps(AUDIO_TAGS);
    render({ strips: 'single' }, planFor(desktopV2Layout, {}));
    expect(q('[data-testid="rx-audio-surface"]')!.closest('[data-zone-id="rx-audio"]')).not.toBeNull();
  });
});
