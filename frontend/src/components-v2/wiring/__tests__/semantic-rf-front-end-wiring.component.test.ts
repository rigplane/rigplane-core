/**
 * MOR-1306 — the semantic RF-front-end surface wired into
 * `SemanticRadioSurfaces`.
 *
 * `semantic/__tests__/RfFrontEndSurface.test.ts` proves what the surface does
 * with a view model. This file proves what only the composed tree can prove:
 *
 *   (a) ordinary intents reach their OWN mapped command-handler spies, with
 *       none cross-wired to a neighbor — mirroring
 *       `semantic-tx-aux-wiring.component.test.ts`'s own "every intent
 *       reaches its own command-bus handler" section. RF gain and squelch
 *       additionally wrap and execute shipped `makeRfFrontEndHandlers` calls
 *       through mocked transport plus the real command/radio stores and
 *       projector, proving their command-feedback lifecycle end to end;
 *   (b) THE MOUNTING CANON (MOR-1304 ruling): the surface mounts through
 *       `zoned(...)` in the SINGLE composition only, and is ABSENT — zoned or
 *       unzoned — from the DUAL composition, with a view model that actually
 *       carries the group (a fixture that cannot see the surface is the bug
 *       being fixed, not a pass). Mirrors
 *       `semantic-rx-audio-wiring.component.test.ts`'s own pin exactly, per
 *       the ticket brief's option (i);
 *   (c) the default path stays byte-identical for a radio with no RF-front-end
 *       capability at all;
 *   (d) the FLIPPED-value contract between the surface and the toggle wiring
 *       (`RfFrontEndSurface.svelte`'s `onToggle`) reaches the real
 *       `onDigiSelToggle`/`onIpPlusToggle` `(on: boolean)` signature, not the
 *       argument-less vox/comp/mon shape `TxAuxSurface` composes.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine —
 * no `vite.config.ts` edit was needed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import {
  acknowledgeCommand, beginCommand, confirmCommand, failCommand, resetCommandLifecycle,
} from '$lib/stores/commands.svelte';

type TestControlSession = {
  state: 'connected' | 'disconnected' | 'reconnecting'; epoch: number;
};
type TestCommandDelivery = {
  commandId: string; kind: 'transport-sent' | 'ack' | 'response-ok' | 'response-error' | 'error';
  originalEpoch: number; eventEpoch: number; error?: string; cancelled?: boolean;
};
type TestLifecycleDelivery = {
  commandId: string; kind: 'held' | 'superseded' | 'timed-out' | 'failed';
  originalEpoch: number; eventEpoch: number; reason?: string; expiresAt?: number; error?: string;
};

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  txController: null as ManagedAppTxController | null,
  noop: vi.fn(),
  att: vi.fn(),
  pre: vi.fn(),
  rfGain: vi.fn(),
  squelch: vi.fn(),
  digiSel: vi.fn(),
  ipPlus: vi.fn(),
  session: { state: 'connected', epoch: 7 } as TestControlSession,
  sessionListeners: new Set<(next: TestControlSession) => void>(),
  sentCommands: [] as Array<{
    name: string; params: Record<string, unknown>; id: string; originalEpoch: number;
  }>,
  deliveryListeners: new Set<(event: TestCommandDelivery) => void>(),
  lifecycleDeliveryListeners: new Set<(event: TestLifecycleDelivery) => void>(),
  transportSessionListeners: new Set<(next: TestControlSession) => void>(),
}));

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: () => h.session,
  sendCommand(name: string, params: Record<string, unknown>, id?: string) {
    if (id === undefined) throw new Error('radio intent did not supply a command id');
    h.sentCommands.push({ name, params, id, originalEpoch: h.session.epoch });
    return true;
  },
  onCommandDelivery(handler: (event: TestCommandDelivery) => void) {
    h.deliveryListeners.add(handler);
    return () => h.deliveryListeners.delete(handler);
  },
  onCommandLifecycleDelivery(
    handler: (event: TestLifecycleDelivery) => void,
  ) {
    h.lifecycleDeliveryListeners.add(handler);
    return () => h.lifecycleDeliveryListeners.delete(handler);
  },
  onControlSessionTransition(
    handler: (event: TestControlSession) => void,
  ) {
    h.transportSessionListeners.add(handler);
    return () => h.transportSessionListeners.delete(handler);
  },
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; },
    get caps() { return h.caps; },
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
    get controlSession() { return h.session; },
    subscribeControlSession(handler: (next: typeof h.session) => void) {
      h.sessionListeners.add(handler); return () => h.sessionListeners.delete(handler);
    },
    // MOR-1312 slice 12B (rebase fix): the wiring now also hands the adapter
    // a scope-display snapshot (the FIFTH argument). This file tests
    // rfFrontEnd, so this stays on its pre-1312 path regardless of these
    // values.
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
  },
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => h.txController,
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
// Most handlers remain routing spies. RF gain/squelch wrappers below also call
// the shipped handlers through mocked transport and real command/radio stores
// plus the projector; export names/arities remain covered by
// `stub-export-parity.test.ts` and TypeScript.
vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>();
  return {
    ...actual,
    makeVfoHandlers: () => ({
      onVfoSelect: h.noop, onSplitToggle: h.noop, onDualWatchToggle: h.noop,
    }),
    makeVoxHandlers: () => ({
      onVoxToggle: h.noop, onVoxGainChange: h.noop, onAntiVoxGainChange: h.noop, onVoxDelayChange: h.noop,
    }),
    makeTxHandlers: () => ({
      onRfPowerChange: h.noop, onMicGainChange: h.noop, onAtuToggle: h.noop, onAtuTune: h.noop,
      onVoxToggle: h.noop, onCompToggle: h.noop, onCompLevelChange: h.noop, onMonToggle: h.noop,
      onMonLevelChange: h.noop, onDriveGainChange: h.noop,
    }),
    makeRxAudioHandlers: () => ({ onMonitorModeChange: h.noop, onAfLevelChange: h.noop }),
    makeAudioRoutingHandlers: () => ({ onFocusChange: h.noop, onSplitStereoChange: h.noop }),
    // MOR-1304/MOR-1305 — the wiring's module scope also composes the
    // filter/dsp intent vocabularies unconditionally; without stubs here the
    // wiring's `makeFilterHandlers()`/`makeDspHandlers()`/`makeAgcHandlers()`
    // calls throw before this file's own RF-front-end assertions ever run.
    makeModeHandlers: () => ({
      onModInputChange: h.noop, onModeChange: h.noop, onDataModeChange: h.noop,
    }),
    makeFilterHandlers: () => ({
      onFilterChange: h.noop, onFilterWidthChange: h.noop, onFilterShapeChange: h.noop,
      onIfShiftChange: h.noop, onPbtInnerChange: h.noop, onPbtOuterChange: h.noop,
    }),
    makeDspHandlers: () => ({
      onNrModeChange: h.noop, onNrLevelChange: h.noop, onNbToggle: h.noop,
      onNbLevelChange: h.noop, onNbDepthChange: h.noop, onNbWidthChange: h.noop,
      onNotchModeChange: h.noop, onNotchFreqChange: h.noop,
      onManualNotchWidthChange: h.noop, onAgcTimeChange: h.noop,
    }),
    makeAgcHandlers: () => ({ onAgcModeChange: h.noop }),
    makeRfFrontEndHandlers: () => {
      const shipped = actual.makeRfFrontEndHandlers();
      return {
        onAttChange: h.att,
        onPreChange: h.pre,
        onRfGainChange: (level: number) => {
          h.rfGain(level);
          shipped.onRfGainChange(level);
        },
        onSquelchChange: (level: number) => {
          h.squelch(level);
          shipped.onSquelchChange(level);
        },
        onDigiSelToggle: h.digiSel,
        onIpPlusToggle: h.ipPlus,
      };
    },
    // MOR-1307 slice 7B: the band-select intent the band surface composes.
    // This fixture declares no band capability, so it is never reachable —
    // same stand-in role as the noop handlers above.
    makeBandHandlers: () => ({ onBandSelect: h.noop }),
    // MOR-1309 slice 8C: the wiring's module scope also composes the antenna
    // intent vocabulary unconditionally; without a stub here the wiring's
    // `makeAntennaHandlers()` call throws before this file's own RF-front-end
    // assertions ever run. This fixture declares no antenna capability, so
    // none of these is reachable — same stand-in role as `makeBandHandlers`.
    makeAntennaHandlers: () => ({ onSelectAnt1: h.noop, onSelectAnt2: h.noop, onToggleRxAnt: h.noop }),
    // MOR-1308 — the wiring's module scope also composes the RIT/XIT and scan
    // intent vocabularies unconditionally; without stubs here the wiring's
    // `makeRitXitHandlers()`/`makeScanHandlers()` calls throw before this
    // file's own RF-front-end assertions ever run.
    makeRitXitHandlers: () => ({
      onRitToggle: h.noop, onXitToggle: h.noop, onRitOffsetChange: h.noop,
      onXitOffsetChange: h.noop, onClear: h.noop,
    }),
    makeScanHandlers: () => ({
      onScanStart: h.noop, onScanStop: h.noop, onDfSpanChange: h.noop, onResumeChange: h.noop,
    }),
    // MOR-1310 slice 9B — the wiring's module scope also composes the CW keyer
    // intent vocabulary unconditionally; without a stub here the wiring's
    // `makeCwPanelHandlers()` call throws before this file's own RF-front-end
    // assertions ever run. This fixture declares no cwKeyer capability, so
    // none of these is reachable — same stand-in role as `makeRitXitHandlers`.
    makeCwPanelHandlers: () => ({
      onKeySpeedChange: h.noop, onCwPitchChange: h.noop, onBreakInDelayChange: h.noop,
      onBreakInModeChange: h.noop, onApfChange: h.noop, onTwinPeakToggle: h.noop,
      onReversePaddleToggle: h.noop,
    }),
    // MOR-1311 slice 11B: the scope-toolbar/popover intent vocabulary.
    makeScopeControlsHandlers: () => ({
      onModeChange: h.noop, onEdgeChange: h.noop, onSpanChange: h.noop, onSpeedChange: h.noop,
      onHoldChange: h.noop, onRefChange: h.noop, onDualChange: h.noop, onReceiverChange: h.noop,
      onDuringTxChange: h.noop, onCenterTypeChange: h.noop, onVbwChange: h.noop, onRbwChange: h.noop,
    }),
  };
});

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
// MOR-1366 (S7), N1 fold (verify-MOR-1365 ruling item 3): the REAL manifest +
// the REAL resolution seam, mirroring `semantic-scope-display-wiring
// .component.test.ts`'s S6a context-injection recipe — the only way to prove
// the `rf-front-end` zone binding, since `useSurfacePlan()` falls back to
// `NO_PLAN` on a standalone mount.
import { desktopV2Layout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import { resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan } from '../../../presentation/workspace/resolution';

const fresh = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
  lastObservedMonotonic: 5,
};
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

/** Every rfFrontEnd raw field the MOR-1292/1293 adapter reads, all observed fresh. */
const RF_FRONT_END_STATE = { preamp: 1, att: 6, rfGain: 0.8, squelch: 0.1, digisel: false, ipplus: false };
const RF_FRONT_END_PATHS = ['preamp', 'att', 'rfGain', 'squelch', 'digisel', 'ipplus'];

