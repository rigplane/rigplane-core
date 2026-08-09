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
 *   (b) MOUNTING CANON (MOR-1304 ruling). Control-bearing, no manifest
 *       declares a `scopeControls` zone, so it mounts in the SINGLE
 *       composition only, bare, and renders NOTHING in the DUAL composition
 *       — pinned with a view model that actually CARRIES the group (the
 *       rxAudio/ritXitScan/cwKeyer precedent), plus a control test mounting
 *       the same fixture in single to foreclose vacuity.
 *   (c) the default path (no `scope` capability) stays byte-identical.
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
  audio: { muted: true, rxEnabled: false, volume: 0 },
  audioConnected: false,
  guardVisible: false,
  listeners: new Set<(next: unknown) => void>(),
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});
vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
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

import { sendCommand } from '$lib/transport/ws-client';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
// MOR-1370 (S6b-2): the REAL manifests + the REAL resolution seam, mirroring
// `semantic-scope-display-wiring.component.test.ts`'s "MOR-1365 (S6a)"
// section — the only way to prove the `scope-controls` zone binding and the
// S5/S6-pre subtraction asymmetry, since `useSurfacePlan()` falls back to
// `NO_PLAN` on a standalone mount.
import {
  desktopV2Layout, dualReceiverCockpitLayout,
} from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../../../presentation/workspace/resolution';

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
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
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    scopeControls: {
      mode: 1, edge: 2, span: 3, speed: 1, hold: false, refDb: -5, dual: false, receiver: 0,
      duringTx: false, centerType: 0, vbwNarrow: false, rbw: 0,
    },
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (tags: readonly string[]): Capabilities => ({
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
const el = (id: string) => q<HTMLElement>(`[data-testid="${id}"]`);
function useState(state: ServerState): void {
  h.state = state;
  resetRadioState();
  setRadioState(state);
}

beforeEach(() => {
  useState(liveState());
  h.caps = liveCaps(SCOPE_TAGS);
  h.snapshot = { ...IDLE };
  h.audio = { muted: true, rxEnabled: false, volume: 0 };
  h.audioConnected = false;
  h.guardVisible = false;
  h.listeners.clear();
  vi.mocked(sendCommand).mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

/* ── (a) every control category reaches the shipped command vocabulary ── */

describe('the surface intents reach the shipped scope command vocabulary', () => {
  it('a mode click sends set_scope_mode with the absolute wire ordinal', () => {
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

/* ── (b) MOUNTING CANON: single bare, dual absent ─────────────────────── */

describe('the surface mounts only in the single composition, never in dual', () => {
  it('renders no scope-controls surface for a radio without the scope capability', () => {
    h.caps = liveCaps(NO_SCOPE_TAGS);
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
      .filter((node) => node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });
});

/* ── (d) MOR-1370 (S6b-2) — desktop-v2 REALLY declares the zone; the ──────
   ── cockpit still mounts nothing (canon option (ii), single-only) ────── */

/**
 * `desktopV2Layout` now carries a `scope-controls` zone
 * (`presentation/layouts/desktop-declarations.ts`). Unlike `scopeDisplay`
 * (pure readout, bare in both compositions), `scopeControls` is
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
