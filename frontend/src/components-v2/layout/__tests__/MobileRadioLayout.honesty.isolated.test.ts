/**
 * MOR-1409 A13a — MobileRadioLayout command-bus + projection migration.
 *
 * This gate re-points the mobile skin's 15 command-bus handler imports at the
 * sanctioned adapter layer (`lib/runtime/adapters/panel-adapters.ts`) and its
 * 16 read projections at the A11/A12-hardened
 * `lib/runtime/props/panel-props.ts`.
 *
 * The read half is NOT a mechanical import swap: all 15 projection functions
 * shared with `components-v2/wiring/state-adapter.ts` have diverged, because
 * A11 and A12 hardened only `panel-props`. Migrating therefore imports the
 * whole honesty change into a shipped skin at once, and every direct-render
 * boundary in this layout has to be shown safe against `NaN` / `'---'`.
 *
 * Two rendered boundaries were live-traced as unsafe and are guarded inside
 * this layout (it is their only production consumer, so per the 5246487510
 * single-consumer test `mobile-layout-logic.ts` stays byte-identical):
 * `formatSValue(meter.signal)` renders "S NaN"-class strings and
 * `formatDbm(meter.signal)` renders the literal "NaN dBm".
 *
 * Each test names the mutation it exists to kill.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { mount, unmount, flushSync } from 'svelte';

// ── Child components irrelevant to the read-boundary contract ──────────────
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('../panels/lcd/AmberLcdDisplay.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../display/FrequencyDisplay.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../meters/LinearSMeter.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/CollapsiblePanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/BottomSheet.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/BandSelector.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/PttFab.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/FilterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RxAudioPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/TxPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/DspPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AgcPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RfFrontEnd.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RitXitPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AntennaPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/ScanPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/CwPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/DockMeterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/EssentialsPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/ModInputTxWarning.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../wiring/SemanticRadioSurfaces.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('./mobile-chip-bar.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('./KeyboardHandler.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('$lib/Button', () => ({ HardwareButton: function S() { return {}; } }));
vi.mock('lucide-svelte', () => {
  const S = function () { return {}; };
  return {
    Settings: S, ChevronLeft: S, ChevronRight: S, ChevronsLeft: S, ChevronsRight: S,
    Sliders: S, Radio: S, Mic: S, MicOff: S,
  };
});
vi.mock('../controls/value-control', () => ({
  ValueControl: function S() { return {}; },
  normalizedPercentDisplay: (v: number) => `${Math.round(v * 100)}%`,
}));
vi.mock('./vfo-layout-tokens', () => ({
  resolveVfoLayoutProfile: vi.fn(() => 'standard'),
  vfoLayoutStyleVars: vi.fn(() => ''),
}));

// ── Stores: a connected radio that has reported nothing yet ────────────────
// The layout's projections must be honest about that, not fabricate a
// 14.074 MHz / USB / FIL1 / S0 / -73 dBm operator out of thin air.
const radioStore = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }));
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: radioStore,
  getActiveReceiver: vi.fn(),
  getRadioState: vi.fn(() => radioStore.current),
  subscribeRadioState: vi.fn(() => () => {}),
}));
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => 'connected'),
  getRadioPowerOn: vi.fn(() => null),
  getHttpConnected: vi.fn(() => true),
  getWsConnected: vi.fn(() => true),
  isStale: vi.fn(() => false),
  isReconnecting: vi.fn(() => false),
  getRadioStatus: vi.fn(() => 'ok'),
  isAudioConnected: vi.fn(() => false),
}));
vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({
    volume: 50, muted: false, rxEnabled: false, txEnabled: false,
    micEnabled: false, bridgeRunning: false,
  })),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: { start: vi.fn(), stop: vi.fn(), setVolume: vi.fn(), toggleMute: vi.fn() },
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true), hasDualReceiver: vi.fn(() => false), hasAnyScope: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false), getCapabilities: vi.fn(() => null),
  getKeyboardConfig: vi.fn(() => null), hasCapability: vi.fn(() => false),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  hasAudioFft: vi.fn(() => false),
  getMeterCalibration: vi.fn(() => null), getMeterRedline: vi.fn(() => null),
  getSmeterCalibration: vi.fn(() => null), getSmeterRedline: vi.fn(() => null),
}));

// The TX controller is not what this suite is about — a flat idle facade keeps
// the layout mountable without pulling in the real state machine.
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => ({ guard: null, intent: 'idle', radioTx: 'off' }),
    subscribe: () => () => {},
  }),
}));

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import mobileLayoutSource from '../MobileRadioLayout.svelte?raw';

const LAYOUT_DIR = 'src/components-v2/layout/';

let host: HTMLElement | null = null;
let instance: Record<string, unknown> | null = null;

function mountLayout(): HTMLElement {
  host = document.createElement('div');
  document.body.appendChild(host);
  instance = mount(MobileRadioLayout, { target: host });
  flushSync();
  return host;
}

const originalWidth = window.innerWidth;
const originalHeight = window.innerHeight;

function setViewport(width: number, height: number): void {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true });
  Object.defineProperty(window, 'innerHeight', { value: height, configurable: true, writable: true });
}

beforeEach(() => {
  setViewport(390, 844);
  radioStore.current = null;
});

afterEach(() => {
  if (instance) unmount(instance);
  instance = null;
  if (host) host.remove();
  host = null;
  setViewport(originalWidth, originalHeight);
});

// ───────────────────────────────────────────────────────────────────────────
// Static closure: the layout consumes the canonical modules only
// ───────────────────────────────────────────────────────────────────────────

describe('MobileRadioLayout canonical module surface (MOR-1409 A13a)', () => {
  // Kills: re-pointing any projection back at `components-v2/wiring/state-adapter`.
  it('imports no projection from the legacy wiring state-adapter', () => {
    expect(mobileLayoutSource).not.toContain('wiring/state-adapter');
  });

  // Kills: leaving any handler family on the `wiring/command-bus` shim, whose
  // production importer count must reach zero for A15's deletion clause.
  it('imports no handler family from the legacy command-bus shim', () => {
    expect(mobileLayoutSource).not.toContain('wiring/command-bus');
  });

  // Kills: bypassing the adapter layer by importing the command module directly.
  it('does not reach into lib/runtime/commands directly', () => {
    expect(mobileLayoutSource).not.toContain('runtime/commands/panel-commands');
  });

  it('reads its projections from the hardened panel-props module', () => {
    expect(mobileLayoutSource).toContain('$lib/runtime/props/panel-props');
  });

  it('binds its handlers through the sanctioned panel-adapters layer', () => {
    expect(mobileLayoutSource).toContain('$lib/runtime/adapters/panel-adapters');
  });

  // Kills: calling `bindSemanticSurfaceHandlers()` more than once — the A07
  // convention, since each call mints fresh per-instance debounce state.
  it('calls the semantic surface binder exactly once', () => {
    const calls = mobileLayoutSource.match(/bindSemanticSurfaceHandlers\(\)/g) ?? [];
    expect(calls).toHaveLength(1);
  });

  // Kills: passing a literal for `toRxAudioProps`' fourth argument. The honest
  // source is the same one `lib/runtime/adapters/audio-adapter.ts:18` uses.
  it('sources toRxAudioProps audioConnected from live connection state', () => {
    const call = mobileLayoutSource.match(/toRxAudioProps\([^)]*\)/)?.[0] ?? '';
    expect(call).toContain('runtime.connectionAudio');
    expect(call).not.toMatch(/,\s*(true|false)\s*\)/);
  });

  // Kills: absorbing the display guards into `mobile-layout-logic.ts`, which is
  // a single-consumer helper module and therefore NOT an owner of this gate
  // (correction 5246842617 §8, applying the 5246487510 single-consumer test).
  it('leaves mobile-layout-logic.ts byte-identical', () => {
    const bytes = readFileSync(`${LAYOUT_DIR}mobile-layout-logic.ts`);
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(
      'db0062b97d7c33c3c12a91da5cf367e03343810d24957da0777941e226f1cd5a',
    );
  });
});

// ───────────────────────────────────────────────────────────────────────────
// Consumer boundaries: honest projections must not render as garbage
// ───────────────────────────────────────────────────────────────────────────

describe('MobileRadioLayout honest-projection rendering (MOR-1409 A13a)', () => {
  // Kills: dropping the finite guard from the landscape S-meter readout —
  // `formatSValue(NaN)` returns the literal `S${NaN}`. This is the "NaNkHz"
  // defect class that BLOCKED PR #2363, reproduced on the mobile skin.
  it('guards the landscape S-meter readout against an unobserved signal', () => {
    setViewport(844, 390);
    const readout = mountLayout().querySelector('.m-ls-smeter')?.textContent ?? '';
    expect(readout).not.toContain('NaN');
    // `formatSValue(0)` — the state-adapter's fabricated zero-signal reading —
    // renders a full-scale "S9" for a receiver that has reported nothing.
    expect(readout).not.toBe('S9');
    expect(readout).toContain('---');
  });

  // Kills: dropping the finite guard from the landscape dBm readout —
  // `formatDbm(NaN)` renders the literal "NaN dBm".
  it('guards the landscape dBm readout against an unobserved signal', () => {
    setViewport(844, 390);
    const readout = mountLayout().querySelector('.m-ls-dbm')?.textContent ?? '';
    expect(readout).not.toContain('NaN');
    expect(readout).not.toBe('-73 dBm');
    expect(readout).toContain('---');
  });

  // Kills: restoring `toVfoProps`' fabricated 'USB' / 'FIL1' stand-ins by
  // re-pointing the VFO projection at the stale state-adapter twin.
  it('shows placeholder mode and filter labels, not fabricated USB / FIL1', () => {
    const root = mountLayout();
    expect(root.querySelector('.m-vfo-mode')?.textContent).toBe('---');
    expect(root.querySelector('.m-vfo-filter')?.textContent).toBe('---');
  });

  // Kills: any guard that leaks "NaN" through a different portrait boundary —
  // the portrait deck is where the idle mobile screen actually lives.
  it('renders no "NaN" substring anywhere in portrait with nothing observed', () => {
    const text = mountLayout().textContent ?? '';
    expect(text).not.toContain('NaN');
  });
});

// ───────────────────────────────────────────────────────────────────────────
// Connected-but-this-field-pending: the second variant every family needs.
// A capability gate answers "does the rig support this", never "has this been
// observed" — so each of these mounts a CONNECTED radio that has simply not
// reported the field in question.
// ───────────────────────────────────────────────────────────────────────────

const CONNECTED_RX = {
  freqHz: 14074000, mode: 'USB', filter: 1, sMeter: 12,
  att: 0, preamp: 0, nb: false, nr: false,
};

describe('MobileRadioLayout pending-field boundaries (MOR-1409 A13a)', () => {
  // Kills: dropping the RIT-offset guard. `toRitXitProps` reports `NaN` for an
  // active RIT whose offset has never been sent, and the raw
  // `offset >= 0 ? '+' : ''` template renders "NaN" for it.
  it('guards the RIT offset badge when the offset has not been observed', () => {
    radioStore.current = { active: 'MAIN', main: CONNECTED_RX, ritOn: true };
    const badge = mountLayout().querySelector('.m-vfo-rit')?.textContent ?? '';
    expect(badge).toContain('RIT');
    expect(badge).not.toContain('NaN');
    expect(badge).toContain('---');
  });

  // Kills: dropping the same guard on the XIT branch.
  it('guards the XIT offset badge when the offset has not been observed', () => {
    radioStore.current = { active: 'MAIN', main: CONNECTED_RX, ritTx: true };
    const badge = mountLayout().querySelector('.m-vfo-rit')?.textContent ?? '';
    expect(badge).toContain('XIT');
    expect(badge).not.toContain('NaN');
    expect(badge).toContain('---');
  });

  // Kills: removing the `txMetersObserved` gate on the TX dock meter. The
  // panel formats all four rows itself and has no non-finite branch, so an
  // ungated mount renders "NaNW" / "NaN" SWR / "NaN%" on a live TX surface.
  it('withholds the TX dock meter while its readings are unobserved', () => {
    radioStore.current = { active: 'MAIN', main: CONNECTED_RX, ptt: true };
    const root = mountLayout();
    const txChip = Array.from(root.querySelectorAll<HTMLButtonElement>('button'))
      .find((b) => b.textContent?.trim() === 'TX');
    txChip?.click();
    flushSync();
    expect(root.querySelector('.m-tx-meter')).toBeNull();
    expect(root.textContent ?? '').not.toContain('NaN');
  });

  // Kills: a gate so broad it hides the meter for a rig that IS reporting —
  // the readings must come back the moment they are observed.
  it('renders the TX dock meter once its readings are observed', () => {
    radioStore.current = {
      active: 'MAIN', main: CONNECTED_RX, ptt: true,
      powerMeter: 120, swrMeter: 30, alcMeter: 40,
    };
    const root = mountLayout();
    const txChip = Array.from(root.querySelectorAll<HTMLButtonElement>('button'))
      .find((b) => b.textContent?.trim() === 'TX');
    txChip?.click();
    flushSync();
    expect(root.querySelector('.m-tx-meter')).not.toBeNull();
    expect(root.textContent ?? '').not.toContain('NaN');
  });
});