function liveState(withRfFrontEnd: boolean): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    if (withRfFrontEnd) paths.push(...RF_FRONT_END_PATHS.map((p) => `${rx}.${p}`));
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
    ...(withRfFrontEnd ? RF_FRONT_END_STATE : {}),
  });
  return {
    stateContractVersion: 1, providerGeneration: 3,
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

// MOR-1447 leg 2: `rfSqlControlModel` defaults to omitted/'separate' — only the
// combined-knob describe block below passes 'combined' explicitly, so every
// pre-existing test in this file keeps exercising the unchanged two-slider path.
const liveCaps = (withRfFrontEnd: boolean, rfSqlControlModel?: 'separate' | 'combined'): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 3,
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: withRfFrontEnd
    ? ['audio', 'tx', 'dual_rx', 'preamp', 'attenuator', 'rf_gain', 'squelch', 'digisel', 'ip_plus']
    : ['audio', 'tx', 'dual_rx'],
  preValues: [0, 1, 2], attValues: [0, 6, 12, 18],
  receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
  ...(rfSqlControlModel !== undefined ? { rfSqlControlModel } : {}),
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

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
const el = (id: string) => q<HTMLElement>(`[data-testid="rf-front-end-${id}"]`);
let acceptedState: ServerState;

function acceptedStoreState(state: ServerState): ServerState {
  const receiver = (value: ServerState['main']) => ({
    ...value,
    dataMode: value.dataMode ?? 0,
    sMeter: value.sMeter ?? 0,
    att: value.att ?? 0,
    preamp: value.preamp ?? 0,
    nb: value.nb ?? false,
    nr: value.nr ?? false,
    afLevel: value.afLevel ?? 0.5,
    rfGain: value.rfGain ?? 0.8,
    squelch: value.squelch ?? 0.1,
  });
  return {
    ...state,
    revision: state.revision ?? 1,
    stateRevision: state.stateRevision ?? 1,
    freshnessRevision: state.freshnessRevision ?? 1,
    observationSeq: state.observationSeq ?? 1,
    updatedAt: state.updatedAt ?? '2026-09-06T00:00:00.000Z',
    tunerStatus: state.tunerStatus ?? 0,
    connection: state.connection
      ?? { rigConnected: true, radioReady: true, controlConnected: true },
    main: receiver(state.main),
    sub: receiver(state.sub ?? state.main),
  };
}

function observedMainLevels(
  state: ServerState,
  levels: Readonly<{ rfGain?: number; squelch?: number }>,
  marker: number,
): ServerState {
  const next = {
    ...state,
    main: { ...state.main, ...levels },
    fieldStatus: {
      ...state.fieldStatus,
      ...(levels.rfGain === undefined ? {} : {
        'main.rfGain': { ...state.fieldStatus?.['main.rfGain'], lastObservedMonotonic: marker },
      }),
      ...(levels.squelch === undefined ? {} : {
        'main.squelch': { ...state.fieldStatus?.['main.squelch'], lastObservedMonotonic: marker },
      }),
    },
  } as ServerState;
  h.state = next;
  acceptedState = {
    ...acceptedState,
    revision: (acceptedState.revision ?? 0) + 1,
    stateRevision: (acceptedState.stateRevision ?? 0) + 1,
    freshnessRevision: (acceptedState.freshnessRevision ?? 0) + 1,
    observationSeq: (acceptedState.observationSeq ?? 0) + 1,
    updatedAt: `2026-09-06T00:00:0${marker}.000Z`,
    main: { ...acceptedState.main, ...levels },
    fieldStatus: next.fieldStatus,
  };
  expect(setRadioState(acceptedState)).toBe(true);
  return next;
}

function acknowledgeSent(command: (typeof h.sentCommands)[number]): void {
  for (const listener of h.deliveryListeners) listener({
    commandId: command.id,
    kind: 'ack',
    originalEpoch: command.originalEpoch,
    eventEpoch: command.originalEpoch,
  });
}

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  h.state = liveState(true);
  h.caps = liveCaps(true);
  h.session = { state: 'connected', epoch: 7 };
  h.sessionListeners.clear();
  h.sentCommands.length = 0;
  resetCommandLifecycle();
  resetRadioState();
  clearCapabilities();
  expect(setCapabilities(h.caps as Capabilities)).toBe(true);
  acceptedState = acceptedStoreState(h.state as ServerState);
  expect(setRadioState(acceptedState)).toBe(true);
  for (const value of Object.values(h)) {
    if (typeof value === 'function' && 'mockReset' in value) (value as ReturnType<typeof vi.fn>).mockReset();
  }
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  expect(txHarness.listenerCount()).toBe(0);
  expect(txHarness.trace()).toEqual([]);
  expect(h.sessionListeners.size).toBe(0);
  resetCommandLifecycle();
  resetRadioState();
  clearCapabilities();
  document.body.innerHTML = '';
});

