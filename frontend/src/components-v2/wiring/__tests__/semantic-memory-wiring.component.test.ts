/**
 * MOR-2425 (Memory lane, phase B2) — the semantic Memory surface wired into
 * `SemanticRadioSurfaces` and mounted on the `desktop-v2` zone.
 *
 * Unlike every other family in this file's siblings, `memory` carries no
 * MOR-1262 RadioViewModel group: the radio cannot report memory-channel
 * contents at all (`MemorySurface.svelte`'s own header), so the surface
 * mounts unconditionally and `facts`/handlers come from
 * `deriveMemoryPanelProps()` / `getMemoryHandlers()` — the SAME
 * panel-adapters singleton the legacy `MemoryPanel.svelte` already uses, not
 * a second instance.
 *
 * This file proves what only the composed tree can prove:
 *   (a) desktop-v2 mounts exactly one semantic Memory surface, inside the
 *       declared `memory` zone, with zero legacy `MemoryPanel` twins.
 *   (b) recall dispatches the real `panel-commands.ts` intent, per channel.
 *   (c) store is refused, end to end, when the adapter reports
 *       `vfoIdentityKnown: false` — the WRONG-VFO doctrine
 *       `MemorySurface.svelte`'s header documents, proven through the REAL
 *       adapter rather than a unit-test fixture.
 *   (d) the surface unmounts on a layout that declares no `memory` zone, and
 *       remounts exactly once switching back.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  authoritySubscribers: new Set<(next: {
    state: unknown; caps: unknown; session: ControlSessionTransition;
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void>(),
  audio: { muted: false, rxEnabled: true, volume: 42 },
  audioConnected: true,
  rxEnabled: true,
  txController: null as ManagedAppTxController | null,
  session: { state: 'connected' as ControlSessionTransition['state'], epoch: 1 },
  sessionSubscriber: undefined as ((event: ControlSessionTransition) => void) | undefined,
  delivery: undefined as ((event: CommandDeliveryEvent) => void) | undefined,
  transition: undefined as ((event: ControlSessionTransition) => void) | undefined,
}));

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(() => true),
  getControlSession: () => h.session,
  onCommandDelivery: vi.fn((handler: (event: CommandDeliveryEvent) => void) => {
    h.delivery = handler;
    return () => { if (h.delivery === handler) h.delivery = undefined; };
  }),
  onControlSessionTransition: vi.fn((handler: (event: ControlSessionTransition) => void) => {
    h.transition = handler;
    return () => { if (h.transition === handler) h.transition = undefined; };
  }),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    get rxEnabled() { return h.rxEnabled; },
    startRx: vi.fn(), stopRx: vi.fn(), setRxVolume: vi.fn(), setAudioConfig: vi.fn(),
    onTxAudioDied: () => () => {},
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return h.session; },
    subscribeControlSession(handler: (event: ControlSessionTransition) => void) {
      h.sessionSubscriber = handler;
      return () => { if (h.sessionSubscriber === handler) h.sessionSubscriber = undefined; };
    },
    subscribeControlAuthority(handler: (typeof h.authoritySubscribers extends Set<infer T> ? T : never)) {
      h.authoritySubscribers.add(handler);
      handler({
        state: h.state, caps: h.caps, session: h.session,
        rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
      });
      return () => { h.authoritySubscribers.delete(handler); };
    },
    get audio() { return h.audio; },
    get connectionAudio() { return h.audioConnected; },
    get rxEnabled() { return h.rxEnabled; },
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
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => {
    if (!h.txController) throw new Error('managed TX harness is not installed');
    return h.txController;
  },
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: 'LAN' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { sendCommand } from '$lib/transport/ws-client';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import { persistMemoryChannels } from '../../../semantic/memory-channels';
import HostedRadioLayoutFixture from '../../layout/__tests__/fixtures/HostedRadioLayoutFixture.svelte';
import { desktopV2Layout, sdrTestLayout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import { resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY } from '../../../presentation/workspace/resolution';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';

/**
 * A dual-receiver radio with a fully observed MAIN receiver — the shape
 * `currentA03cContext()`/`knownA03cReceiver()` (`panel-commands.ts`) and
 * `relativeVfoIdentityUnknown()` (`panel-props.ts`) both need to answer
 * "identity known, MAIN readable" for both the facts and the command
 * authority. `main`/`connection`/`txTarget` follow `isValidServerState`'s
 * real contract (`$lib/stores/radio.svelte.ts`) — the REAL `setRadioState`
 * is used here, unlike `memory-command-authority.isolated.test.ts`, which
 * mocks the store and so does not need it.
 */
