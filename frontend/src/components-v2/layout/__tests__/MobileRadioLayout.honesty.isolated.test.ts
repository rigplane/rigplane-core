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
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const txHarness = new ManagedAppTxHarness();

// ── Child components irrelevant to the read-boundary contract ──────────────
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});
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
  subscribeRadioState: vi.fn((handler: (state: Record<string, unknown> | null) => void) => {
    handler(radioStore.current);
    return () => {};
  }),
}));
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => 'connected'),
  getRadioPowerOn: vi.fn(() => null),
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
vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true), hasDualReceiver: vi.fn(() => false), hasAnyScope: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false), getCapabilities: vi.fn(() => null),
  getScopeSource: vi.fn(() => null),
  subscribeCapabilities: vi.fn((handler: (caps: null) => void) => {
    handler(null);
    return () => {};
  }),
  getKeyboardConfig: vi.fn(() => null), hasCapability: vi.fn(() => false),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  hasAudioFft: vi.fn(() => false),
  getMeterCalibration: vi.fn(() => null), getMeterRedline: vi.fn(() => null),
  getSmeterCalibration: vi.fn(() => null), getSmeterRedline: vi.fn(() => null),
}));

vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHarness.controller,
}));

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import mobileLayoutSource from '../MobileRadioLayout.svelte?raw';
import { getSmeterCalibration } from '$lib/stores/capabilities.svelte';
import {
  calibratedToDbm as calibratedToDbmPure,
  formatDbm as formatDbmPure,
  type SmeterCalibrationPoint,
} from '../../../primitives/meters/s-meter-scale';

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
  txHarness.reset();
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
  // Hash refreshed for the MOR-1451 follow-up that re-pointed the S-meter
  // formatting at the shared `smeter-scale.ts` helpers — the module still
  // carries no non-finite display guard.
  it('leaves mobile-layout-logic.ts byte-identical', () => {
    const bytes = readFileSync(`${LAYOUT_DIR}mobile-layout-logic.ts`);
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(
      'edbfd96547b1a1ba3e8d4e24884fec0ccd0adb37d6515047a23969fdc2fe0bb6',
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
  // MOR-2675: an unread value renders EMPTY in its reserved slot — never a
  // dash run, and never a fabricated zero-signal "S9" (the state-adapter's
  // old stand-in for a receiver that has reported nothing).
  it('renders an unobserved landscape S-meter as an empty reserved slot, never a dash', () => {
    setViewport(844, 390);
    const readout = mountLayout().querySelector('.m-ls-smeter')?.textContent ?? '';
    expect(readout).toBe('');
    expect(readout).not.toContain('---');
    expect(readout).not.toContain('S9');
  });

  // Kills: dropping the finite guard from the landscape dBm readout —
  // `formatDbm(NaN)` renders the literal "NaN dBm".
  // MOR-2675: unread is EMPTY — no digits and no 'dBm' unit without them.
  it('renders an unobserved landscape dBm readout as an empty reserved slot, never a dash', () => {
    setViewport(844, 390);
    const readout = mountLayout().querySelector('.m-ls-dbm')?.textContent ?? '';
    expect(readout).toBe('');
    expect(readout).not.toContain('---');
    expect(readout).not.toContain('dBm');
  });

  // Kills: restoring `toVfoProps`' fabricated 'USB' / 'FIL1' stand-ins by
  // re-pointing the VFO projection at the stale state-adapter twin.
  // MOR-2673: the unread sentinel is the empty string — never a dash run.
  it('shows empty mode and filter labels, not fabricated USB / FIL1', () => {
    const root = mountLayout();
    expect(root.querySelector('.m-vfo-mode')?.textContent).toBe('');
    expect(root.querySelector('.m-vfo-filter')?.textContent).toBe('');
  });

  // Kills: any guard that leaks "NaN" through a different portrait boundary —
  // the portrait deck is where the idle mobile screen actually lives.
  it('renders no "NaN" substring anywhere in portrait with nothing observed', () => {
    const text = mountLayout().textContent ?? '';
    expect(text).not.toContain('NaN');
  });

  // ── MOR-2675: the landscape readouts keep one width in every state ──

  // Kills: a reservation narrower than the widest text the formatter
  // renders over the sMeter wire domain (raw int 0–255). The widest text is
  // derived by RUNNING formatDbm(calibratedToDbm) over that whole domain
  // against the widest shipped calibration ladder — not read off one
  // radio's constants — and the reservation must cover exactly that text
  // (MOR-2705, the #3768 method).
  it('renders the widest S-unit and dBm texts the landscape readouts can hold', () => {
    setViewport(844, 390);
    const widestShippedLadder: SmeterCalibrationPoint[] = [
      { raw: 0, actual: -54, label: 'S0' },
      { raw: 120, actual: 0, label: 'S9' },
      { raw: 255, actual: 60, label: 'S9+60' },
    ];
    vi.mocked(getSmeterCalibration).mockReturnValue(widestShippedLadder);
    try {
      radioStore.current = { active: 'MAIN', main: { ...CONNECTED_RX, sMeter: 255 } };
      const root = mountLayout();
      // Known values, exact text and spaces included.
      expect(root.querySelector('.m-ls-smeter')?.textContent).toBe('S9+60');
      expect(root.querySelector('.m-ls-dbm')?.textContent).toBe('−13 dBm');
      // Geometry: the reservations must cover those widest texts.
      const reservedS = mobileLayoutSource.match(
        /\.m-ls-smeter \{[^}]*min-width: (\d+)ch;/,
      )?.[1];
      expect(reservedS).toBe(String('S9+60'.length));
      // MOR-2705: the dBm slot's widest text no longer includes the
      // 'uncalibrated' word — the formatter renders nothing for an
      // uncomputable dBm. The widest REAL text is derived below by RUNNING
      // the formatter over the ladder's own calibrated-dB domain (the
      // sMeter line carries calibrated dB-rel-S9 inside [min, max]), not a
      // radio constant.
      const domainMin = widestShippedLadder[0].actual;
      const domainMax = widestShippedLadder[widestShippedLadder.length - 1].actual;
      let widestDbm = '';
      for (let value = domainMin; value <= domainMax; value += 1) {
        const text = formatDbmPure(calibratedToDbmPure(value, widestShippedLadder));
        if (text.length > widestDbm.length) widestDbm = text;
      }
      expect(widestDbm).toBe('−127 dBm');
      const reservedDbm = mobileLayoutSource.match(
        /\.m-ls-dbm \{[^}]*min-width: (\d+)ch;/,
      )?.[1];
      expect(reservedDbm).toBe(String(widestDbm.length));
    } finally {
      vi.mocked(getSmeterCalibration).mockReturnValue(null);
    }
  });

  // Kills: printing the 'uncalibrated' word where no dBm is computable —
  // per profile THAT is fixed when capabilities load, so the slot simply
  // stays empty and nothing moves (MOR-2705). The S-unit text keeps its raw
  // reading.
  it('renders no dBm readout at all on an uncalibrated radio (MOR-2705)', () => {
    setViewport(844, 390);
    radioStore.current = { active: 'MAIN', main: { ...CONNECTED_RX, sMeter: 255 } };
    const root = mountLayout();
    expect(root.querySelector('.m-ls-smeter')?.textContent).toBe('255');
    expect(root.querySelector('.m-ls-dbm')?.textContent).toBe('');
  });

  // Geometry: jsdom has no layout, so the reservations are pinned
  // structurally against the component source (the MOR-2658/MOR-2667
  // pattern): tabular digits, and the MOR-2657 zero-width strut that keeps
  // the empty unread boxes' line-box metrics in the baseline-aligned
  // `.m-ls-meter` flex row. The RIT badge always carries its label text,
  // so it needs no strut — only the reserved width.
  it('keeps the landscape readouts and the RIT badge reserved and tabular in every state', () => {
    expect(mobileLayoutSource).toMatch(/\.m-ls-smeter \{[^}]*tabular-nums/);
    expect(mobileLayoutSource).toMatch(/\.m-ls-smeter:empty::before \{ content: '\\200b'; \}/);
    expect(mobileLayoutSource).toMatch(/\.m-ls-dbm \{[^}]*tabular-nums/);
    expect(mobileLayoutSource).toMatch(/\.m-ls-dbm:empty::before \{ content: '\\200b'; \}/);
    // 'RIT +9999' / 'XIT -9999' — 9ch over the shared ±9999 Hz RIT domain.
    expect(mobileLayoutSource).toMatch(/\.m-vfo-rit \{[^}]*min-width: 9ch;/);
    expect(mobileLayoutSource).toMatch(/\.m-vfo-rit \{[^}]*tabular-nums/);
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
  // MOR-2675: the unread offset renders EMPTY after its label — never a
  // dash run — with the space kept in the label's own text node.
  it('renders an unobserved RIT offset as an empty value after the label, never a dash', () => {
    radioStore.current = { active: 'MAIN', main: CONNECTED_RX, ritOn: true };
    const badge = mountLayout().querySelector('.m-vfo-rit')?.textContent ?? '';
    expect(badge).toBe('RIT ');
    expect(badge).not.toContain('---');
    expect(badge).not.toContain('NaN');
  });

  // Kills: dropping the same guard on the XIT branch.
  it('renders an unobserved XIT offset as an empty value after the label, never a dash', () => {
    radioStore.current = { active: 'MAIN', main: CONNECTED_RX, ritTx: true };
    const badge = mountLayout().querySelector('.m-vfo-rit')?.textContent ?? '';
    expect(badge).toBe('XIT ');
    expect(badge).not.toContain('---');
    expect(badge).not.toContain('NaN');
  });

  // Kills: a guard so broad it hides a real offset — a reported ritFreq is
  // a genuine reading and must render label, space, sign and digits intact
  // (the known-state text is pinned with its exact spaces, MOR-2675).
  it('renders a known RIT offset exactly, spaces included', () => {
    radioStore.current = {
      active: 'MAIN', main: CONNECTED_RX, ritOn: true, ritFreq: 1200,
    };
    const badge = mountLayout().querySelector('.m-vfo-rit')?.textContent ?? '';
    expect(badge).toBe('RIT +1200');
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
    txHarness.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on' });
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

// ───────────────────────────────────────────────────────────────────────────
// Phone TX power and SWR (MOR-2658): unread is empty in a reserved slot,
// never '—', and never a unit ('W', 'SWR') without its number.
// ───────────────────────────────────────────────────────────────────────────

describe('MobileRadioLayout unread TX power and SWR (MOR-2658)', () => {
  function openTxChip(root: HTMLElement): void {
    const txChip = Array.from(root.querySelectorAll<HTMLButtonElement>('button'))
      .find((b) => b.textContent?.trim() === 'TX');
    expect(txChip, 'TX chip is present when hasTx=true').toBeDefined();
    txChip!.click();
    flushSync();
  }

  // Kills: the '—' fallback on the phone TX power readout. A radio whose
  // powerLevel field is structurally unavailable shows an empty reserved
  // box, not a dash.
  // NOTE: a connected radio with NO fieldStatus entry reads as available
  // (the legacy no-entry fallback in field-status.ts). Before MOR-2658
  // that branch rendered toTxProps' batch-B 0.5 stand-in (an explicit A12
  // non-fix) as "50W", so only the unavailable branch below was pinned;
  // the source now reports NaN and the connected-but-unobserved branch
  // has its own pin next.
  it('renders a structurally-unavailable TX power as an empty reserved slot, never a dash', () => {
    radioStore.current = {
      active: 'MAIN',
      main: CONNECTED_RX,
      fieldStatus: { powerLevel: { observed: false, freshness: 'missing', availability: 'unavailable' } },
    };
    const root = mountLayout();
    openTxChip(root);
    const power = root.querySelector('.m-tx-power-value')!;
    expect(power).not.toBeNull();
    expect(power.textContent).toBe('');
    expect(root.textContent ?? '').not.toContain('—');
  });

  // Kills: a fabricated "50W" for a connected-but-unobserved rig — with no
  // fieldStatus entry the legacy no-entry fallback reads as available, so
  // the old `?? 0.5` arrived as a reading and printed "50W". toTxProps now
  // reports NaN and the readout stays empty (MOR-2658).
  it('renders a connected-but-unobserved TX power as an empty slot, never 50W', () => {
    radioStore.current = { active: 'MAIN', main: CONNECTED_RX };
    const root = mountLayout();
    openTxChip(root);
    expect(root.querySelector('.m-tx-power-value')?.textContent).toBe('');
  });

  // Kills: a guard so broad it hides a real reading — powerLevel 0.5 is a
  // genuine report and must still render 50W.
  it('renders a known TX power exactly as before', () => {
    radioStore.current = { active: 'MAIN', main: CONNECTED_RX, powerLevel: 0.5 };
    const root = mountLayout();
    openTxChip(root);
    expect(root.querySelector('.m-tx-power-value')?.textContent).toBe('50W');
  });

  // Kills: the power sheet slider printing "NaN%" for an unread rig —
  // `normalizedPercentDisplay(NaN)` is the literal "NaN%", so the sheet
  // needs the same finite guard TxPanel's `rfPowerDisplay` carries.
  // BottomSheet/ValueControl are stubbed in this file, so the pin is on
  // the exact display prop the sheet passes; the unknown-path rendering
  // itself ('' text, no fill, no position) is pinned behaviorally through
  // the same ValueControl construct in TxPanel.isolated.test.ts (MOR-2658).
  it('passes an unread-safe display to the power sheet slider', () => {
    const sheet = mobileLayoutSource.slice(mobileLayoutSource.indexOf('POWER MODAL'));
    expect(sheet.match(/displayFn=\{([^}]*)\}/)?.[1]).toBe('formatRfPowerDisplay');
  });

  // Kills: `swr > 0` treating a real reading of 0 as absent — an unread SWR
  // is Number.NaN from toMeterProps, never the number 0, so 0 must render.
  // The SWR line shows only while transmitting (managedTxRf === 'on').
  it('renders a real SWR reading of 0 while transmitting, and stays empty when unread', () => {
    txHarness.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on' });
    radioStore.current = {
      active: 'MAIN', main: CONNECTED_RX, ptt: true,
      powerMeter: 120, swrMeter: 0, alcMeter: 40,
    };
    const read = mountLayout();
    openTxChip(read);
    expect(read.querySelector('.m-tx-swr-value')?.textContent).toBe('SWR 0.0');
    if (instance) unmount(instance);
    instance = null;
    if (host) host.remove();

    radioStore.current = { active: 'MAIN', main: CONNECTED_RX, ptt: true };
    const unread = mountLayout();
    openTxChip(unread);
    // MOR-2658: no unit without its number — the slot stays reserved but the
    // 'SWR' word renders only with its reading.
    expect(unread.querySelector('.m-tx-swr-value')?.textContent).toBe('');
    expect(unread.textContent ?? '').not.toContain('—');
  });

  // Kills: a missing reserved box — the power and SWR slots keep their width
  // for unread AND each known value, sized for the widest text ('100W').
  it('reserves the TX power and SWR slots structurally', () => {
    expect(mobileLayoutSource).toMatch(/\.m-tx-power-value \{[^}]*min-width:/);
    expect(mobileLayoutSource).toMatch(/\.m-tx-swr-value \{[^}]*min-width:/);
  });

  // Kills: an SWR slot narrower than its widest text. swrMeter is raw
  // 0-255 (`src/rigplane/backends/yaesu_cat/radio.py` `get_swr_meter`;
  // the wire field is `swrMeter` in `src/rigplane/web/state_schema.py`),
  // so the slot's `(swr / 10).toFixed(1)` maxes
  // out at "SWR 25.5" (8 chars) — the reservation must cover exactly that
  // (MOR-2658).
  it('sizes the SWR slot to its widest text', () => {
    const widest = `SWR ${(255 / 10).toFixed(1)}`;
    expect(widest).toBe('SWR 25.5');
    const reserved = mobileLayoutSource.match(/\.m-tx-swr-value \{[^}]*min-width: (\d+)ch;/)?.[1];
    expect(reserved).toBe(String(widest.length));
  });

  // Kills: a widest-text claim the slot never actually renders — a rig
  // reporting the raw maximum must show that exact text while transmitting.
  it('renders the widest SWR text the slot can hold', () => {
    txHarness.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on' });
    radioStore.current = {
      active: 'MAIN', main: CONNECTED_RX, ptt: true,
      powerMeter: 255, swrMeter: 255, alcMeter: 40,
    };
    const root = mountLayout();
    openTxChip(root);
    expect(root.querySelector('.m-tx-swr-value')?.textContent).toBe('SWR 25.5');
  });
});
