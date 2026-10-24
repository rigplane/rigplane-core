/**
 * MOR-2425 (Memory lane, phase B2) — the semantic Memory surface wired into
 * `SemanticRadioSurfaces` and mounted on the `desktop-v2` zone.
 *
 * Unlike every other family in this file's siblings, `memory` carries no
 * MOR-1262 RadioViewModel group: its local catalogue is browser-side
 * (`MemorySurface.svelte`'s own header), so the surface
 * mounts unconditionally and `facts`/handlers come from
 * `deriveMemoryPanelProps()` / `getMemoryHandlers()` — the SAME
 * panel-adapters singleton the legacy `MemoryPanel.svelte` already uses, not
 * a second instance.
 *
 * This file proves what only the composed tree can prove:
 *   (a) desktop-v2 mounts exactly one semantic Memory surface, inside the
 *       declared `memory` zone, with zero legacy `MemoryPanel` twins.
 *   (b) recall dispatches the real `panel-commands.ts` intent, per channel.
 *   (c) when the REAL adapter reports `vfoIdentityKnown: false`, the store
 *       affordance is disabled and a forced click dispatches nothing (the
 *       handler-level guard itself is pinned in `MemorySurface.test.ts`).
 *   (d) the surface unmounts on a layout that declares no `memory` zone, and
 *       remounts exactly once switching back.
 *   (e) radio-list acquisition, group selection and recall use the real
 *       controller, HTTP client and memory intent handlers. The shared reply
 *       is checked by tests/test_memory_list_api.py::
 *       test_memories_endpoint_serves_a_page_with_the_profile_bounds.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { MemoryPageDto } from '$lib/types/memory-list';
import ic705MemoryPage from '../../../../../tests/fixtures/ic705-memory-page.json';
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
 * and NO `main.activeSlot` observation — one of the shapes
 * `relativeVfoIdentityUnknown()` reports true for (`panel-props.ts`), which
 * is what `MemorySurface.svelte`'s `facts.vfoIdentityKnown` gate reads.
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
 * Sets the runtime-mock facts (`deriveMemoryPanelProps` reads
 * `runtime.state`/`runtime.caps`).
 */
function useFacts(state: ServerState, caps: Capabilities): void {
  h.state = state;
  h.caps = caps;
}

/** Mounts the REAL `RadioLayout` behind a reactive `skinId`, with a resolved
 *  `SurfacePlan` in context so `zoneOwning()` can answer for real — same
 *  shape as `semantic-cw-keyer-wiring.component.test.ts`'s `renderHosted`. */