const fresh = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
  lastObservedMonotonic: 10,
} as const;
const slot = (freqHz: number, mode: string) => ({ freqHz, mode, filterNum: 1, dataMode: 0 });
function liveState(): ServerState {
  const paths = ['active'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
  }
  const receiver = (hz: number) => ({
    ...slot(hz, 'USB'), vfoA: slot(hz, 'USB'), vfoB: slot(hz + 50_000, 'USB'), activeSlot: 'A',
    filter: 1, sMeter: 0, att: 0, preamp: 0, nb: false, nr: false, afLevel: 0, rfGain: 0, squelch: 0,
  });
  return {
    revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
    updatedAt: '2026-09-07T00:00:00Z', tunerStatus: 0,
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    stateContractVersion: 1, providerGeneration: 0,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14_074_000 },
    main: receiver(14_074_000), sub: receiver(14_100_000),
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}
const liveCaps = (): Capabilities => ({
  model: 'fixture', scope: false, audio: false, tx: true, stateContractVersion: 1, providerGeneration: 0,
  capabilities: ['dual_rx'], receivers: 2, vfoScheme: 'main_sub', freqRanges: [],
  modes: ['USB'], filters: ['FIL1'],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false }, txBands: [], scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

/**
 * A single-receiver `ab`-scheme radio with `vfoReadback: 'selected_unselected'`
 * and NO `main.activeSlot` observation — the one shape
 * `relativeVfoIdentityUnknown()` reports true for (`panel-props.ts`), which
 * is what `MemorySurface.svelte`'s `facts.vfoIdentityKnown` gate reads. Only
 * the FACTS path (`deriveMemoryPanelProps`, reading the mocked `runtime`)
 * needs this shape; the command-authority store is left untouched from
 * `beforeEach`'s reset, since the surface's own guard must refuse the click
 * before any command authority is even consulted.
 */
function relativeIdentityUnknownState(): ServerState {
  return { active: 'MAIN', main: { freqHz: 7_100_000, mode: 'LSB' }, fieldStatus: {} } as unknown as ServerState;
}
const relativeIdentityUnknownCaps = (): Capabilities => ({
  model: 'fixture', scope: false, audio: false, tx: true,
  capabilities: [], receivers: 1, vfoScheme: 'ab', vfoReadback: 'selected_unselected', freqRanges: [],
  modes: ['LSB'], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false }, txBands: [], scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

function useState(state: ServerState, caps: Capabilities): void {
  h.state = state;
  h.caps = caps;
  resetRadioState();
  setRadioState(state);
  setCapabilities(caps);
}

/**
 * Sets ONLY the runtime-mock facts (`deriveMemoryPanelProps` reads
 * `runtime.state`/`runtime.caps`), leaving the command-authority store as
 * given — used by the store-refusal test below to prove the refusal is a
 * handler-level guard in `MemorySurface.svelte` ahead of the command
 * authority, not merely an artifact of an empty store.
 */
function useFacts(state: ServerState, caps: Capabilities): void {
  h.state = state;
  h.caps = caps;
}

/** Mounts the REAL `RadioLayout` behind a reactive `skinId`, with a resolved
 *  `SurfacePlan` in context so `zoneOwning()` can answer for real — same
 *  shape as `semantic-cw-keyer-wiring.component.test.ts`'s `renderHosted`. */
function renderHosted() {
  target = document.createElement('div');
  document.body.appendChild(target);
  const props = proxy({ skinId: 'desktop-v2' as 'desktop-v2' | 'sdr-test' });
  const context = new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () =>
    resolveSurfacePlan(props.skinId === 'desktop-v2' ? desktopV2Layout : sdrTestLayout,
      readWorkspace({ version: 1 }).workspace)]]);
  component = mount(HostedRadioLayoutFixture, { target, props, context });
  flushSync();
  return props;
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const qAll = (sel: string) => target.querySelectorAll(sel);
const commands = () => vi.mocked(sendCommand).mock.calls.map(([name, params]) => ({ name, params }));
const press = (node: HTMLElement) => node.dispatchEvent(new MouseEvent('click', { bubbles: true }));

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  h.session = { state: 'connected', epoch: 1 };
  h.sessionSubscriber = undefined;
  vi.mocked(sendCommand).mockClear();
  localStorage.clear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
  localStorage.clear();
  expect(h.sessionSubscriber).toBeUndefined();
  expect(h.authoritySubscribers.size).toBe(0);
});

