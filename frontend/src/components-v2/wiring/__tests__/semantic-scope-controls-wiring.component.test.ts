/**
 * MOR-1311 — the semantic scope-controls surface wired into
 * `SemanticRadioSurfaces` (vocabulary slice 11B, the scope toolbar — the
 * LAST B-slice of the vocabulary program).
 *
 * `semantic/__tests__/ScopeControlsSurface.test.ts` proves what the pure
 * surface does with a view model. This file proves what only the composed
 * tree can prove, using the REAL adapter and the REAL command bus (only the
 * runtime/transport/TX-authority SEAMS are spied), mirroring
 * `semantic-ritxit-scan-wiring.component.test.ts`:
 *
 *   (a) every control category (choice, toggle, stepper) reaches the wire as
 *       the exact command `SpectrumToolbar.svelte`/`ScopeSettingsPopover.svelte`
 *       themselves dispatch — composed, not forked.
 *   (b) MOUNTING CANON (MOR-1304 ruling). Control-bearing, and the DUAL
 *       composition's only layout (`dual-receiver-cockpit.ts`) declares no
 *       `scopeControls` zone, so it mounts in the SINGLE composition only and
 *       renders NOTHING in the DUAL composition. Not bare under `desktop-v2`
 *       any more: MOR-1370 (S6b-2) declared that zone, the last surface in
 *       the vocabulary to graduate
 *       — pinned with a view model that actually CARRIES the group (the
 *       rxAudio/ritXitScan/cwKeyer precedent), plus a control test mounting
 *       the same fixture in single to foreclose vacuity.
 *   (c) the default path (no `scope` capability) stays byte-identical.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount, type Snippet } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import SpectrumPanelStub from '../../layout/__tests__/SpectrumPanelStub.svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';


const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  live: null as unknown,
  selectedFiniteAppearance: undefined as unknown,
  controlSession: { state: 'connected', epoch: 1 } as { state: string; epoch: number },
  controlSessionSubscriber: null as ((next: { state: string; epoch: number }) => void) | null,
  authoritySubscriber: null as ((next: {
    state: unknown; caps: unknown; session: { state: string; epoch: number };
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void) | null,
  txController: null as ManagedAppTxController | null,
  audio: { muted: true, rxEnabled: false, volume: 0 },
  audioConnected: false,
  guardVisible: false,
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
vi.mock('../../../component-kits/activation', () => ({
  getSelectedFiniteControlAppearance: () => h.selectedFiniteAppearance,
  getSelectedFrequencyReadout: () => undefined,
  getSelectedScalarAppearance: () => undefined,
}));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});
vi.mock('$lib/runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return (h.live as Map<string, unknown> | null)?.get('state') ?? h.state; },
    get caps() { return (h.live as Map<string, unknown> | null)?.get('caps') ?? h.caps; },
    get controlSession() { return h.controlSession; },
    subscribeControlSession(handler: (next: { state: string; epoch: number }) => void) {
      h.controlSessionSubscriber = handler;
      return () => { if (h.controlSessionSubscriber === handler) h.controlSessionSubscriber = null; };
    },
    subscribeControlAuthority(handler: typeof h.authoritySubscriber) {
      h.authoritySubscriber = handler;
      handler?.({
        state: (h.live as Map<string, unknown> | null)?.get('state') ?? h.state,
        caps: (h.live as Map<string, unknown> | null)?.get('caps') ?? h.caps,
        session: h.controlSession,
        rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
      });
      return () => { if (h.authoritySubscriber === handler) h.authoritySubscriber = null; };
    },
    get audio() { return h.audio; },
    get connectionAudio() { return h.audioConnected; },
    // MOR-1312 slice 12B: the wiring now also hands the adapter a
    // scope-display snapshot (the FIFTH argument). This file tests
    // scopeControls, so this stays on its pre-1312 path regardless of
    // these values.
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => h.txController,
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: h.guardVisible, sourceLabel: 'MIC' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { sendCommand } from '$lib/transport/ws-client';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import { getRadioState, resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import type { FiniteControlAppearance } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
// MOR-1370 (S6b-2): the REAL manifests + the REAL resolution seam, mirroring
// `semantic-scope-display-wiring.component.test.ts`'s "MOR-1365 (S6a)"
// section — the only way to prove the `scope-controls` zone binding and the
// S5/S6-pre subtraction asymmetry, since `useSurfacePlan()` falls back to
// `NO_PLAN` on a standalone mount.
import {
  desktopV2Layout, dualReceiverCockpitLayout, sdrTestLayout,
} from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../../../presentation/workspace/resolution';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' } as const;
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

/** A radio that has observed every `scopeControls` leaf — every field this
 *  surface can render is present so its structural gates all pass. */
