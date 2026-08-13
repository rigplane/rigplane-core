/**
 * MOR-1562 (C8) — adapter-seam conformance walk over the live IC-7300
 * fixture.
 *
 * MOR-1426's family walks (C6-C13) split `panel-commands.ts`'s intent
 * surface across tickets; C8 is the ONE family that does not walk intents
 * at all. It walks the ADAPTER SEAMS one layer up — the `derive*Props`/
 * `get*Handlers` exports in `lib/runtime/adapters/` that panels actually
 * call (`$derived(deriveModeProps())`, `getModeHandlers()` once at init,
 * per `panel-adapters.ts`'s own header) — against the SAME live-captured
 * `ic7300-{state,capabilities}.json` fixture `mor1428-ic7300-conformance.
 * isolated.test.ts` established, via the shared `./conformance/harness.ts`.
 * This is why `claimed.ts` gains ZERO new entries here (see its own
 * comment) — every intent this file's `expectFrames`/`expectRefusal` calls
 * exercise was already claimed by MOR-1428 through the same
 * `dispatchRadioIntent` factories; C8's contribution is proving the panel
 * adapter SEAM around them, not a new WS frame.
 *
 * WHY THESE SEAMS WERE ZERO-COVERAGE BEFORE THIS FILE: every existing
 * `panel-adapters.ts` consumer test (`mor1519-mode-armed`,
 * `mor1536-armed-adoption`, `mor1441-pending-*`, `mor1409-a15-active-
 * frequency`, `memory-handler-binder`, `keyboard-system-accessors`,
 * `semantic-surface-handler-binder`) builds its OWN synthetic per-test
 * state via a private `vi.mock`, never the committed live fixture. And the
 * `to*Props`-level unit tests (`rf-front-end-adapter.test.ts`,
 * `mode-filter-adapter.test.ts`, etc.) call `toXxxProps` directly with
 * hand-built `ServerState`/`Capabilities` literals — never through the
 * `derive*Props()` wrapper a real panel actually calls, and never against
 * a byte-faithful capture. `deriveModeProps`/`deriveAgcProps`/
 * `deriveRfFrontEndProps`/`deriveFilterProps`/`deriveBandSelectorProps`/
 * `deriveDspProps`/`deriveTxProps`/`deriveAntennaProps`/`deriveScanProps`/
 * `deriveCwProps`/`deriveRitXitProps`, and the entire `capabilities-
 * adapter.ts` surface, had literally no test importing them before this
 * file (verified by grep — see PR body). `presentation-capabilities.ts` has
 * a synthetic-fixture unit test but had never run over the live ic7300
 * caps either.
 *
 * HARNESS EXTENSION (see `./conformance/harness.ts`'s own MOR-1562 comment):
 * `derive*Props` reads `runtime.state`/`runtime.caps` (the
 * `FrontendRuntime` singleton), a seam the pre-C8 harness never stubbed —
 * extended with two getters backed by the same `h.state`/`h.caps` every
 * other mock here reads. `capabilities-adapter.ts` also needed
 * `getMeterCalibration`/`getMeterRedline` added to the capabilities-store
 * mock (only `getControlRange` existed). Both extensions mirror the real
 * implementations exactly — no new behavior invented, see the harness
 * comment for the line-by-line justification.
 *
 * SCOPE DECISION — get*Handlers WIRING: most `get*Handlers` exports in
 * `panel-adapters.ts` are bare singletons (`const _x = makeXHandlers();
 * export function getXHandlers() { return _x; }`) with no adapter logic of
 * their own — dispatch-testing them is not meaningfully different from
 * testing `makeXHandlers()` directly (already exhaustively done for 10+
 * families by MOR-1428). This file's handler-dispatch section covers only
 * the seam objects with genuinely NO prior get*Handlers-level coverage
 * anywhere (Mode/AGC/Filter/Band/DSP dispatch, TX honest refusal) — proving
 * the exact object a panel imports wires correctly, not re-deriving
 * MOR-1428's frame-shape assertions. `getRfFrontEndHandlers` (the one
 * wrapper with real logic — MOR-1447's normalized-to-raw conversion) is
 * already fully covered by MOR-1428 and not repeated here.
 *
 * DEFERRED (explicit follow-up scope, not silent gaps — full accounting in
 * PR body): RitXit/Scan/Cw/Antenna/Vfo/Memory/AudioRouting/Preset/Keyboard/
 * System `get*Handlers` ONLY — not `derive*Props`, `deriveRitXitProps` IS
 * covered below (bare passthroughs, see SCOPE DECISION above); the
 * armed-signal family (fully covered by `mor1536-armed-adoption` +
 * `mor1519-mode-armed`, just not this fixture); getPendingXxx
 * (`mor1441-pending-*`); getActiveFrequencyHz (`mor1409-a15-active-
 * frequency`); bindSemanticSurfaceHandlers/bindVfoTunerContext
 * (`semantic-surface-handler-binder`); deriveAmberScopeProps/
 * deriveAmberCockpitProps/getAmberCockpitHandlers (need a THIRD harness
 * extension — `hasAudioFft`/`hasDualReceiver`/`hasCapability` are unmocked
 * today, out of scope); deriveMemoryPanelProps/deriveVfoControlProps/
 * deriveAudioSpectrumProps/deriveAmberTelemetryProps; every non-panel-
 * adapters seam (audio-adapter.ts, qsy-history-adapter.ts, vfo-adapter.ts,
 * tx-adapter.ts, tx-capabilities.ts, lcd-chrome-adapter.ts, mod-input-*,
 * scope-adapter.ts — its `toSpectrumAuthority` is walked by MOR-1428).
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  expectFrames,
  expectRefusal,
  fixtureCaps,
  fixtureState,
  h,
} from './conformance/harness';
import { PROFILES } from './conformance/profiles';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import {
  deriveModeProps, getModeHandlers,
  deriveAgcProps, getAgcHandlers,
  deriveRfFrontEndProps,
  deriveFilterProps, getFilterHandlers,
  deriveBandSelectorProps, getBandHandlers,
  deriveDspProps, getDspHandlers,
  deriveTxProps, getTxHandlers,
  deriveAntennaProps,
  deriveScanProps,
  deriveCwProps,
  deriveRitXitProps,
} from '../panel-adapters';
import {
  getMeterCalibration, getMeterRedline, getControlRange, getReceiverLabel,
} from '../capabilities-adapter';
import { derivePresentationCapabilities } from '../presentation-capabilities';

const profile = PROFILES.ic7300;
const IC7300_STATE = profile.state;
const IC7300_CAPABILITIES = profile.caps;

beforeEach(() => {
  h.state = fixtureState(profile);
  h.caps = fixtureCaps(profile);
  h.sendCommand.mockClear();
});

afterEach(() => {
  resetCommandLifecycle();
});

/* ── capabilities-adapter.ts — full 4-export surface, zero prior coverage ── */