/* ── (a) MOUNTING — desktop-v2 zone, zero legacy twins ─────────── */

describe('the memory surface mounts on the declared desktop-v2 zone', () => {
  it('renders exactly one semantic Memory surface inside the memory zone, and zero legacy panels', () => {
    useState(liveState(), liveCaps());
    renderHosted();
    const host = q('[data-zone-id="memory"]');
    expect(host).not.toBeNull();
    expect(host!.querySelector('[data-testid="memory-surface"]')).not.toBeNull();
    expect(qAll('[data-testid="memory-surface"]')).toHaveLength(1);
    expect(qAll('[data-panel-id="memory"]')).toHaveLength(0);
  });
});

/* ── (b) RECALL — the real command-authority intent, per channel ── */

describe('recall dispatches the real memory-to-vfo intent, per channel', () => {
  it.each([5, 9])('channel %d: recalling it sends set_memory_mode + memory_to_vfo exactly once', (ch) => {
    useState(liveState(), liveCaps());
    persistMemoryChannels(new Map([[ch, { freq: 14_074_000, mode: 'USB', name: '' }]]));
    renderHosted();
    const recall = q<HTMLElement>(`[data-testid="memory-channel-${ch}-recall"]`);
    expect(recall).not.toBeNull();
    press(recall!);
    flushSync();
    expect(commands()).toEqual([
      { name: 'set_memory_mode', params: { channel: ch } },
      { name: 'memory_to_vfo', params: { channel: ch } },
    ]);
  });
});

/* ── (c) STORE refused under the WRONG-VFO doctrine ────────────── */

describe('store is refused end to end when vfoIdentityKnown is false', () => {
  // The command-authority store is set to a FULLY VALID state — the same
  // fixture the recall tests prove dispatches successfully — while only the
  // runtime-mock FACTS report identity-unknown. If the refusal were merely
  // an artifact of an empty/invalid store, this store would let the click
  // through; it does not, which is what isolates the refusal to
  // `MemorySurface.svelte`'s own `if (!facts.vfoIdentityKnown) return;`
  // guard (the WRONG-VFO doctrine its header documents) rather than to
  // `panel-commands.ts`'s command authority.
  it('never dispatches a command, even bypassing the disabled store button', () => {
    useState(liveState(), liveCaps());
    useFacts(relativeIdentityUnknownState(), relativeIdentityUnknownCaps());
    renderHosted();
    const toggle = q<HTMLElement>('[data-testid="memory-store-toggle"]');
    expect(toggle).not.toBeNull();
    expect(toggle!.hasAttribute('disabled')).toBe(true);
    // Bypass `disabled` with a real bubbling click, same as the phase A
    // surface unit test's `forceClick` precedent: the refusal must be a
    // handler-level guard, not merely an attribute.
    press(toggle!);
    flushSync();
    const confirm = q<HTMLElement>('[data-testid="memory-store-confirm"]');
    expect(confirm, 'store bar opened despite the disabled toggle').not.toBeNull();
    press(confirm!);
    flushSync();
    expect(commands()).toEqual([]);
  });
});

/* ── (d) MOUNT LIFECYCLE across a layout that declares no memory zone ── */

describe('the surface unmounts on a layout that declares no memory zone, and remounts once', () => {
  // `sdrTestLayout` declares no `memory` zone (only `desktop-v2` does), so
  // `declared.has('memory')` is false there and the MOR-1364 suppression
  // channel's own rule applies: the legacy twin is NOT suppressed on a
  // layout that never declared the zone (the same rule every other
  // MOR-1364 family follows — e.g. `cwKeyer`'s legacy panel reappears on an
  // undeclaring layout too). The legacy `MemoryPanel` reappearing here is
  // therefore the CORRECT behaviour, not a residual double-presentation.
  it('desktop-v2 -> sdr-test -> desktop-v2', () => {
    useState(liveState(), liveCaps());
    const props = renderHosted();
    expect(qAll('[data-testid="memory-surface"]')).toHaveLength(1);
    expect(qAll('[data-panel-id="memory"]')).toHaveLength(0);

    props.skinId = 'sdr-test';
    flushSync();
    expect(qAll('[data-testid="memory-surface"]')).toHaveLength(0);
    expect(qAll('[data-panel-id="memory"]')).toHaveLength(1);

    props.skinId = 'desktop-v2';
    flushSync();
    expect(qAll('[data-testid="memory-surface"]')).toHaveLength(1);
    expect(qAll('[data-panel-id="memory"]')).toHaveLength(0);
  });
});