function liveState(over: Partial<ServerState> = {}): ServerState {
  const scopeLeaves = [
    'mode', 'edge', 'span', 'speed', 'hold', 'refDb', 'dual', 'receiver',
    'duringTx', 'centerType', 'vbwNarrow', 'rbw',
  ];
  const paths = ['active', 'split', 'dualWatch', 'txTarget', ...scopeLeaves.map((l) => `scopeControls.${l}`)];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
    sMeter: 0, att: 0, preamp: 0, nb: false, nr: false,
    afLevel: 0, rfGain: 0, squelch: 0,
  });
  return {
    revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
    updatedAt: '2026-08-09T00:00:00Z', tunerStatus: 0,
    stateContractVersion: 1, providerGeneration: 31,
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    scopeControls: {
      mode: 1, edge: 2, span: 3, speed: 1, hold: false, refDb: -5, dual: false, receiver: 0,
      duringTx: false, centerType: 0, vbwNarrow: false, rbw: 0,
    },
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (tags: readonly string[]): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 31,
  model: 'fixture', scope: true, audio: false, tx: true,
  capabilities: tags, receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: 'hardware', audioFftAvailable: false,
} as unknown as Capabilities);

/** `scope` + `dual_rx` is what makes the FULL group (incl. `dual`/`receiver`)
 *  present; `NO_SCOPE_TAGS` declines the group evidence gate entirely. */
const SCOPE_TAGS = ['tx', 'scope', 'dual_rx'] as const;
const NO_SCOPE_TAGS = ['tx'] as const;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

function render(props: { strips?: 'single' | 'dual'; regions?: boolean; scopeControlsInRegionContent?: boolean; regionContent?: Snippet<[Snippet | undefined]> } = {}, plan?: SurfacePlan): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = plan === undefined
    ? undefined
    : new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]);
  component = mount(SemanticRadioSurfaces, { target, props, context });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const el = (id: string) => q<HTMLElement>(`[data-testid="${id}"]`);
function useState(state: ServerState): void {
  h.state = state;
  (h.live as SvelteMap<string, unknown> | null)?.set('state', state);
  resetRadioState();
  setRadioState(state);
}

function useCaps(caps: Capabilities): void {
  h.caps = caps;
  (h.live as SvelteMap<string, unknown> | null)?.set('caps', caps);
  setCapabilities(caps);
}

function publishAuthority(
  state: ServerState,
  caps: Capabilities = h.caps as Capabilities,
  session: { state: string; epoch: number } = h.controlSession,
): void {
  h.state = state;
  h.caps = caps;
  h.controlSession = session;
  (h.live as SvelteMap<string, unknown>).set('state', state);
  (h.live as SvelteMap<string, unknown>).set('caps', caps);
  h.authoritySubscriber?.({
    state, caps, session,
    rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
  });
}