describe('capabilities-adapter.ts over the live ic7300 caps (MOR-1562)', () => {
  it('getMeterCalibration: exact swr knot points from the live capture', () => {
    expect(IC7300_CAPABILITIES.meterCalibrations?.swr).toBeDefined();
    expect(getMeterCalibration('swr')).toEqual(IC7300_CAPABILITIES.meterCalibrations!.swr);
  });

  it('getMeterCalibration: honest null — this profile declares no ALC calibration (capability-not-declared refusal)', () => {
    expect(IC7300_CAPABILITIES.meterCalibrations?.alc).toBeUndefined();
    expect(getMeterCalibration('alc')).toBeNull();
  });

  it('getMeterRedline: exact swr redline', () => {
    expect(getMeterRedline('swr')).toBe(120);
  });

  it('getMeterRedline: honest null for ALC', () => {
    expect(getMeterRedline('alc')).toBeNull();
  });

  it('getControlRange: exact af_level raw range', () => {
    expect(getControlRange('af_level')).toEqual({ raw_min: 0, raw_max: 255 });
  });

  it('getControlRange: honest null — nb_depth control is not declared on this profile', () => {
    expect(IC7300_CAPABILITIES.controls?.nb_depth).toBeUndefined();
    expect(getControlRange('nb_depth')).toBeNull();
  });

  it.each(['MAIN', 'SUB'] as const)('getReceiverLabel(%s): passthrough contract', (id) => {
    expect(getReceiverLabel(id)).toBe(id);
  });
});

/* ── presentation-capabilities.ts — over the live caps, zero prior live
 * coverage (existing unit test uses synthetic caps only). */

