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
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';


const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  authoritySubscribers: new Set<(next: {
    state: unknown; caps: unknown; session: { state: 'connected'; epoch: 1 };
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void>(),
  txController: null as ManagedAppTxController | null,
  audio: { muted: false, rxEnabled: true, volume: 42 },
  audioConnected: true,
  rxEnabled: true,
  guardVisible: false,
  setVolume: vi.fn(),
  setMuted: vi.fn(),
  setRxLive: vi.fn(),
  setRxVolume: vi.fn(),
  selectedFiniteAppearance: undefined as unknown,
}));

/** MOR-2425 RX-B/RX-C — same recipe `semantic-rf-front-end-wiring
 *  .component.test.ts` uses to feed a real external-renderer appearance
 *  through `component-kits/activation`'s selection seam. */
vi.mock('../../../component-kits/activation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../component-kits/activation')>();
  return { ...actual, getSelectedFiniteControlAppearance: () => h.selectedFiniteAppearance };
});

/** MOR-2425 persistence witness anchor: `RxAudioInstrumentHost.svelte`
 *  creates its AF `createContinuousScalar` binding ONCE, at component-script
 *  top level — capturing it here (same recipe `semantic-rf-front-end-wiring
 *  .component.test.ts`'s own `createContinuousPair` capture uses) proves
 *  whether the host itself survives a real Standard->SDR skin switch,
 *  independent of whether any one finite seat's own external lease does.
 *  `createContinuousScalar` is shared by every scalar-backed host in the
 *  composed tree (DSP, TxAux, CW keyer...), so a bare capture would count
 *  ALL of them — filtered here to RX-audio's own by its `input().ownerKey`,
 *  which `RxAudioInstrumentHost.svelte`'s own `key()` always stamps with the
 *  literal `'rx-af'` (`'rx-af:inactive'` or the `["rx-af", ...]` JSON form). */
const rxAudioScalar = vi.hoisted(() => ({ bindings: [] as unknown[] }));
vi.mock('../../../primitives/scalar/continuous-scalar.svelte', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../primitives/scalar/continuous-scalar.svelte')>();
  return {
    ...actual,
    createContinuousScalar: (...args: Parameters<typeof actual.createContinuousScalar>) => {
      const binding = actual.createContinuousScalar(...args);
      const [input] = args;
      const ownerKey = typeof input === 'function' ? (input() as { ownerKey?: unknown }).ownerKey : undefined;
      if (typeof ownerKey === 'string' && ownerKey.includes('rx-af')) rxAudioScalar.bindings.push(binding);
      return binding;
    },
  };
});

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: vi.fn(() => ({ state: 'connected', epoch: 1 })),
  onCommandDelivery: vi.fn(() => () => undefined),
  onControlSessionTransition: vi.fn(() => () => undefined),
  sendCommand: vi.fn(),
}));
vi.mock('$lib/runtime/commands/radio-intents', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/radio-intents')>();
  const { sendCommand } = await import('$lib/transport/ws-client');
  return {
    ...actual,
    dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params),
  };
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
    onTxAudioDied: () => () => {},
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return { state: 'connected' as const, epoch: 1 }; },
    subscribeControlAuthority(handler: (typeof h.authoritySubscribers extends Set<infer T> ? T : never)) {
      h.authoritySubscribers.add(handler);
      handler({
        state: h.state, caps: h.caps, session: { state: 'connected', epoch: 1 },
        rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
      });
      return () => { h.authoritySubscribers.delete(handler); };
    },
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
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => h.txController,
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: h.guardVisible, sourceLabel: 'MIC' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { audioManager } from '$lib/audio/audio-manager';
import { sendCommand } from '$lib/transport/ws-client';
import { MOD_INPUT_SOURCES, modInputCommand, modInputStateKey } from '$lib/radio/mod-input';
import { FOCUS_CHOICES, SPLIT_CHOICES } from '../../../semantic/rx-audio-instruments';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import HostedRadioLayoutFixture from '../../layout/__tests__/fixtures/HostedRadioLayoutFixture.svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import { makeAudioRoutingHandlers, makeModeHandlers, makeRxAudioHandlers } from '$lib/runtime/commands/panel-commands';
import { desktopV2Layout, sdrTestLayout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../../../presentation/workspace/resolution';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import type { FiniteControlAppearance } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';

/** MOR-2425 RX-B/RX-C — same shared external-renderer fixture the DSP/RF
 *  wiring tests use, so a mounted choice seat can be identified by its own
 *  accessible label (`retainedInvocations`) across a re-render. */
const finiteAppearance = {
  action: FiniteControlRendererFixture as FiniteControlAppearance['action'],
  toggle: FiniteControlRendererFixture as FiniteControlAppearance['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance['choice'],
} satisfies FiniteControlAppearance;

const RADIO_LAYOUT_SOURCE = readFileSync('src/components-v2/layout/RadioLayout.svelte', 'utf8');
const DESKTOP_V2_CONTROL_CSS = readFileSync('src/skins/desktop-v2/semantic-controls.css', 'utf8');

/**
 * (a), half one. Read BEFORE any `mockClear()` — the only pin that can see a
 * module-load side effect anywhere in the transitive import closure of the
 * wiring, the surface, the adapter and the real command bus.
 */
const LOAD_TIME_CALLS = [
  audioManager.startRx, audioManager.stopRx, audioManager.setRxVolume,
  audioManager.setAudioConfig, sendCommand, h.setRxLive, h.setVolume, h.setMuted,
].map((spy) => vi.mocked(spy).mock.calls.length);

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
    providerGeneration: 1,
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
  providerGeneration: 1,
} as unknown as Capabilities);