const finiteFixture = FiniteControlRendererFixture as FiniteControlAppearance['action'];
const finiteAppearance = {
  action: finiteFixture,
  toggle: FiniteControlRendererFixture as FiniteControlAppearance['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance['choice'],
} satisfies FiniteControlAppearance;

beforeEach(() => {
  h.live = new SvelteMap<string, unknown>();
  h.selectedFiniteAppearance = undefined;
  h.controlSession = { state: 'connected', epoch: 1 };
  h.controlSessionSubscriber = null;
  h.authoritySubscriber = null;
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  useCaps(liveCaps(SCOPE_TAGS));
  useState(liveState());
  h.audio = { muted: true, rxEnabled: false, volume: 0 };
  h.audioConnected = false;
  h.guardVisible = false;
  vi.mocked(sendCommand).mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  resetRetainedInvocations();
  expect(txHarness.listenerCount()).toBe(0);
  expect(txHarness.trace()).toEqual([]);
  document.body.innerHTML = '';
});

/* ── (a) every control category reaches the shipped command vocabulary ── */

describe('the surface intents reach the shipped scope command vocabulary', () => {
  it('a mode click sends set_scope_mode with the absolute wire ordinal', () => {
    expect(getRadioState()?.scopeControls?.mode).toBe(1);
    render();
    el('scope-mode-2')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_mode', { mode: 2 });
  });

  it('an edge click sends set_scope_edge', () => {
    render();
    el('scope-edge-3')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_edge', { edge: 3 });
  });

  it('a centerType click sends set_scope_center_type with the snake_case param', () => {
    render();
    el('scope-centerType-1')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_center_type', { center_type: 1 });
  });

  it('an rbw click sends set_scope_rbw', () => {
    render();
    el('scope-rbw-2')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_rbw', { rbw: 2 });
  });

  it('the receiver click sends switch_scope_receiver — the ONE receiver/source field', () => {
    render();
    el('scope-receiver-1')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('switch_scope_receiver', { receiver: 1 });
  });

  it('the HOLD toggle sends set_scope_hold with the flipped boolean', () => {
    render();
    el('scope-hold')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_hold', { on: true });
  });

  it('the DUAL toggle sends set_scope_dual', () => {
    render();
    el('scope-dual')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_dual', { dual: true });
  });

  it('the "During TX" toggle sends set_scope_during_tx', () => {
    render();
    el('scope-duringTx')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_during_tx', { on: true });
  });

  it('the VBW-narrow toggle sends set_scope_vbw with `narrow`', () => {
    render();
    el('scope-vbwNarrow')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_vbw', { narrow: true });
  });

  it('the SPAN stepper sends set_scope_span through the shipped clampSpan', () => {
    useState(liveState({ scopeControls: { ...liveState().scopeControls, mode: 0, span: 3 } } as Partial<ServerState>));
    render();
    el('scope-span')!.querySelectorAll('button')[1]!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_span', { span: 4 });
  });

  it('the SPEED stepper sends set_scope_speed', () => {
    render();
    el('scope-speed')!.querySelectorAll('button')[1]!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_speed', { speed: 0 });
  });

  it('the REF stepper sends set_scope_ref, stepping by 5', () => {
    render();
    el('scope-ref')!.querySelectorAll('button')[1]!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_ref', { ref: 0 });
  });
});

describe('selected finite Scope authority lifetime (MOR-2425)', () => {
  const withSlot = (state: ServerState, receiver: 'main' | 'sub', activeSlot: 'A' | 'B') => ({
    ...state,
    [receiver]: { ...state[receiver], activeSlot },
  } as ServerState);

  it('revokes retained A1 synchronously on unobserved A→B→A before flush, then admits only fresh A3', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    const a1 = liveState();
    render();
    const retainedA1 = {
      action: retainedInvocations.get('+')!,
      toggle: retainedInvocations.get('HOLD')!,
      choice: retainedInvocations.get('Scope center type')!,
    };
    expect(Object.values(retainedA1).every(callback => typeof callback === 'function')).toBe(true);

    const b2 = withSlot({ ...a1, stateRevision: 2 } as ServerState, 'main', 'B');
    const a3 = { ...a1, stateRevision: 3 } as ServerState;
    publishAuthority(b2);
    publishAuthority(a3);
    resetRadioState();
    setRadioState(a3);

    retainedA1.action();
    retainedA1.toggle();
    retainedA1.choice(1);
    expect(sendCommand).not.toHaveBeenCalled();

    flushSync();
    const retainedA3 = {
      action: retainedInvocations.get('+')!,
      toggle: retainedInvocations.get('HOLD')!,
      choice: retainedInvocations.get('Scope center type')!,
    };
    for (const kind of ['action', 'toggle', 'choice'] as const) {
      expect(retainedA3[kind]).not.toBe(retainedA1[kind]);
    }
    retainedA3.action();
    retainedA3.toggle();
    retainedA3.choice(1);
    expect(vi.mocked(sendCommand).mock.calls).toEqual([
      ['set_scope_ref', { ref: 0 }],
      ['set_scope_hold', { on: true }],
      ['set_scope_center_type', { center_type: 1 }],
    ]);
    retainedA1.action();
    retainedA1.toggle();
    retainedA1.choice(2);
    expect(sendCommand).toHaveBeenCalledTimes(3);
  });

  it.each([
    ['session', (state: ServerState, caps: Capabilities) => ({ state, caps, session: { state: 'connected', epoch: 2 } })],
    ['provider', (state: ServerState, caps: Capabilities) => ({
      state: { ...state, providerGeneration: 32 } as ServerState,
      caps: { ...caps, providerGeneration: 32 } as Capabilities,
      session: h.controlSession,
    })],
    ['topology', (state: ServerState, caps: Capabilities) => ({
      state,
      caps: { ...caps, vfoScheme: 'ab_shared', receivers: 2 } as Capabilities,
      session: h.controlSession,
    })],
    ['active receiver', (state: ServerState, caps: Capabilities) => ({
      state: { ...state, active: 'SUB' } as ServerState, caps, session: h.controlSession,
    })],
    ['active slot', (state: ServerState, caps: Capabilities) => ({
      state: withSlot(state, 'main', 'B'), caps, session: h.controlSession,
    })],
  ] as const)('rotates the lease when %s authority changes', (_axis, replacement) => {
    h.selectedFiniteAppearance = finiteAppearance;
    render();
    const first = retainedInvocations.get('HOLD')!;
    const next = replacement(h.state as ServerState, h.caps as Capabilities);
    publishAuthority(next.state, next.caps, next.session);
    flushSync();
    expect(retainedInvocations.get('HOLD')).not.toBe(first);
    first();
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('keeps unknown receiver and active slots authoritative for readable global Scope controls', () => {
    const state = liveState();
    state.fieldStatus!.active = { ...fresh, observed: false, freshness: 'unknown', availability: 'missing' };
    state.fieldStatus!['main.activeSlot'] = { ...fresh, observed: false, freshness: 'unknown', availability: 'missing' };
    state.fieldStatus!['sub.activeSlot'] = { ...fresh, observed: false, freshness: 'unknown', availability: 'missing' };
    useState(state);
    h.selectedFiniteAppearance = finiteAppearance;
    render();

    const hold = el('external-HOLD') as HTMLButtonElement;
    expect(hold).not.toBeNull();
    expect(hold.disabled).toBe(false);
    hold.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_hold', { on: true });
  });

  it('keeps the chosen external appearance inert through disconnect and creates a fresh lease on return', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    render();
    const connected = retainedInvocations.get('HOLD')!;

    publishAuthority(h.state as ServerState, h.caps as Capabilities, { state: 'disconnected', epoch: 1 });
    connected();
    expect(sendCommand).not.toHaveBeenCalled();
    flushSync();
    expect(el('scope-hold')).toBeNull();
    expect(el('external-HOLD')).toBeNull();

    publishAuthority(h.state as ServerState, h.caps as Capabilities, { state: 'connected', epoch: 2 });
    flushSync();
    const returned = retainedInvocations.get('HOLD')!;
    expect(returned).not.toBe(connected);
    returned();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_scope_hold', { on: true });
  });

  it.each([
    ['provider mismatch', (state: ServerState, caps: Capabilities) => ({
      state, caps: { ...caps, providerGeneration: 99 } as Capabilities,
    })],
    ['invalid topology', (state: ServerState, caps: Capabilities) => ({
      state, caps: { ...caps, receivers: 1 } as Capabilities,
    })],
    ['absent Scope group', (state: ServerState, caps: Capabilities) => ({
      state, caps: liveCaps(NO_SCOPE_TAGS),
    })],
  ] as const)('revokes on %s without exposing native fallback controls', (_reason, invalidate) => {
    h.selectedFiniteAppearance = finiteAppearance;
    render();
    const retained = retainedInvocations.get('HOLD')!;
    const invalid = invalidate(h.state as ServerState, h.caps as Capabilities);
    publishAuthority(invalid.state, invalid.caps);
    retained();
    expect(sendCommand).not.toHaveBeenCalled();
    flushSync();
    expect(el('scope-hold')).toBeNull();
  });

  it('preserves the lease across irrelevant value updates while refreshing its view', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    render();
    const retained = retainedInvocations.get('HOLD')!;
    const changed = liveState({
      stateRevision: 2,
      scopeControls: { ...liveState().scopeControls!, hold: true },
      main: { ...liveState().main!, freqHz: 14251000 },
    });
    publishAuthority(changed);
    flushSync();
    expect(retainedInvocations.get('HOLD')).toBe(retained);
    expect((el('external-HOLD') as HTMLButtonElement).getAttribute('aria-pressed')).toBe('true');

    const unavailable = liveState({ stateRevision: 3 });
    unavailable.fieldStatus!['scopeControls.hold'] = {
      ...fresh, observed: false, freshness: 'unknown', availability: 'missing',
    };
    publishAuthority(unavailable);
    flushSync();
    expect(retainedInvocations.get('HOLD')).toBe(retained);
    expect((el('external-HOLD') as HTMLButtonElement).disabled).toBe(true);

    const telemetry = liveState({
      stateRevision: 4, freshnessRevision: 9, updatedAt: '2026-08-09T00:00:01Z',
      main: { ...liveState().main!, mode: 'LSB', freqHz: 14252000 },
      powerMeter: 45,
    });
    publishAuthority(telemetry);
    flushSync();
    expect(retainedInvocations.get('HOLD')).toBe(retained);
  });

  it('leaves the native Scope path unchanged when no appearance is selected', () => {
    render();
    expect(el('scope-hold')).not.toBeNull();
    expect(el('external-HOLD')).toBeNull();
    expect(h.authoritySubscriber).toBeNull();
  });
});

