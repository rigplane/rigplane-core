/** Semantic RX/TX surfaces consume one App-root managed TX projection. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { setLocale, _resetLocale } from '$lib/i18n/store.svelte';

type Snapshot = {
  phase: string; intent: string | null; radioTx: string; txRisk: string;
  fault: string | null;
  fresh?: boolean;
};

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  listeners: new Set<(next: unknown) => void>(),
  start: vi.fn(),
  release: vi.fn(),
  selectVfo: vi.fn(),
  splitToggle: vi.fn(),
  dualWatchToggle: vi.fn(),
  setLan: vi.fn(),
  dismissWarning: vi.fn(),
  modInputGuard: { visible: false, sourceLabel: null } as { visible: boolean; sourceLabel: string | null },
  subscribeCalls: 0,
  unsubscribeCalls: 0,
  /** MOR-1265: stand-in for every txAux intent. These fixtures declare no
   *  txAux capability and carry no txAux state, so the MOR-1244 evidence gate
   *  omits the group and none of these is ever reachable here. */
  txAuxNoop: vi.fn(),
  /** MOR-1305: same stand-in role for every dsp intent — no dsp capability
   *  or state here either, so the group is absent and these are unreachable. */
  dspNoop: vi.fn(),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    // MOR-1279 slice 3B: the wiring now also hands the adapter an
    // App-owned RX-audio snapshot (the FOURTH argument). Muted with no
    // browser stream keeps every fixture below on its pre-1279 path.
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
    // MOR-1312 slice 12B: the wiring now also hands the adapter a
    // scope-display snapshot (the FIFTH argument). Every fixture below
    // declares no scope capability, so this stays on its pre-1312 path
    // regardless of these values.
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
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => {
  const controller = Object.freeze({
    snapshot: () => ({ ...(h.snapshot as object), fresh: (h.snapshot as Snapshot).fresh ?? true, remainingMs: null }),
    subscribe: (listener: (next: unknown) => void) => {
      h.subscribeCalls += 1;
      const managed = (next: unknown) => listener({
        ...(next as object), fresh: (next as Snapshot).fresh ?? true, remainingMs: null,
      });
      h.listeners.add(managed);
      return () => { h.unsubscribeCalls += 1; h.listeners.delete(managed); };
    },
    pttOn: vi.fn(),
    pttOff: vi.fn(),
    transmitOn: h.start,
    forceOff: h.release,
  });
  return { getManagedAppTxController: () => controller };
});
// The MOR-617 preflight's own adapter — stubbed so the test drives the
// warning's real trigger condition (`visible`) without a live TX guard.
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => h.modInputGuard,
  getModInputTxGuardHandlers: () => ({ onSetLan: h.setLan, onDismiss: h.dismissWarning }),
}));
vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>();
  return {
    ...actual,
    makeVfoHandlers: () => ({
      onVfoSelect: h.selectVfo,
      onSplitToggle: h.splitToggle,
      onDualWatchToggle: h.dualWatchToggle,
    }),
    // MOR-1265 — the wiring now also composes the txAux intent vocabulary.
    makeVoxHandlers: () => ({
      onVoxToggle: h.txAuxNoop, onVoxGainChange: h.txAuxNoop,
      onAntiVoxGainChange: h.txAuxNoop, onVoxDelayChange: h.txAuxNoop,
    }),
    makeTxHandlers: () => ({
      onRfPowerChange: h.txAuxNoop, onMicGainChange: h.txAuxNoop, onAtuToggle: h.txAuxNoop,
      onAtuTune: h.txAuxNoop, onVoxToggle: h.txAuxNoop, onCompToggle: h.txAuxNoop,
      onCompLevelChange: h.txAuxNoop, onMonToggle: h.txAuxNoop,
      onMonLevelChange: h.txAuxNoop, onDriveGainChange: h.txAuxNoop,
    }),
    // MOR-1279 slice 3B: the RX-audio intent vocabulary.
    makeRxAudioHandlers: () => ({ onMonitorModeChange: h.txAuxNoop, onAfLevelChange: h.txAuxNoop }),
    // MOR-1310 slice 9B: the semantic CW-keyer surface's setting intents.
    makeCwPanelHandlers: () => ({
      onKeySpeedChange: h.txAuxNoop, onCwPitchChange: h.txAuxNoop, onBreakInDelayChange: h.txAuxNoop,
      onBreakInModeChange: h.txAuxNoop, onApfChange: h.txAuxNoop, onTwinPeakToggle: h.txAuxNoop,
      onReversePaddleToggle: h.txAuxNoop,
    }),
    makeAudioRoutingHandlers: () => ({ onFocusChange: h.txAuxNoop, onSplitStereoChange: h.txAuxNoop }),
    // MOR-1304 — the wiring now also composes the modeFilter/filterPassband
    // intent vocabulary; `makeModeHandlers` is composed at both call sites
    // (rxAudio's MOD-input remedy and filterIntents), so the stub carries both.
    makeModeHandlers: () => ({
      onModInputChange: h.txAuxNoop, onModeChange: h.txAuxNoop, onDataModeChange: h.txAuxNoop,
    }),
    makeFilterHandlers: () => ({
      onFilterChange: h.txAuxNoop, onFilterWidthChange: h.txAuxNoop, onFilterShapeChange: h.txAuxNoop,
      onIfShiftChange: h.txAuxNoop, onPbtInnerChange: h.txAuxNoop, onPbtOuterChange: h.txAuxNoop,
    }),
    // MOR-1305 — the wiring now also composes the dsp intent vocabulary.
    makeDspHandlers: () => ({
      onNrModeChange: h.dspNoop, onNrLevelChange: h.dspNoop, onNbToggle: h.dspNoop,
      onNbLevelChange: h.dspNoop, onNbDepthChange: h.dspNoop, onNbWidthChange: h.dspNoop,
      onNotchModeChange: h.dspNoop, onNotchFreqChange: h.dspNoop,
      onManualNotchWidthChange: h.dspNoop, onAgcTimeChange: h.dspNoop,
    }),
    makeAgcHandlers: () => ({ onAgcModeChange: h.dspNoop }),
    // MOR-1306 slice 6B: the RF-front-end intent vocabulary.
    makeRfFrontEndHandlers: () => ({
      onAttChange: h.txAuxNoop, onPreChange: h.txAuxNoop, onRfGainChange: h.txAuxNoop,
      onSquelchChange: h.txAuxNoop, onDigiSelToggle: h.txAuxNoop, onIpPlusToggle: h.txAuxNoop,
    }),
    // MOR-1307 slice 7B: the band-select intent the band surface composes.
    makeBandHandlers: () => ({ onBandSelect: h.txAuxNoop }),
    // MOR-1309 slice 8C: the antenna intent vocabulary.
    makeAntennaHandlers: () => ({ onSelectAnt1: h.txAuxNoop, onSelectAnt2: h.txAuxNoop, onToggleRxAnt: h.txAuxNoop }),
    // MOR-1308 slice 8B: the RIT/XIT and scan intent vocabularies.
    makeRitXitHandlers: () => ({
      onRitToggle: h.txAuxNoop, onXitToggle: h.txAuxNoop, onRitOffsetChange: h.txAuxNoop,
      onXitOffsetChange: h.txAuxNoop, onClear: h.txAuxNoop,
    }),
    makeScanHandlers: () => ({
      onScanStart: h.txAuxNoop, onScanStop: h.txAuxNoop, onDfSpanChange: h.txAuxNoop,
      onResumeChange: h.txAuxNoop,
    }),
    // MOR-1311 slice 11B: the scope-toolbar/popover intent vocabulary.
    makeScopeControlsHandlers: () => ({
      onModeChange: h.txAuxNoop, onEdgeChange: h.txAuxNoop, onSpanChange: h.txAuxNoop,
      onSpeedChange: h.txAuxNoop, onHoldChange: h.txAuxNoop, onRefChange: h.txAuxNoop,
      onDualChange: h.txAuxNoop, onReceiverChange: h.txAuxNoop, onDuringTxChange: h.txAuxNoop,
      onCenterTypeChange: h.txAuxNoop, onVbwChange: h.txAuxNoop, onRbwChange: h.txAuxNoop,
    }),
  };
});

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

