/**
 * MOR-1094 — the mobile presentation shell migrated to App-owned behavior and
 * a v1 layout manifest, mirroring the MOR-1092 LCD slice.
 *
 * SAFETY-ADJACENT. The mobile shell carries the operator's press-and-hold PTT,
 * and this slice also renders the semantic RX/TX surface, which emits a
 * latched TRANSMIT intent. Four things must hold at once:
 *   1. the semantic VFO / RX-TX surfaces are mounted in the portrait deck via
 *      the unchanged `SemanticRadioSurfaces` wiring — no new TX code path;
 *   2. the press-and-hold path is still the MOR-1011/1012 gesture recognizer
 *      feeding the App-root managed controller — pinned by wire intent;
 *   3. momentary WS PTT and latched HTTP TRANSMIT/ForceOFF remain distinct,
 *      and rotation releases only an in-progress momentary press;
 *   4. the App-global Toast / power overlay / TX lamp stay singular and global
 *      (MOR-1059), and the skin wrapper owns no resolution or runtime.
 *
 * Each test's doc line names the mutation it exists to kill.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { mount, unmount, flushSync } from 'svelte';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { ManagedTxState } from '$lib/runtime/tx-controller/managed-state';
import { presentationResources } from '$lib/runtime';
import type { ResourceLease } from '$lib/runtime/resource-demand';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';

// -- Child components the shell mounts that are irrelevant here -------------
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('../display/FrequencyDisplay.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../meters/LinearSMeter.svelte', () => ({ default: function S() { return {}; } }));
// MOR-1245 — CollapsiblePanel, BottomSheet and TxPanel stay REAL here: the
// one-banner acceptance now includes the TX-settings sheet's TxPanel copy,
// which only a real mount can prove. TxPanel's runtime inputs are mocked
// below (panel-adapters, mod-input-auto) — same idiom as TxPanel.isolated.
vi.mock('../controls/BandSelector.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/FilterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RxAudioPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/DspPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AgcPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RfFrontEnd.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RitXitPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AntennaPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/ScanPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/CwPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/DockMeterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/EssentialsPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('./KeyboardHandler.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('$lib/Button', () => ({ HardwareButton: function S() { return {}; } }));
vi.mock('lucide-svelte', () => {
  const S = function () { return {}; };
  return { Settings: S, ChevronLeft: S, ChevronRight: S, ChevronsLeft: S, ChevronsRight: S, Mic: S, MicOff: S, Sliders: S, Radio: S };
});
vi.mock('../controls/value-control', () => ({
  ValueControl: function S() { return {}; },
  normalizedPercentDisplay: (v: number) => `${Math.round(v * 100)}%`,
}));
vi.mock('./vfo-layout-tokens', () => ({
  resolveVfoLayoutProfile: vi.fn(() => 'standard'),
  vfoLayoutStyleVars: vi.fn(() => ''),
}));

// The MOR-617 banner's adapter reads persisted state; keep this suite hermetic
// (and free of the localStorage-shaped environment noise) by stubbing it flat.
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: vi.fn(() => ({ visible: false, sourceLabel: null })),
  getModInputTxGuardHandlers: vi.fn(() => ({ onSetLan: vi.fn(), onDismiss: vi.fn() })),
}));

// The now-real TxPanel (MOR-1245, sheet-open one-banner test) pulls two more
// adapters. PARTIAL panel-adapters mock — importOriginal spread keeps the
// layout's own `bindSemanticSurfaceHandlers`/`getPresetHandlers`/
// `getKeyboardHandlers` real; only TxPanel's three Tx accessors are faked,
// so the real mount never pins the transport/stores modules in the shared
// (isolate: false) cache — same #771 rationale as the guard adapter above.
const txPanelProps = {
  rfPower: 0.5, micGain: 128, atuActive: false, atuTuning: false,
  voxActive: false, compActive: false, compLevel: 64, monActive: false,
  monLevel: 64, driveGain: 128, hasTx: true, hasTuner: true, hasMonitor: true,
};
const txPanelHandlerNames = [
  'onRfPowerChange', 'onMicGainChange', 'onAtuToggle', 'onAtuTune', 'onVoxToggle',
  'onCompToggle', 'onCompLevelChange', 'onMonToggle', 'onMonLevelChange', 'onDriveGainChange',
] as const;
vi.mock('$lib/runtime/adapters/panel-adapters', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/adapters/panel-adapters')>();
  return {
    ...actual,
    deriveTxProps: () => txPanelProps,
    getTxHandlers: () => Object.fromEntries(txPanelHandlerNames.map((n) => [n, vi.fn()])),
    getTxAuxControlFeedback: () => ({
      confirmed: 128, target: null, requestedTarget: null, phase: 'idle', busy: false,
      availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
      sessionEpoch: 1, scope: { control: 'mic-gain', receiver: 0 },
      repeatPolicy: 'latest-target-wins',
    }),
  };
});
vi.mock('$lib/runtime/adapters/mod-input-auto.svelte', () => ({
  deriveAutoLanModInputProps: () => ({ available: false, enabled: false }),
  setAutoLanModInputEnabled: vi.fn(),
}));

// A real, fully-populated view model so the semantic surfaces actually RENDER.
// Without it `SemanticRadioSurfaces` short-circuits on `{#if view}` and every
// assertion below about the surfaces would pass for the wrong reason.
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', async () => {
  const { topologyFixtures } = await import('../../../semantic/fixtures/topologies');
  // `1/single` matches the mocked capabilities below (no dual receiver) and is
  // the fixture whose TX target is observed and whose permit is allowed — so
  // the RX/TX surface's key action is reachable rather than blocked.
  return { toRadioViewModel: vi.fn(() => topologyFixtures['1/single']) };
});

// -- Store mocks (kept in step with MobileRadioLayout.component.svelte.test.ts) --
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null as { active?: 'MAIN' | 'SUB' } | null },
  getActiveReceiver: vi.fn(), getRadioState: vi.fn(), patchActiveReceiver: vi.fn(),
  subscribeRadioState: vi.fn((handler: (state: null) => void) => {
    handler(null);
    return () => {};
  }),
  patchRadioState: vi.fn(), patchReceiver: vi.fn(),
}));
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => ({ connected: false })),
  getWsConnected: vi.fn(() => false),
  getRadioPowerOn: vi.fn(() => null),
  // MOR-1279 slice 3B: the RX-audio snapshot reports audio-WS link health.
  isAudioConnected: vi.fn(() => false),
}));
vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({ volume: 50, muted: false, rxEnabled: false, txEnabled: false, micEnabled: false, bridgeRunning: false })),
  getRxAudioTargetSnapshot: vi.fn(() => Object.freeze({ muted: false, rxEnabled: false })),
  subscribeRxAudioTarget: vi.fn((handler: (target: { muted: boolean; rxEnabled: boolean }) => void) => {
    handler(Object.freeze({ muted: false, rxEnabled: false }));
    return () => {};
  }),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    onChange: () => () => {}, getAppliedAudioConfig: () => null,
    onTxAudioDied: () => () => {},
    setOperatorNotifier: vi.fn(),
    start: vi.fn(), stop: vi.fn(), setVolume: vi.fn(), toggleMute: vi.fn(),
  },
}));
vi.mock('$lib/utils/tx-permit', () => ({ getTxPermit: vi.fn(() => 'allowed') }));
vi.mock('$lib/stores/tuning.svelte', () => ({ applyModeDefault: vi.fn() }));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true), hasDualReceiver: vi.fn(() => false), hasAnyScope: vi.fn(() => false),
  hasSpectrum: vi.fn(() => true), getCapabilities: vi.fn(() => ({ capabilities: [], freqRanges: [], modes: [], filters: [] })),
  subscribeCapabilities: vi.fn((handler: (caps: unknown) => void) => {
    handler({ capabilities: [], freqRanges: [], modes: [], filters: [] });
    return () => {};
  }),
  getKeyboardConfig: vi.fn(() => null), setCapabilities: vi.fn(), hasCapability: vi.fn(() => false),
  vfoLabel: vi.fn((s: string) => s === 'A' ? 'MAIN' : 'SUB'),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id), isAudioFftScope: vi.fn(() => false),
  hasAudioFft: vi.fn(() => false), getScopeSource: vi.fn(() => null), hasAudio: vi.fn(() => false),
  getSmeterCalibration: vi.fn(() => null), getSmeterRedline: vi.fn(() => null),
  getMeterCalibration: vi.fn(() => null), getMeterRedline: vi.fn(() => null),
  getControlRange: vi.fn(() => ({ min: 0, max: 255 })),
  getSupportedModes: vi.fn(() => ['USB', 'LSB', 'CW', 'AM', 'FM']),
  getSupportedFilters: vi.fn(() => ['FIL1', 'FIL2', 'FIL3']),
  getAttValues: vi.fn(() => [0, 10, 20]), getAttLabels: vi.fn(() => ({ 0: '0dB', 10: '10dB', 20: '20dB' })),
  getPreValues: vi.fn(() => [0, 1, 2]), getPreLabels: vi.fn(() => ({ 0: 'OFF', 1: 'PRE1', 2: 'PRE2' })),
  getAgcModes: vi.fn(() => [0, 1, 2, 3]),
  getAgcLabels: vi.fn(() => ({ 0: 'OFF', 1: 'FAST', 2: 'MID', 3: 'SLOW' })),
  getVfoScheme: vi.fn(() => 'ab'), getAntennaCount: vi.fn(() => 1),
}));
// MOR-1409 A15: the `wiring/command-bus` and `wiring/state-adapter` mocks
// that stood here were dead weight — MobileRadioLayout stopped importing
// either module at A13a (pinned by MobileRadioLayout.honesty), so the mocks
// intercepted nothing. A15 deletes both modules; the mocks are removed
// rather than re-pointed, because re-pointing them at panel-commands /
// panel-props would ACTIVATE previously inert stubs and change what this
// suite actually exercises.

const txHost = vi.hoisted(() => ({ current: undefined as ManagedAppTxController | undefined }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => {
    if (!txHost.current) throw new Error('managed TX host missing in test');
    return txHost.current;
  },
}));

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import mobileLayoutSource from '../MobileRadioLayout.svelte?raw';
import mobileSkinSource from '../../../skins/mobile/MobileSkin.svelte?raw';
import { hasTx, getScopeSource, getCapabilities, hasDualReceiver } from '$lib/stores/capabilities.svelte';
import { radio } from '$lib/stores/radio.svelte';
import { deriveModInputTxGuardProps } from '$lib/runtime/adapters/mod-input-tx-guard.svelte';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
import {
  topologyFixtures, withTxAux, withMeters, withRxAudio, withModeFilter,
  withFilterPassband, withDsp, withRfFrontEnd, withBand, withRitXit,
  withAntenna, withCwKeyer, withScan, withScopeControls, withScopeDisplay,
} from '../../../semantic/fixtures/topologies';

const RX: ManagedTxState = Object.freeze({
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null,
  faultDetail: null, fresh: true, releaseRequired: false, remainingMs: null,
  configuredSeconds: 180, lastOperation: null,
});

function createTxHarness() {
  let state = RX;
  const listeners = new Set<(next: Readonly<ManagedTxState>) => void>();
  const pttOn = vi.fn();
  const pttOff = vi.fn();
  const transmitOn = vi.fn();
  const forceOff = vi.fn();
  const setTot = vi.fn(async () => {});
  const facade: ManagedAppTxController = Object.freeze({
    snapshot: () => state,
    subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener); },
    pttOn, pttOff, transmitOn, forceOff, setTot,
  });
  return {
    facade, pttOn, pttOff, transmitOn, forceOff, setTot,
    project: (next: ManagedTxState) => {
      state = Object.freeze({ ...next });
      for (const listener of listeners) listener(state);
      flushSync();
    },
  };
}

let tx: ReturnType<typeof createTxHarness>;
let components: ReturnType<typeof mount>[] = [];

const HOLD_MS = 50;

function setViewport(landscape: boolean) {
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: landscape ? 844 : 390 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: landscape ? 390 : 844 });
}

function mountMobile(): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(MobileRadioLayout, { target }));
  flushSync();
  return target;
}

function rotate(landscape: boolean) {
  setViewport(landscape);
  window.dispatchEvent(new Event('resize'));
  flushSync();
}

function pointer(el: Element, type: string, init: PointerEventInit = {}) {
  el.dispatchEvent(new PointerEvent(type, { bubbles: true, clientX: 0, clientY: 0, pointerId: 1, ...init }));
  flushSync();
}

const fabEl = (t: HTMLElement) => t.querySelector<HTMLButtonElement>('.ptt-fab')!;
const flushAudio = async () => { await Promise.resolve(); await Promise.resolve(); flushSync(); };

/** A completed FAB press: PttFab only calls onDown() past its 50 ms guard. */
function fabPress(t: HTMLElement) {
  pointer(fabEl(t), 'pointerdown');
  vi.advanceTimersByTime(HOLD_MS);
  flushSync();
}