describe('presentation-capabilities.ts over the live ic7300 caps (MOR-1562)', () => {
  it('resolves a clean single-RX A/B topology with zero diagnostics', () => {
    expect(derivePresentationCapabilities(IC7300_CAPABILITIES)).toEqual({
      topology: {
        scheme: 'ab',
        structuralCount: 1,
        structuralReceivers: ['MAIN'],
        operationalReceivers: ['MAIN'],
        slots: { MAIN: ['A', 'B'] },
      },
      scope: {
        hardwareScopeAvailable: true,
        audioFftAvailable: true,
        availableSources: ['hardware', 'audio_fft'],
        defaultSource: 'hardware',
      },
      diagnostics: [],
    });
  });

  it('flags scope-capability-contradiction when scope tag and caps.scope disagree', () => {
    const contradictory = { ...IC7300_CAPABILITIES, scope: false };
    expect(derivePresentationCapabilities(contradictory).diagnostics)
      .toContain('scope-capability-contradiction');
  });
});

/* ── panel-adapters.ts derive*Props against the live fixture — exact
 * values, zero prior coverage of this seam (see file header). */

describe('panel-adapters.ts derive*Props over the live ic7300 fixture (MOR-1562)', () => {
  it('deriveModeProps: exact mode/catalog/data-mode reading', () => {
    expect(deriveModeProps()).toMatchObject({
      currentMode: 'USB',
      modes: IC7300_CAPABILITIES.modes,
      dataMode: 0,
      hasDataMode: true,
    });
  });

  it('deriveAgcProps: exact AGC reading + capability-derived catalog', () => {
    expect(deriveAgcProps()).toEqual({
      agcMode: 3,
      agcModes: [1, 2, 3],
      agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
      hasAgc: true,
    });
  });

  it('deriveRfFrontEndProps: exact rf-chain reading, honest ipPlus/digiSel refusal (two flavors)', () => {
    // ip_plus IS a declared capability, but this session never observed the
    // field — unobserved-field refusal (mirrors MOR-1428's RIT case).
    expect(IC7300_CAPABILITIES.capabilities).toContain('ip_plus');
    expect(IC7300_STATE.fieldStatus?.['main.ipplus']?.observed).toBe(false);
    // digisel is NOT a declared capability on this profile at all —
    // capability-not-declared refusal, a different flavor from the above.
    expect(IC7300_CAPABILITIES.capabilities).not.toContain('digisel');

    expect(deriveRfFrontEndProps()).toMatchObject({
      rfGain: 0.8196078431372549,
      squelch: 0,
      att: 0,
      pre: 0,
      showRfGain: true,
      showSquelch: true,
      showAtt: true,
      showPre: true,
      attValues: [0, 20],
      preOptions: [
        { value: 0, label: 'OFF' },
        { value: 1, label: 'P1' },
        { value: 2, label: 'P2' },
      ],
      showIpPlus: false,
      showDigiSel: false,
    });
  });

  it('deriveFilterProps: exact filter selection + honest if_shift/pbt capability split', () => {
    // 'if_shift' is not declared at all (Icom PBT-only radio); 'pbt' is.
    expect(IC7300_CAPABILITIES.capabilities).not.toContain('if_shift');
    expect(IC7300_CAPABILITIES.capabilities).toContain('pbt');

    expect(deriveFilterProps()).toMatchObject({
      currentMode: 'USB',
      currentFilter: 1,
      hasFilterShape: true,
      hasIfShift: false,
      hasPbt: true,
    });
  });

  it('deriveBandSelectorProps: exact active-VFO frequency', () => {
    expect(deriveBandSelectorProps()).toEqual({ currentFreq: 14_188_000 });
  });

  it('deriveDspProps: exact NB/NR reading, honest nb-depth/notch/agc-time refusal (two flavors)', () => {
    // nb_depth control range is NOT declared on this profile at all —
    // capability-not-declared refusal.
    expect(IC7300_CAPABILITIES.controls?.nb_depth).toBeUndefined();
    // 'notch' IS a declared capability, but manualNotch/autoNotch/
    // agcTimeConstant were never observed this session — unobserved-field
    // refusal, the other flavor.
    expect(IC7300_CAPABILITIES.capabilities).toContain('notch');
    expect(IC7300_STATE.fieldStatus?.['main.manualNotch']?.observed).toBe(false);

    expect(deriveDspProps()).toMatchObject({
      nrMode: 1,
      nbActive: true,
      hasNr: true,
      hasNb: true,
      hasNbDepth: false,
      hasNbWidth: false,
      nbLevelPercent: true,
      nbLevelMax: 255,
      notchMode: 'off',
      hasNotch: false,
      hasAutoNotch: false,
      hasAgcTime: false,
    });
  });

  it('deriveTxProps: honest micGain refusal — capability declared, field never observed on this stand', () => {
    expect(IC7300_STATE.fieldStatus?.micGain?.observed).toBe(false);
    expect(deriveTxProps()).toMatchObject({
      hasTx: true,
      micGainAvailable: false,
    });
  });

  it('deriveAntennaProps: honest rx_antenna refusal — capability-not-declared', () => {
    expect(IC7300_CAPABILITIES.capabilities).not.toContain('rx_antenna');
    expect(deriveAntennaProps()).toEqual({
      txAntenna: 1, rxAnt: false, antennaCount: 1, hasRxAntenna: false,
    });
  });

  it('deriveScanProps: exact idle-scan reading', () => {
    expect(deriveScanProps()).toEqual({ scanning: false, scanType: 0, scanResumeMode: 0 });
  });

  it('deriveCwProps: mode-gated APF/TPF disable + full capability catalog for the live USB reading', () => {
    expect(deriveCwProps()).toMatchObject({
      currentMode: 'USB',
      apfDisabled: true, // active mode is USB, not CW/CW-R
      tpfDisabled: true, // active mode is USB, not RTTY/RTTY-R
      hasCw: true,
      hasBreakIn: true,
      hasApf: true,
      hasTwinPeak: true,
    });
  });

  // MOR-1574 (filed off this walk): unlike toAgcProps/toRfFrontEndProps,
  // `toRitXitProps` (panel-props.ts:482-494) has NO fieldStatus gate on
  // ritOn/ritFreq/ritTx — pinned CURRENT baseline, not an endorsement;
  // update once MOR-1574 lands.
  it('deriveRitXitProps: pins the current un-gated reading — production honesty gap tracked as MOR-1574', () => {
    expect(IC7300_STATE.fieldStatus?.ritOn?.observed).toBe(false);
    expect(IC7300_STATE.fieldStatus?.ritFreq?.observed).toBe(false);
    expect(IC7300_STATE.fieldStatus?.ritTx?.observed).toBe(false);
    expect(deriveRitXitProps()).toEqual({
      ritActive: false, ritOffset: 0, xitActive: false, xitOffset: 0,
      hasRit: true, hasXit: true,
    });
  });
});