function renderHosted(skinId: 'desktop-v2' | 'sdr-test' = 'desktop-v2') {
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
const qAll = (sel: string) => target.querySelectorAll(sel);
const commands = () => vi.mocked(sendCommand).mock.calls.map(([name, params]) => ({ name, params }));
const press = (node: HTMLElement) => node.dispatchEvent(new MouseEvent('click', { bubbles: true }));

async function settleList(): Promise<void> {
  for (let turn = 0; turn < 50; turn++) { await Promise.resolve(); flushSync(); }
}

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
  vi.unstubAllGlobals();
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

it('makes no memories requests in a hosted layout without a memory zone, even with memory_read', async () => {
  const fetchMock = vi.fn().mockRejectedValue(new Error('Unexpected memories request'));
  vi.stubGlobal('fetch', fetchMock);
  useState(liveState(), { ...liveCaps(), capabilities: ['memory_read'] });

  renderHosted('sdr-test');
  await settleList();

  expect(q('[data-zone-id="memory"]')).toBeNull();
  expect(q('[data-testid="memory-surface"]')).toBeNull();
  expect(q('[data-testid="radio-memories"]')).toBeNull();
  expect(fetchMock).not.toHaveBeenCalled();
});

it('cancels in-flight memories requests when the hosted memory surface unmounts and acquires again on remount', async () => {
  const signals: (AbortSignal | null | undefined)[] = [];
  const replies: ((response: Response) => void)[] = [];
  const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    signals.push(init?.signal);
    return new Promise<Response>((resolve) => replies.push(resolve));
  });
  vi.stubGlobal('fetch', fetchMock);
  useState(liveState(), { ...liveCaps(), capabilities: ['memory_read'] });

  const props = renderHosted();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(signals[0]?.aborted).toBe(false);

  props.skinId = 'sdr-test';
  flushSync();
  expect(q('[data-testid="memory-surface"]')).toBeNull();
  expect(signals[0]?.aborted).toBe(true);
  replies[0]({ ok: true, status: 200, json: async () => ic705MemoryPage } as Response);
  await settleList();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  props.skinId = 'desktop-v2';
  flushSync();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(q('[data-testid="radio-memories-status"]')?.dataset.status).toBe('loading');
  expect(signals[1]?.aborted).toBe(false);

  await unmount(component!);
  component = null;
  expect(signals[1]?.aborted).toBe(true);
  replies[1]({ ok: true, status: 200, json: async () => ic705MemoryPage } as Response);
  await settleList();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it('acquires IC-705 radio memories only with memory_read, renders them, selects a group and recalls its channel', async () => {
  const golden = ic705MemoryPage as MemoryPageDto;
  const requests: URL[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    expect(url.pathname).toBe('/api/v1/memories');
    requests.push(url);
    const group = Number(url.searchParams.get('group') ?? golden.group);
    const start = Number(url.searchParams.get('start') ?? golden.channels.first);
    const count = Number(url.searchParams.get('count') ?? 25);
    const page: MemoryPageDto = {
      ...golden, group,
      entries: Array.from({ length: Math.min(count, golden.channels.last - start + 1) }, (_, offset) => {
        const channel = start + offset;
        return { ...(golden.entries.find((entry) => entry.channel === channel) ?? { channel, empty: true }), group };
      }),
    };
    return { ok: true, status: 200, json: async () => page } as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  const caps: Capabilities = {
    ...liveCaps(), model: 'IC-705', receivers: 1, vfoScheme: 'ab', capabilities: [],
  };

  useState(liveState(), caps);
  renderHosted();
  await settleList();
  expect(fetchMock).not.toHaveBeenCalled();
  expect(q('[data-testid="radio-memories"]')).toBeNull();
  await unmount(component!);
  component = null;
  target.remove();

  useState(liveState(), { ...caps, capabilities: ['memory_read'] });
  renderHosted();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(Object.fromEntries(requests[0].searchParams)).toEqual({ count: '25' });
  await settleList();
  expect(requests.map((url) => Object.fromEntries(url.searchParams))).toEqual([
    { count: '25' },
    { group: '0', start: '25', count: '25' },
    { group: '0', start: '50', count: '25' },
    { group: '0', start: '75', count: '25' },
  ]);
  expect(q('[data-testid="radio-memories-status"]')?.dataset.status).toBe('ready');
  const row = q('[data-testid="radio-memory-4"]');
  expect(row).not.toBeNull();
  expect(row!.querySelector('.rm-name')?.textContent).toBe('TEST RPT');
  expect(row!.querySelector('.rm-freq')?.textContent).toBe('146.940.000');
  expect(row!.querySelector('.rm-mode')?.textContent).toBe('FM');
  expect(commands()).toEqual([]);

  const chooser = q<HTMLInputElement>('[data-testid="radio-memories-group"]');
  expect(chooser).not.toBeNull();
  expect([chooser!.min, chooser!.max, chooser!.value]).toEqual(['0', '99', '0']);
  press(chooser!);
  chooser!.value = '7';
  chooser!.dispatchEvent(new Event('change', { bubbles: true }));
  flushSync();
  expect(Object.fromEntries(requests[4].searchParams)).toEqual({ group: '7', count: '25' });
  await settleList();
  expect(requests.slice(4).map((url) => Object.fromEntries(url.searchParams))).toEqual([
    { group: '7', count: '25' },
    { group: '7', start: '25', count: '25' },
    { group: '7', start: '50', count: '25' },
    { group: '7', start: '75', count: '25' },
  ]);
  expect(chooser!.value).toBe('7');
  expect(q('[data-testid="radio-memories-status"]')?.dataset.status).toBe('ready');
  expect(q('[data-testid="radio-memory-4"]')?.textContent).toContain('TEST RPT');
  expect(commands()).toEqual([]);
  const recall = q<HTMLButtonElement>('[data-testid="radio-memory-4-recall"]');
  expect(recall).not.toBeNull();
  expect(recall!.disabled).toBe(false);
  press(recall!);
  flushSync();
  expect(commands()).toEqual([{ name: 'set_memory_mode', params: { group: 7, channel: 4 } }]);
  expect(q('[data-testid="radio-memory-4"]')?.classList.contains('selected')).toBe(true);

  const reload = q<HTMLButtonElement>('[data-testid="radio-memories-reload"]');
  expect(reload).not.toBeNull();
  expect(reload!.disabled).toBe(false);
  press(reload!);
  flushSync();
  expect(Object.fromEntries(requests[8].searchParams)).toEqual({ group: '7', count: '25' });
  await settleList();
  expect(requests).toHaveLength(12);
  expect(chooser!.value).toBe('7');
  expect(q('[data-testid="radio-memories-status"]')?.dataset.status).toBe('ready');
  expect(q('[data-testid="radio-memory-4"]')?.textContent).toContain('TEST RPT');
  expect(commands()).toEqual([{ name: 'set_memory_mode', params: { group: 7, channel: 4 } }]);
});

/* ── (c) STORE refused under the WRONG-VFO doctrine ────────────── */

describe('store is refused end to end when vfoIdentityKnown is false', () => {
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