/** Press and hold the FAB until the managed gesture emits PTT ON. */
async function hold(t: HTMLElement) {
  fabPress(t);
  await flushAudio();
}

/**
 * Key through the semantic RX/TX surface. An authoritative readback comes
 * first because the surface refuses its own key action while the RF state is
 * unobserved (`rf-state-unknown`) — asserting the button is live keeps a
 * silently-disabled button from making these tests pass vacuously.
 */
function semanticKey(t: HTMLElement) {
  const key = t.querySelector<HTMLButtonElement>('[data-testid="rx-tx-key"]')!;
  expect(key.disabled).toBe(false);
  key.click();
  flushSync();
}

beforeEach(() => {
  vi.useFakeTimers();
  components = [];
  setViewport(false);
  tx = createTxHarness();
  txHost.current = tx.facade;
  vi.mocked(hasTx).mockReturnValue(true);
});

afterEach(() => {
  components.forEach((c) => {
    try { unmount(c); } catch { /* already unmounted by a test */ }
  });
  document.body.innerHTML = '';
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// 1. Semantic surface adoption
// ---------------------------------------------------------------------------
describe('semantic VFO / RX-TX adoption in the mobile shell', () => {
  // Kills: the migration never landing — the shell keeping only its legacy
  // header facts and the FAB as its sole TX truth.
  it('mounts the semantic surfaces in the portrait deck', () => {
    const t = mountMobile();
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
    expect(t.querySelectorAll('[data-testid="rx-tx-surface"]')).toHaveLength(1);
  });

  // Kills: mounting the surfaces inside a chip panel. Chip panels are
  // destroyed and recreated on every chip tap, and this subtree holds a TX
  // lease source — churning it would churn a TX identity (MOR-1086 doctrine).
  // MOR-2816: the mount now HOSTS the whole portrait body (the S-meter slot
  // above the scroll deck and `main.m-content` inside it), so the invariant
  // is stated from the same two facts the old form stated: never inside a
  // chip panel, and the scroll deck lives inside the one mount.
  it('mounts them outside every chip panel, hosting the scroll deck (MOR-2816)', () => {
    const t = mountMobile();
    const surfaces = t.querySelector('[data-testid="semantic-radio-surfaces"]')!;
    expect(surfaces.closest('.m-section')).toBeNull();
    expect(t.querySelector('.m-content')!.closest('[data-testid="semantic-radio-surfaces"]')).toBe(surfaces);
  });

  // Kills: adding a second copy of the wiring (one per orientation, or one
  // per chip). Exactly one instance may exist — each is a distinct TX source.
  // MOR-2442: landscape also mounts ONE, hosting its scope panel through the
  // managed region.
  it('never mounts a second copy, in either orientation', () => {
    const t = mountMobile();
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
    rotate(true);
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
    rotate(false);
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
  });

  // Kills: hand-rolling a mobile-local copy of the surfaces instead of reusing
  // the shared wiring the LCD and desktop slices mount (MOR-1065).
  it('reuses the shared SemanticRadioSurfaces wiring, unchanged', () => {
    expect(mobileLayoutSource).toContain('SemanticRadioSurfaces');
    expect(mobileLayoutSource).toContain("from '../wiring/SemanticRadioSurfaces.svelte'");
    // No mobile-local RX/TX or VFO surface reimplementation.
    expect(mobileLayoutSource).not.toContain('RxTxSurface');
    expect(mobileLayoutSource).not.toContain('VfoSurface');
  });
});

// ---------------------------------------------------------------------------
// 1b. MOR-2662 (owner ruling 2026-09-26): the phone shows only ONE VFO —
//     the active receiver's active slot — in both orientations. The
//     presentation option the shell passes does it; the desktop path and the
//     shared surface's default stay byte-identical.
// ---------------------------------------------------------------------------
describe('MOR-2662 — the phone shows only the active VFO', () => {
  afterEach(() => {
    vi.mocked(toRadioViewModel).mockReturnValue(topologyFixtures['1/single']);
  });

  // Kills: the deck still drawing BOTH VFO tiles below the header.
  it('portrait renders exactly one VFO tile — the active receiver\'s active slot', () => {
    vi.mocked(toRadioViewModel).mockReturnValue(topologyFixtures['2/main_sub']);
    const t = mountMobile();
    const tiles = t.querySelectorAll('[data-vfo-tile]');
    expect(tiles).toHaveLength(1);
    expect(tiles[0].getAttribute('data-vfo-receiver')).toBe('MAIN');
    expect(tiles[0].getAttribute('data-vfo-active')).toBe('true');
    expect(tiles[0].getAttribute('data-vfo-active-slot')).toBe('true');
  });

  // The single-receiver A/B shape: the active SLOT's tile alone survives.
  it('portrait renders the active slot only on a slotted A/B radio', () => {
    vi.mocked(toRadioViewModel).mockReturnValue(topologyFixtures['1/ab']);
    const t = mountMobile();
    const tiles = t.querySelectorAll('[data-vfo-tile]');
    expect(tiles).toHaveLength(1);
    expect(tiles[0].getAttribute('data-vfo-slot')).toBe('A');
    expect(tiles[0].getAttribute('data-vfo-active')).toBe('true');
  });

  // Kills: the landscape overlay re-adding a second VFO surface.
  it('landscape renders no VFO tiles — the strip shows the active frequency alone', () => {
    vi.mocked(toRadioViewModel).mockReturnValue(topologyFixtures['2/main_sub']);
    rotate(true);
    const t = mountMobile();
    expect(t.querySelectorAll('[data-vfo-tile]')).toHaveLength(0);
    expect(t.querySelector('.m-ls-vfo')).not.toBeNull();
  });

  // Kills: a future edit dropping the option and silently restoring two
  // tiles. The one-tile phone is a presentation OPTION the shell passes,
  // never a fork of the shared surface.
  it('passes the one-tile presentation option to the shared wiring', () => {
    expect(mobileLayoutSource).toContain('vfoTiles="active"');
  });
});

// ---------------------------------------------------------------------------
// 1d. MOR-2816 (owner ruling 2026-09-27) — the portrait deck mounts ONLY its
//     declared zone (portrait-deck = vfo + rxTx). No optional surface renders
//     bare below the deck: the chip tabs are the one place for controls. The
//     receiver S-meter the VFO surface's MAIN block carried is hoisted into
//     the bar under the frequency header — the same component, fed by the
//     same view model — and the in-card seat is gone.
// ---------------------------------------------------------------------------
describe('MOR-2816 — the phone deck mounts only its declared zone', () => {
  // The optional surfaces the bare single-composition path renders below
  // the deck on a fully-populated view model. Each landmark must be GONE;
  // the two required surfaces must still render.
  const OPTIONAL_SURFACE_LANDMARKS = [
    'rx-audio-surface', 'filter-surface', 'dsp-surface',
    'rf-front-end-surface', 'band-surface', 'antenna-surface',
    'ritxit-scan-surface', 'cw-keyer-surface', 'tx-aux-surface', 'meters-surface',
    'scope-display-surface', 'scope-controls-surface',
  ] as const;

  // The base topology fixtures carry NO optional groups, so the base mock
  // would make every landmark assertion below pass vacuously. Compose every
  // optional group onto `1/single` — the radio the owner measured on
  // 2026-09-27 had every one of these surfaces rendering bare.
  const fullyLoadedView = withScopeDisplay(withScopeControls(withScan(
    withCwKeyer(withAntenna(withRitXit(withBand(withRfFrontEnd(
      withDsp(withFilterPassband(withModeFilter(
        withRxAudio(withMeters(withTxAux(topologyFixtures['1/single']))))))))))))));

  // Kills: any optional surface regressing onto the phone's bare path —
  // the unstyled control block the owner measured on 2026-09-27.
  it('renders no optional surface below the deck; VFO and RX/TX still render', () => {
    const restore = vi.mocked(toRadioViewModel).getMockImplementation();
    vi.mocked(toRadioViewModel).mockReturnValue(fullyLoadedView);
    try {
      const t = mountMobile();
      for (const landmark of OPTIONAL_SURFACE_LANDMARKS) {
        expect(t.querySelectorAll(`[data-testid="${landmark}"]`), landmark).toHaveLength(0);
      }
      expect(t.querySelectorAll('[data-testid="vfo-surface"]')).toHaveLength(1);
      expect(t.querySelectorAll('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    } finally {
      vi.mocked(toRadioViewModel).mockImplementation(restore ?? (() => topologyFixtures['1/single']));
    }
  });

  // Kills: a second meter implementation, or the hoisted bar keeping a twin
  // in the VFO card's receiver-indicator block. The bar under the header is
  // the SAME receiver meter the card carried — same view model, one mount —
  // and the card's in-card seat is withheld. The view carries the
  // receiver-indicator group so the in-card seat WOULD render without the
  // withholding option (no vacuous pass).
  it('hoists the receiver S-meter to the bar under the header, leaving no in-card meter', () => {
    const indicatorField = <T>(value: T) => ({
      reading: { status: 'known' as const, value },
      availability: { structural: true, operational: true },
    });
    const cardMeterView = {
      ...topologyFixtures['1/single'],
      receiverIndicators: [{
        receiver: 'MAIN' as const,
        availability: { structural: true, operational: true },
        sMeter: {
          ...indicatorField(0),
          source: {
            providerGeneration: 1, scope: 'receiver' as const, receiver: 'MAIN' as const,
            path: 'main.sMeter',
          },
        },
        bandwidthHz: indicatorField(2400),
        agcMode: indicatorField(0),
        nbActive: indicatorField(false),
        nrActive: indicatorField(false),
        notchMode: indicatorField<'off' | 'auto' | 'manual'>('off'),
        attenuator: indicatorField(0),
        preamp: indicatorField(0),
        rfGain: indicatorField(0),
        digiSel: indicatorField(false),
        ipPlus: indicatorField(false),
      }],
    };
    const restore = vi.mocked(toRadioViewModel).getMockImplementation();
    vi.mocked(toRadioViewModel).mockReturnValue(cardMeterView);
    try {
      const t = mountMobile();
      const bar = t.querySelector('.m-smeter-bar');
      expect(bar).not.toBeNull();
      // The bar sits inside the one semantic mount, before the scroll deck.
      expect(bar!.closest('[data-testid="semantic-radio-surfaces"]')).not.toBeNull();
      const content = t.querySelector('.m-content')!;
      expect(bar!.compareDocumentPosition(content) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      // The in-card seat the VFO surface's MAIN block used to carry is gone.
      expect(t.querySelectorAll('[data-testid="receiver-s-meter"]')).toHaveLength(0);
      expect(t.querySelectorAll('[data-testid="receiver-s-meter-unknown"]')).toHaveLength(0);
    } finally {
      vi.mocked(toRadioViewModel).mockImplementation(restore ?? (() => topologyFixtures['1/single']));
    }
    // The presentation option reaches the shared wiring — an option, never
    // a fork: the shell passes `vfoMeter="external"` the way it passes
    // `vfoTiles="active"`, and the hoisted bar keeps the card meter's own
    // face (the same compact `vfo-wide` variant the card rendered).
    expect(mobileLayoutSource).toContain('vfoMeter="external"');
    expect(mobileLayoutSource).toContain('variant="vfo-wide"');
  });

  // Kills: the hoisted bar showing the wrong receiver on a dual-receiver
  // radio — it follows the ACTIVE receiver, the same receiver the header
  // reads (the retired value-fed strip's contract, MOR-2511).
  it('the hoisted bar follows the active receiver on a dual-receiver radio', () => {
    const restore = vi.mocked(toRadioViewModel).getMockImplementation();
    vi.mocked(toRadioViewModel).mockReturnValue(topologyFixtures['2/main_sub']);
    radio.current = { active: 'SUB' } as unknown as ServerState;
    try {
      const t = mountMobile();
      expect(t.querySelector('.m-smeter-bar')?.getAttribute('data-receiver')).toBe('SUB');
    } finally {
      radio.current = null;
      vi.mocked(toRadioViewModel).mockImplementation(restore ?? (() => topologyFixtures['1/single']));
    }
  });
});

// ---------------------------------------------------------------------------
// 1c. MOR-1245 — the MOD-input TX preflight banner renders exactly once per
//     orientation. The portrait deck mounts the shared wiring (whose copy
//     exists since MOR-1065 slice c) beside the shell's fixed overlay, which
//     without a suppression prop means two banners, one trigger.
// ---------------------------------------------------------------------------
describe('MOR-1245 — one MOD-input TX banner per orientation', () => {
  beforeEach(() => {
    vi.mocked(deriveModInputTxGuardProps).mockReturnValue({ visible: true, sourceLabel: 'MIC' });
  });
  afterEach(() => {
    vi.mocked(deriveModInputTxGuardProps).mockReturnValue({ visible: false, sourceLabel: null });
  });

  // Kills the MOR-1094 duplicate: the shell's fixed overlay and the shared
  // wiring's `txAdjacentAlerts` both mounting in portrait. The FIXED
  // instance is the survivor — an operator cannot scroll past it while
  // keying (MOR-1094's reason for keeping it outside the scroll deck).
  it('renders exactly one banner in portrait, and it is the fixed overlay one', () => {
    const t = mountMobile();
    const banners = t.querySelectorAll('[data-testid="mod-input-tx-warning"]');
    expect(banners).toHaveLength(1);
    expect(t.querySelector('.m-mod-input-warning')!.contains(banners[0])).toBe(true);
  });

  // Kills the suppression leaking into landscape: the semantic deck unmounts
  // there (`rotate(true)` → zero `semantic-radio-surfaces` per 1 above), so
  // the fixed overlay is the ONLY possible instance and must keep rendering.
  it('renders exactly one banner in landscape', () => {
    const t = mountMobile();
    rotate(true);
    expect(t.querySelectorAll('[data-testid="mod-input-tx-warning"]')).toHaveLength(1);
    expect(t.querySelector('.m-mod-input-warning')).not.toBeNull();
  });

  // Kills the sheet-open duplicate: the TX-settings sheet mounts the REAL
  // TxPanel (unmocked here on purpose), whose inline copy renders a second
  // banner unless the shell suppresses it. The fixed overlay is again the
  // survivor — the sheet carries none.
  it('renders exactly one banner with the TX-settings sheet open', () => {
    const t = mountMobile();
    t.querySelector<HTMLElement>('[aria-controls="m-chip-panel-tx"]')!.click();
    flushSync();
    t.querySelector<HTMLElement>('.m-tx-settings-btn')!.click();
    flushSync();
    const banners = t.querySelectorAll('[data-testid="mod-input-tx-warning"]');
    expect(banners).toHaveLength(1);
    expect(t.querySelector('.m-mod-input-warning')!.contains(banners[0])).toBe(true);
    expect(t.querySelector('[data-testid="mod-input-tx-warning"]')!.closest('.m-sheet-content')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 1b. Spectrum slot (MOR-2511): this suite's `hasSpectrum` mock returns true,
// so it carries the with-spectrum counterpart of the no-spectrum pins in
// MobileRadioLayout.component.svelte.test.ts.
// ---------------------------------------------------------------------------
describe('spectrum slot with a spectrum-capable radio (MOR-2511)', () => {
  it.each([['portrait', false], ['landscape', true]] as const)(
    'mounts the SpectrumPanel in the %s spectrum slot', (_label, landscape) => {
      setViewport(landscape);
      const t = mountMobile();
      const slot = t.querySelector(landscape ? '.m-ls-spectrum' : '.m-spectrum');
      expect(slot?.querySelector('.spectrum-panel-stub')).not.toBeNull();
    });
});

// ---------------------------------------------------------------------------
// 1c. Managed scope contract (MOR-2442): both orientations receive the
// SemanticRadioSurfaces-managed projection/demand region — no second
// subscriber, no legacy panel subscription. The two halves depend on
// DIFFERENT mechanisms: the portrait half is killed by dropping the
// `scopeManaged` opt-in from the SRS gates; the landscape half is killed by
// the layout's hosted mount no longer forwarding `instruments.managedScope`
// to its SpectrumPanel. Each pin therefore lives on the mechanism that
// carries the region into that orientation's panel.
// The beforeEach also establishes AUTHORITY (matching state/caps
// generations), which the region assertions do not need but the lease pin
// below does: without it the SRS lease effect can never engage.
// ---------------------------------------------------------------------------
describe('managed scope contract on a hardware-scope radio (MOR-2442)', () => {
  // Resource spies registered during a test are restored HERE so a failing
  // pin cannot leak its wrapper into neighboring tests.
  const resourceSpies: { mockRestore(): void }[] = [];
  beforeEach(() => {
    vi.mocked(getScopeSource).mockReturnValue('hardware');
    vi.mocked(getCapabilities).mockReturnValue({
      capabilities: [], freqRanges: [], modes: [], filters: [],
      providerGeneration: 1, scopeSource: 'hardware',
    } as unknown as Capabilities);
    radio.current = { providerGeneration: 1, fieldStatus: {} } as unknown as ServerState;
  });
  afterEach(() => {
    vi.mocked(getScopeSource).mockReturnValue(null);
    vi.mocked(getCapabilities).mockReturnValue({
      model: '', scope: false, audio: false, tx: false,
      capabilities: [], receivers: 1, vfoScheme: 'single',
      freqRanges: [], modes: [], filters: [],
      audioConfig: { sampleRate: 48000, channels: 1, codecs: [] },
      webrtc: { available: false, enabled: false },
      txBands: null,
    });
    radio.current = null;
    for (const spy of resourceSpies.splice(0)) spy.mockRestore();
  });

  it.each([['portrait', false], ['landscape', true]] as const)(
    'receives the SemanticRadioSurfaces-managed region in %s', (_label, landscape) => {
      setViewport(landscape);
      const t = mountMobile();
      const slot = t.querySelector(landscape ? '.m-ls-spectrum' : '.m-spectrum');
      const panel = slot?.querySelector('.spectrum-panel-stub');
      // Projection may still be null while no frame has been accepted; the
      // contract is the bound region object itself, non-`undefined`.
      expect(panel?.getAttribute('data-managed-scope')).toBe('true');
      expect(panel?.getAttribute('data-scope-demanded')).toBe('true');
      expect(panel?.getAttribute('data-has-scope-demand-handler')).toBe('true');
    });

  // Kills: a leaked scope lease — either the hidden orientation's SRS
  // instance holding one while the visible side holds another, or a lease
  // surviving unmount. Planted form for the mini RED: drop the
  // `presentationResources.release(lease)` cleanup in SemanticRadioSurfaces'
  // lease effect — then rotation leaves TWO live leases and unmount leaves
  // one. Each orientation mounts at most one SRS instance; the pin counts
  // live leases at every settled step, not just the end state.
  it('holds at most one scope lease through rotation, and none after unmount', () => {
    const realAcquire = presentationResources.acquire.bind(presentationResources);
    const realRelease = presentationResources.release.bind(presentationResources);
    const live = new Set<ResourceLease>();
    const acquire = vi.spyOn(presentationResources, 'acquire').mockImplementation((resource, consumer) => {
      const lease = realAcquire(resource, consumer);
      live.add(lease);
      return lease;
    });
    const release = vi.spyOn(presentationResources, 'release').mockImplementation((lease) => {
      live.delete(lease);
      return realRelease(lease);
    });
    resourceSpies.push(acquire, release);

    const t = mountMobile(); // portrait — the visible instance takes one lease
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
    expect(live.size).toBe(1);

    rotate(true); // → landscape
    expect(live.size).toBe(1);

    rotate(false); // → back to portrait
    expect(live.size).toBe(1);

    for (const component of components.splice(0)) unmount(component);
    flushSync();
    expect(live.size).toBe(0); // no lease survives unmount
  });
});

// ---------------------------------------------------------------------------
// 2. Momentary PTT and latched TRANSMIT stay distinct
// ---------------------------------------------------------------------------
describe('mobile managed TX intent routing', () => {
  it('emits one momentary PTT ON through the App-root facade', async () => {
    const t = mountMobile();
    await hold(t);
    expect(tx.pttOn).toHaveBeenCalledTimes(1);
    expect(tx.pttOff).not.toHaveBeenCalled();
    expect(tx.transmitOn).not.toHaveBeenCalled();
    expect(tx.forceOff).not.toHaveBeenCalled();
  });

  it('emits one HTTP TRANSMIT intent from the semantic key and no WS PTT', () => {
    const t = mountMobile();
    semanticKey(t);
    expect(tx.transmitOn).toHaveBeenCalledTimes(1);
    expect(tx.pttOn).not.toHaveBeenCalled();
    expect(tx.pttOff).not.toHaveBeenCalled();
    expect(tx.forceOff).not.toHaveBeenCalled();
  });

  // Kills: reintroducing a local TX machine, raw PTT commands or bespoke
  // timers alongside the App controller (the MOR-1012 acceptance evidence,
  // re-asserted here because this slice edits the same file).
  it('still routes every mobile TX path through the App controller alone', () => {
    expect(mobileLayoutSource).toContain('getManagedAppTxController');
    expect(mobileLayoutSource).toContain('createManagedMobilePttSurface');
    // Deliberately NOT asserting on 'ptt_on'/'ptt_off' as bare substrings —
    // the retirement note in the layout's own comments names them.
    for (const retired of [
      'tx-adapter', 'getTxAudioControl', 'systemHandlers', 'engageTx', 'disengageTx',
      'pttSafetyTimer', 'PTT_SAFETY_TIMEOUT_MS', 'txStartToken', 'lastPttDown',
      'sendCommand',
    ]) {
      expect(mobileLayoutSource).not.toContain(retired);
    }
  });
});

// ---------------------------------------------------------------------------
// 3. Lifecycle and explicit unlock preserve transport meaning
// ---------------------------------------------------------------------------
describe('managed mobile lifecycle', () => {
  it('rotation releases an in-progress momentary PTT exactly once', async () => {
    const t = mountMobile();
    await hold(t);
    rotate(true);
    expect(tx.pttOff).toHaveBeenCalledTimes(1);
    expect(tx.forceOff).not.toHaveBeenCalled();
    expect(tx.transmitOn).not.toHaveBeenCalled();
  });

  it('a press while canonical TRANSMIT is latched emits unconditional ForceOFF only', () => {
    const t = mountMobile();
    tx.project({ ...RX, phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on' });
    fabPress(t);
    expect(tx.forceOff).toHaveBeenCalledTimes(1);
    expect(tx.pttOn).not.toHaveBeenCalled();
    expect(tx.pttOff).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 4. Orientation preserves App-owned authority and resources
// ---------------------------------------------------------------------------
describe('orientation change preserves App authority (MOR-1086 doctrine)', () => {
  // Kills: the shell re-deriving, re-creating or re-hosting TX authority per
  // orientation. A rotation is an intra-layout surface swap; the App-owned
  // controller object must be the IDENTICAL instance on the other side.
  it('keeps the identical App TX controller object across a rotation', () => {
    const t = mountMobile();
    const before = txHost.current;
    rotate(true);
    rotate(false);
    expect(txHost.current).toBe(before);
    expect(txHost.current).toBe(tx.facade);
    // And the shell is still live on it: a post-rotation key still lands.
    semanticKey(t);
    expect(tx.transmitOn).toHaveBeenCalledTimes(1);
  });

  // Kills: treating every semantic mount as selected LCD demand. Explicit
  // LCD faces may lease their selected source at the shared wiring host, but
  // mobile passes no displayFrameSource. Its destroy/rebuild on rotation must
  // therefore never bounce either canonical App resource.
  it('keeps undefined-source mobile mount, rotation, rebuild, and destroy at zero demand', () => {
    const acquire = vi.spyOn(presentationResources, 'acquire');
    const release = vi.spyOn(presentationResources, 'release');
    const t = mountMobile();
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
    expect(acquire).not.toHaveBeenCalled();
    expect(release).not.toHaveBeenCalled();

    rotate(true);
    // MOR-2442: landscape mounts its own ONE instance; with no display
    // source this mount still never leases. The hardware-source lease path
    // is pinned separately in 1c above.
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
    expect(acquire).not.toHaveBeenCalled();
    expect(release).not.toHaveBeenCalled();

    rotate(false);
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
    expect(acquire).not.toHaveBeenCalled();
    expect(release).not.toHaveBeenCalled();

    const mounted = components.splice(0);
    mounted.forEach((component) => unmount(component));
    flushSync();
    expect(acquire).not.toHaveBeenCalled();
    expect(release).not.toHaveBeenCalled();

    expect(mobileLayoutSource).not.toContain('presentationResources');
    expect(mobileLayoutSource).not.toContain('resource-demand');
    expect(mobileLayoutSource).not.toContain('displayFrameSource');
    // The plan still names hardware-scope for mobile, owned by SpectrumPanel.
    const registrySource = readFileSync('src/skins/registry.ts', 'utf8');
    expect(registrySource).toMatch(
      /'mobile':\s*\{[^}]*resources:\s*\['hardware-scope'\]/s,
    );
  });

  it('presentation rotation preserves canonical latched TRANSMIT without commands', () => {
    const t = mountMobile();
    tx.project({ ...RX, phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on' });
    rotate(true);
    rotate(false);
    expect(tx.pttOn).not.toHaveBeenCalled();
    expect(tx.pttOff).not.toHaveBeenCalled();
    expect(tx.transmitOn).not.toHaveBeenCalled();
    expect(tx.forceOff).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 5. Global-host singletons stay global (MOR-1059)
// ---------------------------------------------------------------------------
describe('the App-global host stays singular on mobile (MOR-1059)', () => {
  // Kills: the migration reintroducing a layout-hosted Toast, power overlay
  // or TX lamp — the duplicates MOR-1059 pulled up to the composition root.
  it.each([['portrait', false], ['landscape', true]] as const)(
    'hosts no Toast, power overlay or global TX lamp in %s', (_label, landscape) => {
      setViewport(landscape);
      const t = mountMobile();
      for (const selector of [
        '.toast-container',
        '[data-testid="app-global-host"]',
        '[data-testid="global-power-off"]',
        '[data-testid="global-tx-indication"]',
        '[data-testid="global-tx-fault"]',
      ]) {
        expect(t.querySelectorAll(selector)).toHaveLength(0);
      }
    });

  // Source-level backstop, matching the app-global-host suite's idiom.
  it('imports neither the Toast nor the global host itself', () => {
    expect(mobileLayoutSource).not.toMatch(/shared\/Toast\.svelte/);
    expect(mobileLayoutSource).not.toMatch(/<Toast\b/);
    expect(mobileLayoutSource).not.toContain('power-off-overlay');
    expect(mobileLayoutSource).not.toMatch(/<AppGlobalHost\b/);
  });
});

// ---------------------------------------------------------------------------
// 6. The skin wrapper owns no resolution and no runtime
// ---------------------------------------------------------------------------
describe('the mobile skin wrapper is a pure delegator', () => {
  // Kills: the wrapper re-deriving which skin/layout to show. Selection,
  // loading and commit belong to App.svelte over skins/registry.ts; a wrapper
  // that resolves again can disagree with the presentation App committed.
  it('owns no resolver: it selects nothing and loads nothing', () => {
    for (const owned of [
      'resolveSkinId', 'loadSkin', 'resolvePersistedSkinId', 'normalizeLayoutMode',
      'presentationResourcePlan', 'getLayout', 'resolveLayoutForViewport',
    ]) {
      expect(mobileSkinSource).not.toContain(owned);
    }
  });

  // Kills: the wrapper reaching past the presentation boundary. eslint's
  // no-restricted-imports covers src/skins/**; this pins the same boundary
  // from the test side so a config drift cannot silently open it.
  it('owns no runtime: no transport, command, capability or TX-authority import', () => {
    for (const forbidden of [
      '$lib/transport', 'audio-manager', '$lib/stores/capabilities',
      'tx-controller/managed-app-host', 'sendCommand', 'wiring/command-bus',
    ]) {
      expect(mobileSkinSource).not.toContain(forbidden);
    }
    // What it DOES do: delegate, and nothing else.
    expect(mobileSkinSource).toContain('MobileRadioLayout');
  });

  // Kills: a manifest that reaches for live state. Manifests are declarations
  // — the presentation/ zone bans runtime, transport and capability imports.
  it('keeps the mobile manifest a declaration over the layout contract alone', () => {
    const manifest = readFileSync('src/presentation/layouts/mobile-declarations.ts', 'utf8');
    const imports = [...manifest.matchAll(/from '([^']+)'/g)].map((m) => m[1]);
    expect(imports).toEqual(['./contract']);
  });
});