/* ── (b) MOUNTING CANON: single bare, dual absent ─────────────────────── */

describe('the surface mounts only in the single composition, never in dual', () => {
  it('renders no scope-controls surface for a radio without the scope capability', () => {
    useCaps(liveCaps(NO_SCOPE_TAGS));
    render();
    expect(el('scope-controls-surface')).toBeNull();
  });

  it('renders it bare in the single composition, outside every zone', () => {
    render();
    const surface = el('scope-controls-surface')!;
    expect(surface).not.toBeNull();
    expect(surface.closest('[data-zone-id]')).toBeNull();
  });

  /**
   * MOUNTING CANON (MOR-1304 ruling). The view model here DOES carry the
   * group — proven by the single-composition assertion above using the
   * IDENTICAL fixture (`SCOPE_TAGS`/`liveState()`), so this cannot pass
   * vacuously the way a cockpit fixture with no scope evidence would.
   */
  it('renders NO scope-controls surface in the dual composition, zoned or unzoned', () => {
    render({ strips: 'dual' });
    expect(el('scope-controls-surface')).toBeNull();
    expect(target.innerHTML).not.toContain('scope-controls-surface');
  });

  it('leaves the cockpit composition with no focusable control outside a declared zone', () => {
    render({ strips: 'dual' });
    const outside = [...target.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')]
      .filter((node) => !node.matches(':disabled') && node.tabIndex >= 0
        && node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });
});

/* ── (d) MOR-1370 (S6b-2) — desktop-v2 REALLY declares the zone; the ──────
   ── cockpit still mounts nothing (canon option (ii), single-only) ────── */

/**
 * `desktopV2Layout` now carries a `scope-controls` zone
 * (`presentation/layouts/desktop-declarations.ts`). Unlike `scopeDisplay`
 * (pure readout, mounted in both compositions), `scopeControls` is
 * control-bearing and mounts SINGLE-COMPOSITION-ONLY under the MOR-1304
 * canon — so there is no dual-composition half of this claim to make; (b)
 * above already proves the dual composition mounts nothing regardless of the
 * plan. What this section adds, using the REAL manifest + the REAL
 * `resolveSurfacePlan` seam (`semantic-scope-display-wiring.component
 * .test.ts`'s "MOR-1365 (S6a)" shape):
 *
 *   (a) the zone binds — `zoneOwning('scopeControls')` now answers
 *       `'scope-controls'` against desktop-v2's real plan, so the composed
 *       tree wraps the surface in `<div data-zone-id="scope-controls">`;
 *   (b) the S5/S6-pre asymmetry: a workspace that SUBTRACTS `scopeControls`
 *       from that zone costs the operator the wrapper `<div>`, never the
 *       controls — `zoned()` degrades to bare (S5-N3), so "the workspace hid
 *       it" and "no zone declares it" are indistinguishable and both render
 *       the pre-1370 bare element shape. MUTATION PROBE: remove the
 *       `zoned(...)` mount from `scopeControlsSurface`'s call site and BOTH
 *       tests below go red — (a) loses the wrapper, (b) loses the surface
 *       entirely;
 *   (c) the dual-receiver cockpit manifest is untouched by this slice, so the
 *       surface keeps mounting NOTHING there — MOR-1069 unmoved, and this is
 *       the one direction where "declared" and "undeclared" agree (both
 *       absent), which is exactly what canon option (ii) requires: declaring
 *       a zone on `desktop-v2` must never put a control into the cockpit.
 */
describe('desktop-v2 declares a REAL scope-controls zone; the cockpit mounts nothing (MOR-1370, S6b-2)', () => {
  /** What App resolves for `layout` given a stored workspace `fields`. */
  function planFor(layout: typeof desktopV2Layout, fields: Record<string, unknown>): SurfacePlan {
    return resolveSurfacePlan(layout, readWorkspace({ version: 1, ...fields }).workspace);
  }

  it('binds the scope-controls zone id against desktop-v2\'s real plan', () => {
    render({ strips: 'single' }, planFor(desktopV2Layout, {}));
    expect(el('scope-controls-surface')!.closest('[data-zone-id="scope-controls"]')).not.toBeNull();
  });

  // THE ASYMMETRY (S5 shape): a workspace subtraction costs the wrapper, not
  // the controls. MUTATION PROBE: reading the PLAN instead of the MANIFEST
  // for suppression anywhere in this channel would make this subtraction
  // able to resurrect the legacy toolbar half — this test only proves the
  // surface side (the legacy-twin side is `semantic-desktop-migration
  // .component.test.ts`'s job), but it is the half that shows the controls
  // themselves never disappear.
  it('degrades to a bare surface — never disappears — when the workspace subtracts scopeControls from its zone', () => {
    render({ strips: 'single' }, planFor(desktopV2Layout, {
      visibleSurfaces: { 'scope-controls': [] },
    }));
    const surface = el('scope-controls-surface')!;
    expect(surface).not.toBeNull();
    expect(surface.closest('[data-zone-id]')).toBeNull();
  });

  it('still mounts nothing in the dual-receiver cockpit — its manifest is untouched by this slice', () => {
    render({ strips: 'dual' }, planFor(dualReceiverCockpitLayout, {}));
    expect(el('scope-controls-surface')).toBeNull();
  });
});


describe('SDR hosted semantic scope controls (MOR-2358)', () => {
  const regionContent = createRawSnippet<[Snippet | undefined]>((scopeControls) => ({
    render: () => '<div class="spectrum-toolbar" data-testid="scope-toolbar-host"></div>',
    setup(element) {
      const child = mount(SpectrumPanelStub, { target: element, props: { scopeControls: scopeControls?.() } });
      return () => { unmount(child); };
    },
  }));
  const plan = (fields: Record<string, unknown> = {}) => resolveSurfacePlan(sdrTestLayout, readWorkspace({ version: 1, ...fields }).workspace);
  const hosted = () => render({ regions: true, scopeControlsInRegionContent: true, regionContent }, plan());

  it('moves exactly one zoned surface into region content with no former center sibling', () => {
    hosted();
    expect(target.querySelectorAll('[data-testid="scope-controls-surface"]')).toHaveLength(1);
    const surface = el('scope-controls-surface')!;
    expect(surface.closest('[data-zone-id="scope-controls"]')).not.toBeNull();
    expect(surface.closest('[data-testid="scope-toolbar-host"]')).not.toBeNull();
  });

  it.each([
    ['scope-mode-2', 0, 'set_scope_mode', { mode: 2 }],
    ['scope-hold', 0, 'set_scope_hold', { on: true }],
    ['scope-ref', 1, 'set_scope_ref', { ref: 0 }],
  ] as const)('hosted %s dispatches exactly the existing command', (id, index, command, params) => {
    hosted();
    const element = el(id)!;
    const control = element.tagName === 'BUTTON' ? element : element.querySelectorAll('button')[index]!;
    expect(control.closest('[data-testid="scope-toolbar-host"]')).not.toBeNull();
    control.click(); flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith(command, params);
  });

  it.each([0, 1, 2, 3, null])('preserves the semantic mode applicability matrix for mode %s', (mode) => {
    const source = liveState({ scopeControls: { ...liveState().scopeControls!, mode: mode ?? 0 } });
    if (mode === null) source.fieldStatus!['scopeControls.mode'] = { ...fresh, observed: false, freshness: 'unknown', availability: 'missing' };
    useState(source); hosted();
    for (const name of ['mode', 'speed', 'hold', 'ref', 'dual', 'receiver', 'duringTx', 'centerType', 'vbwNarrow', 'rbw']) {
      const leaf = el(`scope-${name}`); expect(leaf).not.toBeNull();
      expect(leaf!.closest('[data-testid="scope-toolbar-host"]')).not.toBeNull();
    }
    expect(el('scope-edge') !== null).toBe(mode === 1 || mode === 3);
    expect(el('scope-span') !== null).toBe(mode === 0 || mode === 2);
    if (mode === null) expect((el('scope-mode-0') as HTMLButtonElement).disabled).toBe(true);
  });

  it.each(['stale', 'unobserved'] as const)('keeps a supported %s leaf disabled and rejects a synthetic click', (status) => {
    const source = liveState();
    source.fieldStatus!['scopeControls.hold'] = { ...fresh, observed: status !== 'unobserved', freshness: status === 'stale' ? 'stale' : 'unknown', availability: status === 'stale' ? 'stale' : 'missing' };
    useState(source); hosted();
    const hold = el('scope-hold') as HTMLButtonElement;
    expect(hold.closest('[data-testid="scope-toolbar-host"]')).not.toBeNull(); expect(hold.disabled).toBe(true);
    hold.dispatchEvent(new MouseEvent('click', { bubbles: true })); flushSync(); expect(sendCommand).not.toHaveBeenCalled();
  });

  it('removes unsupported receiver leaves while keeping the supported surface hosted', () => {
    useCaps(liveCaps(['scope'])); hosted();
    expect(target.querySelectorAll('[data-testid="scope-controls-surface"]')).toHaveLength(1);
    expect(el('scope-controls-surface')!.closest('[data-testid="scope-toolbar-host"]')).not.toBeNull();
    expect(el('scope-dual')).toBeNull(); expect(el('scope-receiver')).toBeNull();
  });

  it('honors regions plan subtraction without a bare fallback', () => {
    render({ regions: true, scopeControlsInRegionContent: true, regionContent }, plan({ visibleSurfaces: { 'scope-controls': [] } }));
    expect(el('scope-toolbar-host')).not.toBeNull(); expect(el('scope-controls-surface')).toBeNull();
  });

  it('keeps the surface when the placement flag has no region-content consumer', () => {
    render({ regions: true, scopeControlsInRegionContent: true }, plan());
    expect(el('scope-controls-surface')?.closest('[data-zone-id="scope-controls"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-testid="scope-controls-surface"]')).toHaveLength(1);
  });

  it('leaves standalone non-regions rendering unchanged', () => {
    render({ scopeControlsInRegionContent: true, regionContent });
    expect(el('scope-controls-surface')).not.toBeNull(); expect(el('scope-toolbar-host')).toBeNull();
  });
});