/* ── (a)+(d) each intent reaches its own mapped handler ─────────── */

describe('every rfFrontEnd intent reaches its own command-bus handler, none cross-wired', () => {
  const ALL = [h.att, h.pre, h.rfGain, h.squelch, h.digiSel, h.ipPlus];

  it('routes the preamp choice to onPreChange, verbatim', () => {
    render();
    el('preamp-2')!.click();
    flushSync();
    expect(h.pre).toHaveBeenCalledExactlyOnceWith(2);
    for (const other of ALL.filter((s) => s !== h.pre)) expect(other).not.toHaveBeenCalled();
  });

  it('routes the attenuator choice to onAttChange, verbatim', () => {
    render();
    el('attenuator-18')!.click();
    flushSync();
    expect(h.att).toHaveBeenCalledExactlyOnceWith(18);
    for (const other of ALL.filter((s) => s !== h.att)) expect(other).not.toHaveBeenCalled();
  });

  // MOR-1447: the surface reports the radio's normalized 0..1 reading (no
  // rescale, per `RfFrontEndSurface.svelte`'s own contract), but the REAL
  // `onRfGainChange` dispatches a raw 0-255 wire integer and refuses
  // anything else — so the wiring must convert on the way through. This was
  // the input-snaps-to-0%-or-100% regression: an unconverted intermediate
  // drag silently failed the real handler's integer guard.
  // MOR-1447 verifier follow-up: literal expected value, not the same
  // `Math.round(...)` formula the production seam itself uses — a mutation
  // that swapped `Math.round` for `Math.trunc`/`Math.floor` would still pass
  // a formula-mirroring assertion (140.25 truncates to 140 too). 0.55*255 is
  // pinned as the literal 140.
  it('routes the RF-gain slider to onRfGainChange, converted to the raw 0-255 wire level', () => {
    render();
    const input = el('rfGain')!.querySelector('input')!;
    input.value = '0.55';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(140);
    for (const other of ALL.filter((s) => s !== h.rfGain)) expect(other).not.toHaveBeenCalled();
  });

  it('routes the squelch slider to onSquelchChange, converted to the raw 0-255 wire level', () => {
    render();
    const input = el('squelch')!.querySelector('input')!;
    input.value = '0.2';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.squelch).toHaveBeenCalledExactlyOnceWith(51);
    for (const other of ALL.filter((s) => s !== h.squelch)) expect(other).not.toHaveBeenCalled();
  });

  // Rounding-rule pin (MOR-1447 leg 1 verifier follow-up item a): 0.5 is the
  // exact tie case where `Math.round` (128) and a truncating conversion
  // (127) diverge — 0.5*255 = 127.5. The literal 128 is the ONLY value that
  // proves `Math.round` specifically, not merely "some" integer conversion.
  it('rounds a 0.5 drag to the raw wire level 128, not the truncated 127', () => {
    render();
    const input = el('rfGain')!.querySelector('input')!;
    input.value = '0.5';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(128);
  });

  // (d): `onDigiSelToggle`/`onIpPlusToggle` take an explicit `on: boolean` —
  // the wiring must pass the FLIPPED value the surface computed, not call
  // with no argument (the vox/comp/mon toggle shape) and not the CURRENT value.
  it('routes DIGI-SEL to onDigiSelToggle with the FLIPPED boolean', () => {
    render();
    el('digiSel')!.click();
    flushSync();
    expect(h.digiSel).toHaveBeenCalledExactlyOnceWith(true);
    for (const other of ALL.filter((s) => s !== h.digiSel)) expect(other).not.toHaveBeenCalled();
  });

  it('routes IP+ to onIpPlusToggle with the FLIPPED boolean', () => {
    render();
    el('ipPlus')!.click();
    flushSync();
    expect(h.ipPlus).toHaveBeenCalledExactlyOnceWith(true);
    for (const other of ALL.filter((s) => s !== h.ipPlus)) expect(other).not.toHaveBeenCalled();
  });
});