/* ── panel-adapters.ts get*Handlers wiring — the exact singleton object a
 * panel imports, for seams with zero prior get*Handlers-level coverage (see
 * SCOPE DECISION in the file header for why most families stop at
 * derive*Props above). */

describe('panel-adapters.ts get*Handlers dispatch through the real seam (MOR-1562)', () => {
  it('getModeHandlers: dispatches set_mode on receiver 0', () => {
    expectFrames(
      () => getModeHandlers().onModeChange('CW'),
      [['set_mode', { mode: 'CW', receiver: 0 }]],
    );
  });

  it('getAgcHandlers: dispatches set_agc on receiver 0', () => {
    expectFrames(
      () => getAgcHandlers().onAgcModeChange(2),
      [['set_agc', { mode: 2, receiver: 0 }]],
    );
  });

  it('getFilterHandlers: dispatches set_filter on receiver 0', () => {
    expectFrames(
      () => getFilterHandlers().onFilterChange(2),
      [['set_filter', { filter: 2, receiver: 0 }]],
    );
  });

  it('getBandHandlers: dispatches set_band from a BSR-coded band select', () => {
    expectFrames(
      () => getBandHandlers().onBandSelect('20m', 14_225_000, 5),
      [['set_band', { band: 5 }]],
    );
  });

  it('getDspHandlers: dispatches set_nb on receiver 0', () => {
    expectFrames(
      () => getDspHandlers().onNbToggle(false),
      [['set_nb', { on: false, receiver: 0 }]],
    );
  });

  it('getTxHandlers: REFUSES onMicGainChange — same doctrine as MOR-1428, through the untested singleton seam', () => {
    expect(IC7300_STATE.fieldStatus?.micGain?.observed).toBe(false);
    expectRefusal(() => getTxHandlers().onMicGainChange(100));
  });
});