const AUDIO_TAGS = ['audio', 'tx', 'dual_rx', 'af_level', 'mod_input_routing'] as const;
/** A radio with NO audio chain at all: no live audio, no AF control, no
 *  dual-RX routing, no MOD-input routing ⇒ the adapter emits no group. */
const SILENT_TAGS = ['tx'] as const;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

function publishAuthority(): void {
  for (const subscriber of h.authoritySubscribers) {
    subscriber({
      state: h.state, caps: h.caps, session: { state: 'connected', epoch: 1 },
      rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
    });
  }
}

function render(props: { strips?: 'single' | 'dual' } = {}, plan?: SurfacePlan): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = plan === undefined
    ? undefined
    : new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]);
  component = mount(SemanticRadioSurfaces, { target, props, context });
  flushSync();
}

function renderHosted() {
  target = document.createElement('div');
  document.body.appendChild(target);
  const props = proxy<{ rxAudioLayout: 'grouped' | 'independent' }>({
    rxAudioLayout: 'grouped',
  });
  component = mount(HostedRadioLayoutFixture, { target, props });
  flushSync();
  return props;
}

/**
 * MOR-2425 RX-B/RX-C — the REAL per-skin `SURFACE_PLAN_CONTEXT_KEY` override
 * (mirrors `semantic-rf-front-end-wiring.component.test.ts`'s own
 * `renderHostedFace()`), through the actual `RadioLayout.svelte` — the only
 * mount that places the finite five differently by skin (named Standard
 * seats on `desktop-v2`, the grouped surface on `sdr-test`).
 */
function renderHostedFace(skinId: 'desktop-v2' | 'sdr-test' = 'desktop-v2') {
  target = document.createElement('div');
  document.body.appendChild(target);
  const props = proxy({ skinId });
  const context = new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () =>
    resolveSurfacePlan(props.skinId === 'desktop-v2' ? desktopV2Layout : sdrTestLayout,
      readWorkspace({ version: 1 }).workspace)]]);
  component = mount(HostedRadioLayoutFixture, { target, props, context });
  flushSync();
  return props;
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const el = (id: string) => q<HTMLElement>(`[data-testid="rx-audio-${id}"]`);
const text = (id: string) => el(id)?.textContent?.trim();
const afSlider = () => q<HTMLElement>('[data-testid="rx-audio-af"] [role="slider"]');