const IDLE: Snapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null,
};

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

function liveState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'dual_rx'], receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(props: { strips?: 'single' | 'dual' } = {}): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props });
  flushSync();
}

/** Push a new authority snapshot exactly as the real controller would. */
function push(next: Partial<Snapshot>): void {
  h.snapshot = { ...(h.snapshot as Snapshot), ...next };
  for (const listener of h.listeners) listener(h.snapshot);
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;

beforeEach(() => {
  h.state = liveState();
  h.caps = liveCaps();
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  h.subscribeCalls = 0;
  h.unsubscribeCalls = 0;
  h.start.mockReset();
  h.release.mockReset();
  h.selectVfo = vi.fn();
  h.splitToggle = vi.fn();
  h.dualWatchToggle = vi.fn();
  h.setLan = vi.fn();
  h.dismissWarning = vi.fn();
  h.modInputGuard = { visible: false, sourceLabel: null };
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

describe('the surfaces render from the live adapter output', () => {
  it('mounts both semantic surfaces with the derived view model', () => {
    render();
    expect(q('[data-testid="vfo-surface"]')).not.toBeNull();
    expect(q('[data-testid="rx-tx-surface"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-vfo-tile]')).toHaveLength(4);
    expect(q('[data-testid="rx-tx-target"]')?.dataset.target).toBe('known');
  });

  // MUTATION KILLED: flipping the `strips` default from 'single' to 'dual'
  // (MOR-1067 review cycle 1, F4). The default is what every existing consumer
  // gets — RadioLayout.svelte mounts this with no `strips` prop — so a flipped
  // default silently re-composes sdr-test/desktop into per-receiver channel
  // strips. Nothing else in the suite distinguishes the two modes: this file's
  // fixtures render the same inner surface either way.
  it('defaults to the single unsliced surface — no channel-strip wrapper at all', () => {
    render();
    expect(q('[data-testid="channel-strips"]')).toBeNull();
    expect(target.querySelectorAll('[data-testid="vfo-surface"]')).toHaveLength(1);
  });

  // MOR-1069, finding N1 (routed from the MOR-1068 verification). MOR-1068
  // wrapped the RX/TX surface in an inert `display: contents` zone shell on
  // EVERY path, so the single/default composition — the one sdr-test, the LCD
  // layouts and MOBILE all mount — stopped being element-identical to its
  // pre-cockpit shape. Layout was preserved, nothing queried the old position,
  // and it was accepted as a trade-off; MOR-1069 collapses it back out, and
  // this is the element-shape expectation re-pinned so it cannot drift back.
  // MUTATION KILLED: reintroducing an always-on wrapper (or extending the
  // cockpit's zone binding to the default path, which would put a zone id on
  // a layout whose manifest never declared one).
  it('mounts the RX/TX surface bare — no zone wrapper on the default path', () => {
    render();
    const root = q('[data-testid="semantic-radio-surfaces"]')!;
    const surface = q('[data-testid="rx-tx-surface"]')!;
    expect(surface.parentElement).toBe(root);
    expect(root.querySelector('.rx-tx-zone')).toBeNull();
    expect(target.querySelectorAll('[data-zone-id]')).toHaveLength(0);
    // Still exactly one TX action surface — the branch must not duplicate it.
    expect(target.querySelectorAll('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="rx-tx-key"]')).toHaveLength(1);
  });

  it('renders no surfaces at all rather than guessing when capabilities are absent', () => {
    h.caps = null;
    render();
    expect(q('[data-testid="vfo-surface"]')).toBeNull();
    expect(q('[data-testid="rx-tx-surface"]')).toBeNull();
  });

  it('routes the VFO selection intent to the command bus with the real slot id', () => {
    render();
    const buttons = [...target.querySelectorAll<HTMLButtonElement>('[data-vfo-select]')];
    buttons[0].click();
    flushSync();
    expect(h.selectVfo).toHaveBeenCalledWith('MAIN', 'B');
  });
});

describe('managed TX intent boundary', () => {
  it('emits one unscoped HTTP TRANSMIT intent', () => {
    render();
    q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.click();
    expect(h.start).toHaveBeenCalledExactlyOnceWith();
  });

  it('keeps unconditional ForceOFF enabled in stale fault state', () => {
    h.snapshot = { ...IDLE, fresh: false, phase: 'failed', fault: 'release-not-confirmed' };
    render();
    const off = q<HTMLButtonElement>('[data-testid="rx-tx-unkey"]')!;
    expect(off.disabled).toBe(false);
    off.click();
    expect(h.release).toHaveBeenCalledExactlyOnceWith();
  });

  it('does not emit a command on presentation teardown', () => {
    render();
    unmount(component!); component = null;
    expect(h.release).not.toHaveBeenCalled();
    expect(h.start).not.toHaveBeenCalled();
  });

  it('renders server failure without a browser reset action', () => {
    h.snapshot = { ...IDLE, phase: 'failed', fault: 'not-eligible' };
    render();
    expect(q('[data-testid="tx-fault-reset"]')).toBeNull();
    expect(q('[data-testid="tx-fault-reset-blocked"]')).not.toBeNull();
  });

  it('keeps canonical unknown fail-closed without optimistic browser truth', () => {
    h.snapshot = { ...IDLE, fresh: false, radioTx: 'unknown', phase: 'unknown' };
    render();
    expect(q('[data-testid="rx-tx-state"]')?.getAttribute('data-rf')).toBe('unknown');
    expect(q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.disabled).toBe(true);
    expect(q<HTMLButtonElement>('[data-testid="rx-tx-unkey"]')!.disabled).toBe(false);
  });

  it('does not replay rejected ON and recovers only after a fresh canonical snapshot', () => {
    render();
    q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.click();
    push({ fresh: false, phase: 'failed', fault: 'not-eligible', radioTx: 'unknown' });
    expect(q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.disabled).toBe(true);
    expect(h.start).toHaveBeenCalledTimes(1);
    push({ ...IDLE, fresh: true });
    expect(q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.disabled).toBe(false);
    expect(h.start).toHaveBeenCalledTimes(1);
    expect(h.release).not.toHaveBeenCalled();
  });

  it('reuses the App-root host across A to B to A presentation mounts without TX writes', () => {
    render();
    unmount(component!); component = null;
    render({ strips: 'dual' });
    unmount(component!); component = null;
    render();
    expect(h.subscribeCalls).toBe(3);
    expect(h.unsubscribeCalls).toBe(2);
    expect(h.start).not.toHaveBeenCalled();
    expect(h.release).not.toHaveBeenCalled();
  });
});

describe('the migrated layout keeps the MOR-617 network-voice-TX preflight', () => {
  // MUTATION KILLED: dropping <ModInputTxWarning /> from the wiring. It ships
  // inside TxPanel, which this layout suppresses — but this layout can now key
  // network voice TX (the controller's start-audio effect IS that path), so
  // without it the operator keys into a mis-routed MOD input with no warning
  // and no one-click fix.
  it('shows the warning under the same trigger condition the panel used', () => {
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render();
    const warning = q('[data-testid="mod-input-tx-warning"]');
    expect(warning).not.toBeNull();
    expect(q('[data-testid="mod-input-set-lan"]')).not.toBeNull();

    q<HTMLButtonElement>('[data-testid="mod-input-set-lan"]')!.click();
    flushSync();
    expect(h.setLan).toHaveBeenCalledTimes(1);
  });

  it('stays out of the way when the preflight is satisfied', () => {
    render();
    expect(q('[data-testid="mod-input-tx-warning"]')).toBeNull();
  });

  it('is not gated on the view model — it shows even before capabilities load', () => {
    h.caps = null;
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render();
    expect(q('[data-testid="rx-tx-surface"]')).toBeNull();
    expect(q('[data-testid="mod-input-tx-warning"]')).not.toBeNull();
  });
});

describe('MOR-1258 — the three TX-adjacent alerts join the rx-tx zone in the dual composition', () => {
  // The owner ruling (2026-08-04, gate item (b)): `tx-fault-reset` and the
  // two ModInputTxWarning buttons render inside the rx-tx zone's bound
  // element when `strips="dual"` — the only composition with a bound zone at
  // all (MOR-1069). Direct containment checks at the wiring level, one layer
  // below the full cockpit shell mount in
  // `skins/dual-receiver-cockpit/__tests__/DualReceiverCockpit.component.test.ts`.
  it('contains both ModInputTxWarning buttons inside the rx-tx zone', () => {
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render({ strips: 'dual' });

    const zone = q('.rx-tx-zone')!;
    expect(zone.contains(q('[data-testid="mod-input-set-lan"]'))).toBe(true);
    expect(zone.contains(q('[data-testid="mod-input-dismiss"]'))).toBe(true);
  });

  // The single/default path has no bound zone at all (MOR-1069) — the
  // ticket's explicit, honest carve-out (no containment is possible there).
  // The alerts keep their pre-MOR-1258 bare placement, unchanged.
  // MOR-617's invariant survives the containment move even in the dual
  // composition: the warning still is not gated on the view model.
  it('still shows the MOD-input warning before capabilities load, in the dual composition too', () => {
    h.caps = null;
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render({ strips: 'dual' });
    expect(q('[data-testid="rx-tx-surface"]')).toBeNull();
    const zone = q('.rx-tx-zone')!;
    expect(zone).not.toBeNull();
    expect(zone.contains(q('[data-testid="mod-input-tx-warning"]'))).toBe(true);
  });
});