/* ── (b) THE MOUNTING CANON ──────────────────────────────────────── */

describe('the surface mounts only when the view model carries the group', () => {
  it('renders no rf-front-end surface for a radio with no RF-front-end capability', () => {
    h.state = liveState(false);
    h.caps = liveCaps(false);
    render();
    expect(el('surface')).toBeNull();
    expect(target.innerHTML).not.toContain('rf-front-end');
  });

  it('renders it bare in the single composition, outside every zone', () => {
    render();
    const surface = el('surface')!;
    expect(surface).not.toBeNull();
    expect(surface.closest('[data-zone-id]')).toBeNull();
  });

  /**
   * MOR-1304 mounting canon, applied per the ticket brief's option (i): a
   * control-bearing surface with no declared cockpit zone must be ABSENT from
   * the dual composition, not mounted bare. Caps here positively declare
   * every rfFrontEnd sub-capability, so this is not the `mainSubCaps()` blind
   * spot the MOR-1304 verify round documented for 4B — the group genuinely IS
   * present, and the surface must still not appear.
   */
  it('renders NO rf-front-end surface in the dual composition, zoned or unzoned', () => {
    render({ strips: 'dual' });
    expect(el('surface')).toBeNull();
    expect(target.innerHTML).not.toContain('rf-front-end');
  });

  it('leaves the cockpit with no focusable control outside a declared zone', () => {
    render({ strips: 'dual' });
    const outside = [...target.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')]
      .filter((node) => node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });

  it('never changes with the App TX authority or the raw transmit bit', () => {
    render();
    const before = el('surface')!.outerHTML;
    txHarness.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on' });
    h.state = liveState(true);
    (h.state as unknown as { ptt: boolean }).ptt = true;
    flushSync();
    expect(el('surface')!.outerHTML).toBe(before);
  });
});

/* ── MOR-1447 leg 2: the profile-declared combined RF/SQL knob ──────────
 * IC-7300 is the live gate radio for `rf_sql_control_model = "combined"`
 * (`rigs/ic7300.toml`). These cases pin the dispatch/value contract this
 * wiring must honor once `runtime.caps.rfSqlControlModel` reads "combined":
 * ONE knob position maps to BOTH `set_rf_gain`/`set_squelch`, over the exact
 * same normalized→raw-0-255 seam (`RF_FRONT_END_LEVEL_INTENT`) leg 1 fixed —
 * no new conversion path, no vendor branch. */

describe('MOR-1447 leg 2: the combined RF/SQL knob, when the profile declares it', () => {
  const ALL = [h.att, h.pre, h.rfGain, h.squelch, h.digiSel, h.ipPlus];

  it('renders ONE rf-sql control, not the two separate sliders', () => {
    h.caps = liveCaps(true, 'combined');
    render();
    expect(el('rf-sql')).not.toBeNull();
    expect(el('rfGain')).toBeNull();
    expect(el('squelch')).toBeNull();
    expect(el('rf-sql')!.dataset.feedbackIntegration).toBe('command-feedback');
  });

  it('fails closed through explicit null when the actual control session is disconnected', () => {
    h.caps = liveCaps(true, 'combined');
    h.session = { state: 'disconnected', epoch: 8 };
    render();
    const group = el('rf-sql')!;
    expect(group.dataset.feedbackIntegration).toBe('authority-unresolved');
    expect(group.querySelector('input')!.disabled).toBe(true);
    expect(group.textContent).toContain('?');
  });

  it('projects real lifecycle phases and independent terminal outcomes into the mounted pair', () => {
    h.caps = liveCaps(true, 'combined');
    const rf = beginCommand({
      id: 'mounted-rf', name: 'set_rf_gain', params: { level: 128, receiver: 0 }, originalEpoch: 7,
    });
    const sql = beginCommand({
      id: 'mounted-sql', name: 'set_squelch', params: { level: 51, receiver: 0 }, originalEpoch: 7,
    });
    rf.providerGeneration = 3; sql.providerGeneration = 3;
    render();
    expect(el('rf-sql')!.dataset.rfCommandPhase).toBe('submitted');
    expect(el('rf-sql')!.dataset.sqlCommandPhase).toBe('submitted');
    acknowledgeCommand(rf.id, 7, 7); acknowledgeCommand(sql.id, 7, 7); flushSync();
    expect(el('rf-sql')!.dataset.rfCommandPhase).toBe('awaiting-confirmation');
    confirmCommand(rf.id, 7, 7); failCommand(sql.id, 7, 7, 'denied'); flushSync();
    expect(el('rf-sql')!.dataset.rfCommandPhase).toBe('confirmed');
    expect(el('rf-sql')!.dataset.sqlCommandPhase).toBe('failed');
    expect(el('rf-sql')!.querySelectorAll('[data-control-feedback-status]')).toHaveLength(2);
  });

  it('keeps rendering the two separate sliders when the profile omits the declaration', () => {
    h.caps = liveCaps(true); // no rfSqlControlModel -> defaults to 'separate'
    render();
    expect(el('rf-sql')).toBeNull();
    expect(el('rfGain')).not.toBeNull();
    expect(el('squelch')).not.toBeNull();
    expect(el('rfGain')!.dataset.feedbackIntegration).toBe('command-feedback');
    expect(el('squelch')!.dataset.feedbackIntegration).toBe('command-feedback');
  });

  it('fails both separate controls closed on disconnect and recovers from fresh connected authority', () => {
    render();
    const rfInput = el('rfGain')!.querySelector('input')!;
    const sqlInput = el('squelch')!.querySelector('input')!;
    expect([rfInput.disabled, sqlInput.disabled]).toEqual([false, false]);

    h.session = { state: 'disconnected', epoch: 8 };
    for (const listener of h.sessionListeners) listener(h.session);
    flushSync();
    expect([rfInput.disabled, sqlInput.disabled]).toEqual([true, true]);
    expect(el('rfGain')!.textContent).toContain('?');
    expect(el('squelch')!.textContent).toContain('?');

    h.session = { state: 'connected', epoch: 9 };
    for (const listener of h.sessionListeners) listener(h.session);
    flushSync();
    expect([rfInput.disabled, sqlInput.disabled]).toEqual([false, false]);
    expect(rfInput.valueAsNumber).toBe(0.8);
    expect(sqlInput.valueAsNumber).toBe(0.1);
    expect(h.rfGain).not.toHaveBeenCalled();
    expect(h.squelch).not.toHaveBeenCalled();
  });

  it('projects independent lifecycle outcomes into the two separate controls', () => {
    const rf = beginCommand({
      id: 'separate-rf', name: 'set_rf_gain', params: { level: 128, receiver: 0 }, originalEpoch: 7,
    });
    const sql = beginCommand({
      id: 'separate-sql', name: 'set_squelch', params: { level: 51, receiver: 0 }, originalEpoch: 7,
    });
    rf.providerGeneration = 3;
    sql.providerGeneration = 3;
    render();
    expect(el('rfGain')!.dataset.commandPhase).toBe('submitted');
    expect(el('squelch')!.dataset.commandPhase).toBe('submitted');
    expect(el('rfGain')!.querySelector('input')!.valueAsNumber).toBe(128 / 255);
    expect(el('squelch')!.querySelector('input')!.valueAsNumber).toBe(51 / 255);
    acknowledgeCommand(rf.id, 7, 7);
    acknowledgeCommand(sql.id, 7, 7);
    flushSync();
    expect(el('rfGain')!.dataset.commandPhase).toBe('awaiting-confirmation');
    expect(el('squelch')!.dataset.commandPhase).toBe('awaiting-confirmation');
    confirmCommand(rf.id, 7, 7);
    failCommand(sql.id, 7, 7, 'denied');
    flushSync();
    expect(el('rfGain')!.dataset.commandPhase).toBe('confirmed');
    expect(el('squelch')!.dataset.commandPhase).toBe('failed');
    expect(el('rfGain')!.querySelectorAll('[data-control-feedback-status]')).toHaveLength(1);
    expect(el('squelch')!.querySelectorAll('[data-control-feedback-status]')).toHaveLength(1);
    expect(el('squelch')!.textContent).toContain('denied');
  });

  it('retires a native RF draft after real raw-command confirmation and follows later truth while SQL is pending', () => {
    render();
    const rfInput = el('rfGain')!.querySelector('input')!;
    const sqlInput = el('squelch')!.querySelector('input')!;

    rfInput.value = '0.7';
    rfInput.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(179);
    expect(h.sentCommands).toHaveLength(1);
    const rfCommand = h.sentCommands[0];
    expect(rfCommand).toMatchObject({
      name: 'set_rf_gain', params: { level: 179, receiver: 0 }, originalEpoch: 7,
    });
    expect(el('rfGain')!.dataset.commandPhase).toBe('submitted');

    acknowledgeSent(rfCommand);
    flushSync();
    expect(el('rfGain')!.dataset.commandPhase).toBe('awaiting-confirmation');
    let state = observedMainLevels(h.state as ServerState, { rfGain: 179 / 255 }, 6);
    flushSync();
    expect(el('rfGain')!.dataset.commandPhase).toBe('confirmed');

    sqlInput.value = '0.6';
    sqlInput.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.squelch).toHaveBeenCalledExactlyOnceWith(153);
    expect(h.sentCommands).toHaveLength(2);
    const sqlCommand = h.sentCommands[1];
    expect(sqlCommand).toMatchObject({
      name: 'set_squelch', params: { level: 153, receiver: 0 }, originalEpoch: 7,
    });

    state = observedMainLevels(state, { rfGain: 204 / 255 }, 7);
    acknowledgeSent(sqlCommand);
    flushSync();
    expect(el('rfGain')!.querySelector('input')!.valueAsNumber).toBe(0.8);
    expect(el('rfGain')!.querySelector('output')!.textContent).toBe('80%');
    expect(el('squelch')!.dataset.commandPhase).toBe('awaiting-confirmation');
    expect(el('squelch')!.querySelector('input')!.valueAsNumber).toBe(0.6);

    observedMainLevels(state, { squelch: 153 / 255 }, 8);
    flushSync();
    expect(el('squelch')!.dataset.commandPhase).toBe('confirmed');
    expect(h.rfGain).toHaveBeenCalledTimes(1);
    expect(h.squelch).toHaveBeenCalledTimes(1);
  });

  it('routes separate endpoint requests once through the unchanged raw conversion seam', () => {
    render();
    const rfInput = el('rfGain')!.querySelector('input')!;
    const sqlInput = el('squelch')!.querySelector('input')!;
    rfInput.value = '0';
    rfInput.dispatchEvent(new Event('input', { bubbles: true }));
    sqlInput.value = '1';
    sqlInput.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(0);
    expect(h.squelch).toHaveBeenCalledExactlyOnceWith(255);
  });

  // Hard left: RF min, SQL min — both converted to the raw 0-255 wire level.
  it('routes a hard-left drag to RF min / SQL min, both raw wire integers', () => {
    h.caps = liveCaps(true, 'combined');
    render();
    const input = el('rf-sql')!.querySelector('input')!;
    input.value = '0';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(0);
    expect(h.squelch).toHaveBeenCalledExactlyOnceWith(0);
    for (const other of ALL.filter((s) => s !== h.rfGain && s !== h.squelch)) {
      expect(other).not.toHaveBeenCalled();
    }
  });

  // Center (inside the dead zone): RF max, SQL min — the hardware's default
  // "everything wide open, no squelch" rest position.
  it('routes the knob center to RF max / SQL min', () => {
    h.caps = liveCaps(true, 'combined');
    render();
    const input = el('rf-sql')!.querySelector('input')!;
    input.value = '0.5';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(255);
    expect(h.squelch).toHaveBeenCalledExactlyOnceWith(0);
  });

  // Hard right: SQL max, RF forced to max too (owner semantics: "hard right
  // = SQL max (RF max)").
  it('routes a hard-right drag to SQL max / RF max, both raw wire integers', () => {
    h.caps = liveCaps(true, 'combined');
    render();
    const input = el('rf-sql')!.querySelector('input')!;
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(255);
    expect(h.squelch).toHaveBeenCalledExactlyOnceWith(255);
  });

  // Left-of-center: RF sweeps, SQL stays pinned at min. Literal expected
  // values (not re-derived from the mapping formula) — 0.23 lands exactly
  // halfway across the left leg (`dualParamValuesFromNormX`'s own math,
  // ported from `DualParamRenderer`/`primitives/scalar/value-control-core.ts`).
  it('sweeps RF only on a left-of-center drag, leaving SQL pinned at min', () => {
    h.caps = liveCaps(true, 'combined');
    render();
    const input = el('rf-sql')!.querySelector('input')!;
    input.value = '0.23';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(128);
    expect(h.squelch).toHaveBeenCalledExactlyOnceWith(0);
  });

  // Right-of-center: RF pinned at max, SQL sweeps.
  it('sweeps SQL only on a right-of-center drag, leaving RF pinned at max', () => {
    h.caps = liveCaps(true, 'combined');
    render();
    const input = el('rf-sql')!.querySelector('input')!;
    input.value = '0.77';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(255);
    expect(h.squelch).toHaveBeenCalledExactlyOnceWith(128);
  });

  // Readback projection: SQL above min projects the knob to the right leg
  // (RF forced to max) — the one honest reading when the physical knob
  // cannot express "RF below max AND SQL above min" at once.
  it('positions the knob on the right leg when SQL reads above min, on initial render', () => {
    h.caps = liveCaps(true, 'combined');
    h.state = liveState(true);
    (h.state as unknown as { main: { rfGain: number; squelch: number } }).main.rfGain = 0.8196078431372549;
    (h.state as unknown as { main: { rfGain: number; squelch: number } }).main.squelch = 0.2;
    render();
    const input = el('rf-sql')!.querySelector('input')!;
    expect(input.valueAsNumber).toBeCloseTo(0.632, 3);
  });

  // Verifier follow-up R1, pinned end-to-end through the real
  // `RF_FRONT_END_LEVEL_INTENT` seam (not just the pure surface): an
  // unconditional pair-emit would double-dispatch on every drag, including
  // ones where one half is already at its target — the queue-lag/"Commander
  // stopped" hazard shape on the live serial IC-7300 gate radio.
  it('a drag landing on the ALREADY-CONFIRMED position dispatches NEITHER set_rf_gain nor set_squelch', () => {
    h.caps = liveCaps(true, 'combined');
    h.state = liveState(true);
    // The knob's own "center, at rest" position: RF max, SQL min.
    (h.state as unknown as { main: { rfGain: number; squelch: number } }).main.rfGain = 1;
    (h.state as unknown as { main: { rfGain: number; squelch: number } }).main.squelch = 0;
    render();
    const input = el('rf-sql')!.querySelector('input')!;
    input.value = '0.5';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).not.toHaveBeenCalled();
    expect(h.squelch).not.toHaveBeenCalled();
  });

  it('a drag that only moves RF dispatches set_rf_gain ONLY — SQL is already at its target, no redundant set_squelch', () => {
    h.caps = liveCaps(true, 'combined');
    h.state = liveState(true);
    (h.state as unknown as { main: { rfGain: number; squelch: number } }).main.rfGain = 1;
    (h.state as unknown as { main: { rfGain: number; squelch: number } }).main.squelch = 0;
    render();
    const input = el('rf-sql')!.querySelector('input')!;
    input.value = '0'; // hard left: RF -> 0 (changes), SQL stays 0 (unchanged)
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.rfGain).toHaveBeenCalledExactlyOnceWith(0);
    expect(h.squelch).not.toHaveBeenCalled();
  });
});

/* ── MOR-1366 (S7), N1 fold — desktop-v2's REAL rf-front-end zone binds ─── */

describe('desktop-v2 declares a REAL rf-front-end zone (MOR-1366, S7)', () => {
  it('binds the rf-front-end zone id against desktop-v2\'s real plan', () => {
    const plan = resolveSurfacePlan(desktopV2Layout, readWorkspace({ version: 1 }).workspace);
    render({ strips: 'single' }, plan);
    expect(el('surface')!.closest('[data-zone-id="rf-front-end"]')).not.toBeNull();
  });
});