const SEAM_SPIES = () => [
  audioManager.startRx, audioManager.stopRx, audioManager.setRxVolume,
  audioManager.setAudioConfig, sendCommand, h.setRxLive, h.setVolume, h.setMuted,
];

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  h.state = liveState();
  h.caps = liveCaps(AUDIO_TAGS);
  h.audio = { muted: false, rxEnabled: true, volume: 42 };
  h.audioConnected = true;
  h.rxEnabled = true;
  h.guardVisible = false;
  h.selectedFiniteAppearance = undefined;
  rxAudioScalar.bindings.length = 0;
  resetRetainedInvocations();
  for (const spy of SEAM_SPIES()) vi.mocked(spy).mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  expect(h.authoritySubscribers.size).toBe(0);
  expect(txHarness.listenerCount()).toBe(0);
  expect(txHarness.trace()).toEqual([]);
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
  it('renders a browser volume of 42 as 42% while keeping an AF control value of 0.42', () => {
    render();
    expect(Number(afSlider()!.getAttribute('aria-valuenow')))
      .toBeCloseTo(0.42, 10);
    expect(text('af-value')).toBe('42%');
  });

  it.each([0, 7, 50, 100])('renders a browser volume of %i on the 0..1 scale', (volume) => {
    h.audio = { muted: false, rxEnabled: true, volume };
    render();
    expect(Number(afSlider()!.getAttribute('aria-valuenow')))
      .toBeCloseTo(volume / 100, 10);
  });

  // MUTATION KILLED: a rescale on the way OUT. Driven through the REAL
  // `makeRxAudioHandlers` the wiring composes, so the round trip 42 → 0.42 →
  // 43 is proven end to end rather than asserted about a stub.
  it('returns one 0.01 step to the runtime as a 0..100 volume, through the real bus', () => {
    render();
    afSlider()!.dispatchEvent(new KeyboardEvent(
      'keydown', { key: 'ArrowRight', bubbles: true, cancelable: true },
    ));
    flushSync();
    expect(h.setRxVolume).toHaveBeenCalledExactlyOnceWith(0.43);
    expect(h.setVolume).toHaveBeenCalledExactlyOnceWith(43);
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

describe('the hosted AF owner survives replaceable presentation layouts', () => {
  it('cancels route A-B-A and detached drafts while retaining canonical readback', () => {
    h.audio = proxy({ muted: false, rxEnabled: true, volume: 42 });
    const props = renderHosted();
    const originalSubscribers = [...h.authoritySubscribers];
    const oldSlider = afSlider()!;
    const frame = oldSlider.closest<HTMLElement>('.vc-hbar')!;
    frame.getBoundingClientRect = () => ({
      left: 0, right: 100, top: 0, bottom: 10, width: 100, height: 10, x: 0, y: 0,
      toJSON: () => ({}),
    });
    Object.assign(oldSlider, {
      setPointerCapture: vi.fn(), releasePointerCapture: vi.fn(), hasPointerCapture: () => true,
    });
    oldSlider.dispatchEvent(new PointerEvent(
      'pointerdown', { pointerId: 7, clientX: 42, bubbles: true },
    ));
    h.audio.rxEnabled = false;
    publishAuthority();
    h.audio.rxEnabled = true;
    publishAuthority();
    h.setRxVolume.mockClear();
    h.setVolume.mockClear();
    oldSlider.dispatchEvent(new PointerEvent(
      'pointermove', { pointerId: 7, clientX: 90, bubbles: true },
    ));
    oldSlider.dispatchEvent(new PointerEvent('pointerup', { pointerId: 7, bubbles: true }));
    expect(h.setVolume).not.toHaveBeenCalled();

    oldSlider.dispatchEvent(new PointerEvent(
      'pointerdown', { pointerId: 8, clientX: 70, bubbles: true },
    ));
    flushSync();
    expect(frame.style.getPropertyValue('--vc-fill-percent')).toBe('70%');
    expect(h.setVolume).toHaveBeenCalledExactlyOnceWith(70);
    h.setRxVolume.mockClear();
    h.setVolume.mockClear();

    props.rxAudioLayout = 'independent';
    flushSync();
    const newSlider = q<HTMLElement>('[role="slider"][aria-label="AF"]')!;
    expect(newSlider).not.toBe(oldSlider);
    expect(target.querySelector('[data-af-layout="independent"]')).not.toBeNull();
    expect(target.querySelectorAll('[role="slider"][aria-label="AF"]')).toHaveLength(1);
    expect([...h.authoritySubscribers]).toEqual(originalSubscribers);
    expect(h.authoritySubscribers.size).toBe(6);
    expect(newSlider.closest<HTMLElement>('.vc-hbar')!.style
      .getPropertyValue('--vc-fill-percent')).toBe('42%');
    expect(newSlider.getAttribute('aria-valuenow')).toBe('0.42');
    oldSlider.dispatchEvent(new PointerEvent('pointerup', { pointerId: 8, bubbles: true }));
    oldSlider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(h.setVolume).not.toHaveBeenCalled();

    newSlider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    flushSync();
    expect(h.setRxVolume).toHaveBeenCalledExactlyOnceWith(0.43);
    expect(h.setVolume).toHaveBeenCalledExactlyOnceWith(43);

    h.audio.volume = 43;
    flushSync();
    expect(newSlider.getAttribute('aria-valuenow')).toBe('0.43');
    props.rxAudioLayout = 'grouped';
    flushSync();
    expect(afSlider()!.getAttribute('aria-valuenow')).toBe('0.43');
    expect(target.querySelectorAll('[role="slider"][aria-label="AF"]')).toHaveLength(1);
    expect([...h.authoritySubscribers]).toEqual(originalSubscribers);
    unmount(component!);
    component = null;
    expect(h.authoritySubscribers.size).toBe(0);
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

  // MOR-2425 RX-B: every offered focus, not a single sample — a fresh mount
  // per value, matching the per-value discipline the RF-front-end wiring
  // test already established for preamp/attenuator.
  it.each(FOCUS_CHOICES)('routes the %s routing-focus pick to the shipped audio-config command', (focus) => {
    render();
    el(`focus-${focus}`)!.click();
    flushSync();
    expect(audioManager.setAudioConfig).toHaveBeenCalledExactlyOnceWith({ focus });
  });

  it.each(SPLIT_CHOICES)('routes the %s stereo-split pick to the shipped audio-config command', (value, label) => {
    render();
    el(`split-${label}`)!.click();
    flushSync();
    expect(audioManager.setAudioConfig).toHaveBeenCalledExactlyOnceWith({ split_stereo: value });
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

describe('common MOD-input selector reaches the existing mode handler (MOR-2366)', () => {
  function selectSource(source: number): HTMLSelectElement {
    const select = el('mod-select') as HTMLSelectElement;
    expect(select).not.toBeNull();
    select.value = String(source);
    select.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();
    return select;
  }

  it.each(MOD_INPUT_SOURCES)('dispatches the $label source exactly once', ({ value }) => {
    render();
    expect(selectSource(value).disabled).toBe(false);
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_data_off_mod_input', { source: value });
    expect(text('mod-source')).toBe('MOD: LAN');
    expect(el('mod-input')!.dataset.readiness).toBe('ready');
  });

  it.each([0, 1, 2, 3])('routes DATA group %i to its own source command', (dataMode) => {
    const state = liveState();
    h.state = {
      ...state, main: { ...state.main, dataMode },
      [modInputStateKey(dataMode)]: 3,
      fieldStatus: { ...state.fieldStatus, [modInputStateKey(dataMode)]: fresh },
    };
    render();
    selectSource(1);
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith(modInputCommand(dataMode), { source: 1 });
  });

  it.each(['active', 'main.dataMode', 'dataOffModInput'])(
    'preserves command admission when %s becomes unavailable after rendering', (path) => {
      render();
      const state = liveState();
      h.state = {
        ...state,
        fieldStatus: { ...state.fieldStatus, [path]: { ...fresh, availability: 'missing' } },
      };
      publishAuthority();
      selectSource(3);
      expect(sendCommand).not.toHaveBeenCalled();
    },
  );

  it('renders an unread source as disabled and sends nothing', () => {
    h.state = liveState({ dataOffModInput: null });
    render();
    expect(selectSource(0).disabled).toBe(true);
    expect(text('mod-source')).toBe('MOD: —');
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it.each([-1, 4, null])('preserves admission for invalid DATA group %s', (dataMode) => {
    render();
    const state = liveState();
    h.state = { ...state, main: { ...state.main, dataMode } };
    publishAuthority();
    selectSource(3);
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('omits the selector without modulation routing capability', () => {
    h.caps = liveCaps(AUDIO_TAGS.filter((tag) => tag !== 'mod_input_routing'));
    render();
    expect(el('surface')).not.toBeNull();
    expect(el('mod-select')).toBeNull();
    expect(sendCommand).not.toHaveBeenCalled();
  });
});

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
   * first semantic surface carrying interactive controls that the DUAL
   * composition's only layout (`dual-receiver-cockpit.ts`) declares no zone
   * for, and MOR-1069's cockpit rule is that every focusable
   * control lives inside a declared zone with rx-tx last in the tab order
   * (`skins/dual-receiver-cockpit/__tests__/DualReceiverCockpit.component.test.ts`
   * enforces it). Mounted bare it breaks both clauses; folded into the rx-tx
   * zone it would put an AF slider between the operator and the unkey button.
   * It waited for a declared zone, which this slice made possible;
   * `desktop-v2` supplied one in MOR-1368 (S9), and the cockpit still has not.
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
    txHarness.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on' });
    h.state = liveState({ ptt: true } as Partial<ServerState>);
    publishAuthority();
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

/**
 * MOR-2425 RX-B/RX-C. Each of the five finite controls has exactly ONE owner
 * (`RxAudioInstrumentHost`) and must render exactly once in the composed
 * tree — a double owner (grouped surface AND a named seat both rendering the
 * same field) would show two `[data-testid]` matches here. `desktop-v2`
 * places the five in NAMED Standard seats (`rxAudioFiniteLayout`,
 * `.rx-audio-finite-seat`); `sdr-test` keeps the grouped surface's own
 * default order — both proven through the real `RadioLayout.svelte`
 * (`renderHostedFace`).
 */
describe('each finite control has exactly one owner in the composed tree', () => {
  const ALWAYS = ['monitor', 'focus', 'split', 'mod-input'] as const;

  it('renders monitor, focus, split and MOD input exactly once (bare SemanticRadioSurfaces mount)', () => {
    render();
    for (const id of ALWAYS) {
      expect(target.querySelectorAll(`[data-testid="rx-audio-${id}"]`)).toHaveLength(1);
    }
  });

  it.each(['desktop-v2', 'sdr-test'] as const)(
    'renders monitor, focus, split and MOD input exactly once on %s',
    (skinId) => {
      renderHostedFace(skinId);
      for (const id of ALWAYS) {
        expect(target.querySelectorAll(`[data-testid="rx-audio-${id}"]`)).toHaveLength(1);
      }
    },
  );

  it('places the finite five in the NAMED Standard seat grid on desktop-v2', () => {
    h.state = liveState({ dataOffModInput: 0 } as Partial<ServerState>);
    renderHostedFace('desktop-v2');
    expect(target.querySelectorAll('[data-testid="rx-audio-mod-set-lan"]')).toHaveLength(1);
    const seats = [...target.querySelectorAll<HTMLElement>('.rx-audio-finite-seat')]
      .map((seat) => seat.dataset.field);
    expect(seats).toEqual([
      'monitorMode', 'routingFocus', 'routingSplit', 'modInputSource', 'setModInputLan',
    ]);
  });

  // The shipped Standard face stretches these buttons across the panel, and
  // the only rule that does it is `skins/desktop-v2/semantic-controls.css`'s
  // `… .rx-audio-row … > button { flex: 1 1 0 }`, which reaches the panel
  // width only while `.rx-audio-row` is itself panel-wide. jsdom computes no
  // layout, so the rules are pinned where they are written.
  it('stacks the Standard seats panel-wide, so the row buttons still stretch', () => {
    renderHostedFace('desktop-v2');
    expect(q('.rx-audio-finite-seat[data-field="monitorMode"] > .rx-audio-row')).not.toBeNull();
    expect(DESKTOP_V2_CONTROL_CSS)
      .toMatch(/\.rx-audio-row[^{]*\)\s*>\s*button\s*\{\s*flex:\s*1\s+1\s+0/);
    // A row-direction wrap grid sizes each seat to its content instead, which
    // stops the stretch at the widest label.
    const grid = /\.rx-audio-finite-grid\s*\{([^}]*)\}/.exec(RADIO_LAYOUT_SOURCE)?.[1] ?? '';
    expect(grid).toMatch(/flex-direction:\s*column/);
    expect(grid).not.toMatch(/wrap/);
    // Box-less seats: a structurally absent handle renders nothing, and a
    // seat box would still spend a column gap and push the rows apart.
    expect(RADIO_LAYOUT_SOURCE)
      .toMatch(/\.rx-audio-finite-seat\s*\{\s*display:\s*contents;\s*\}/);
  });

  it('has no Standard seat grid on sdr-test — the grouped surface owns placement there', () => {
    renderHostedFace('sdr-test');
    expect(target.querySelectorAll('.rx-audio-finite-seat')).toHaveLength(0);
  });
});

/**
 * MOR-2425 RX-B/RX-C persistence witness, over a REAL per-skin
 * `SURFACE_PLAN_CONTEXT_KEY` override so the resolved `SurfacePlan` genuinely
 * changes between `desktop-v2` and `sdr-test` (mirrors `semantic-rf-front-end
 * -wiring.component.test.ts`'s own fixed witness, transplanted here rather
 * than re-derived ad hoc, per that file's own review history: a DOM testid
 * or a plain "not the stale one" identity check both proved vacuous there).
 * Proves, in the same required order: (i) the host-owned AF
 * `createContinuousScalar` binding is the SAME object across the switch — a
 * DOM testid is not a valid anchor, since the zone wrapper recreates it on
 * both sides regardless of whether the host survives; (ii) a fresh current
 * monitor-mode invocation still commands normally; (iii) the confirmed
 * monitor-mode reading is unaffected by the switch itself; and only then
 * (iv) the stale pre-switch invocation is inert.
 */
describe('persistent RX-audio composition across a real Standard->SDR plan switch (MOR-2425 RX-B/RX-C)', () => {
  it('detaches the pre-switch monitor-mode invocation and keeps the new one live', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    const props = renderHostedFace('desktop-v2');
    const staleStandardMonitor = retainedInvocations.get('Monitor mode');
    expect(staleStandardMonitor).toBeDefined();
    expect(target.querySelectorAll('.rx-audio-finite-seat[data-field="monitorMode"]')).toHaveLength(1);
    const externalMonitor = () => target.querySelector<HTMLElement>('[data-testid="external-Monitor mode"]');
    const beforeReading = externalMonitor()!.dataset.reading;
    const hostBinding = rxAudioScalar.bindings.at(-1);

    props.skinId = 'sdr-test';
    flushSync();
    expect(target.querySelectorAll('.rx-audio-finite-seat')).toHaveLength(0);

    // (i) Identity, positively: the SAME host-owned AF binding object, not a
    // rebuilt one — the host survives the switch.
    expect(rxAudioScalar.bindings).toHaveLength(1);
    expect(rxAudioScalar.bindings.at(-1)).toBe(hostBinding);

    // (ii) A fresh, CURRENT invocation exists for the new (grouped)
    // placement and commands normally, through the real `makeRxAudioHandlers`.
    const currentSdrMonitor = retainedInvocations.get('Monitor mode');
    expect(currentSdrMonitor).toBeDefined();
    expect(currentSdrMonitor).not.toBe(staleStandardMonitor);
    currentSdrMonitor!('mute');
    flushSync();
    expect(h.setRxLive).toHaveBeenCalledExactlyOnceWith(false);
    expect(h.setMuted).toHaveBeenCalledExactlyOnceWith(true);

    // (iii) Display state (the confirmed reading) is unchanged by the switch
    // itself — only the click above changes anything downstream, and that
    // command is not reflected back into `h.state` here.
    expect(externalMonitor()!.dataset.reading).toBe(beforeReading);

    // (iv) Only now: the stale pre-switch invocation is DETACHED — its named
    // Standard seat was torn down when the layout moved to the grouped
    // surface.
    h.setRxLive.mockClear();
    h.setMuted.mockClear();
    staleStandardMonitor!('local');
    expect(h.setRxLive).not.toHaveBeenCalled();
    expect(h.setMuted).not.toHaveBeenCalled();
  });
});
