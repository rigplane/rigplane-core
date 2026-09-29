/**
 * MOR-2979 — legacy toggle keys expose the confirmed on/off and refuse
 * unread readings: RfFrontEnd (ATT/DIGI-SEL/IP+), Tx (ATU/TUNE/VOX/COMP/MON),
 * RIT/XIT, Scan STOP, Essentials (SPLIT/NB/NR/NOTCH) and DSP (NB/NR/NOTCH/A-NOTCH).
 *
 * Per key: a known reading renders `aria-pressed` "true"/"false" on the
 * confirmed state; an unread reading renders no `aria-pressed` and a
 * disabled key, matching the shared handlers' refusal. One file on purpose
 * (the ticket's 10-file lease): the panels share one mocked adapter seam,
 * the same convention as `mor-2978-choice-keys-pressed.isolated.test.ts`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { setLocale } from '$lib/i18n';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import type { CommandScalarFeedback } from '../../../primitives/scalar/continuous-scalar.svelte';

// ── Mock state (one vi.mock factory serves all six panels) ──

const mockRfProps = {
  rfGain: 1, squelch: 0, att: 0, pre: 0, digiSel: false, ipPlus: false,
  rfGainAvailable: false, squelchAvailable: false,
  attAvailable: true, preAvailable: true, digiSelAvailable: true, ipPlusAvailable: true,
  attValues: [0, 20], attLabels: {} as Record<string, string>,
  preValues: [0, 1], preOptions: [{ value: 0, label: 'OFF' }, { value: 1, label: 'P1' }],
  showRfGain: false, showSquelch: false, showAtt: true, showPre: false,
  preDisabled: false, preDisabledReason: '', showDigiSel: true, showIpPlus: true,
};
const mockRfHandlers = {
  onRfGainChange: vi.fn(), onSquelchChange: vi.fn(), onAttChange: vi.fn(),
  onPreChange: vi.fn(), onDigiSelToggle: vi.fn(), onIpPlusToggle: vi.fn(),
};

const mockTxProps = {
  txActive: false, txActiveAvailable: true, rfPower: 0.5,
  atuActive: false, atuTuning: false, atuAvailable: true,
  voxActive: false, voxAvailable: true, hasVox: true,
  compActive: false, compLevel: 0, compAvailable: true, hasComp: true,
  monActive: false, monLevel: 0, monAvailable: true,
  hasTx: true, hasTuner: true, hasMonitor: true,
  rfPowerAvailable: true, micGainAvailable: true,
  compLevelAvailable: true, monLevelAvailable: true, driveGainAvailable: true,
};
const mockTxHandlers = {
  onRfPowerChange: vi.fn(), onMicGainChange: vi.fn(),
  onAtuToggle: vi.fn(), onAtuTune: vi.fn(), onVoxToggle: vi.fn(),
  onCompToggle: vi.fn(), onCompLevelChange: vi.fn(),
  onMonToggle: vi.fn(), onMonLevelChange: vi.fn(), onDriveGainChange: vi.fn(),
};

const mockRitXitProps = {
  ritActive: false, ritOffset: 0, xitActive: false, xitOffset: 0,
  hasRit: true, hasXit: true, ritDomain: undefined as unknown,
  ritKnown: true, xitKnown: true,
};
const mockRitXitHandlers = {
  onRitToggle: vi.fn(), onXitToggle: vi.fn(),
  onRitOffsetChange: vi.fn(), onXitOffsetChange: vi.fn(), onClear: vi.fn(),
};

const mockScanProps = {
  scanning: false, scanType: 0x01, scanResumeMode: 0, scanningKnown: true,
};
const mockScanHandlers = {
  onScanStart: vi.fn(), onScanStop: vi.fn(),
  onDfSpanChange: vi.fn(), onResumeChange: vi.fn(),
};

const mockDspProps = {
  nrMode: 0, nrLevel: 5, nbActive: false, nbLevel: 128,
  notchMode: 'off' as string, notchFreq: 1000,
  nbDepth: 0, nbWidth: 0, manualNotchWidth: 0, agcTimeConstant: 0,
  hasNr: true, hasNb: true, hasNbDepth: false, hasNbWidth: false,
  nbLevelMax: 255, nbLevelPercent: true,
  hasNotch: true, hasAutoNotch: true, hasAgcTime: false,
  nbKnown: true, nrKnown: true, manualNotchKnown: true, autoNotchKnown: true,
};
const mockDspHandlers = {
  onNrModeChange: vi.fn(), onNrLevelChange: vi.fn(), onNbToggle: vi.fn(),
  onNbLevelChange: vi.fn(), onNotchModeChange: vi.fn(), onNotchFreqChange: vi.fn(),
  onNbDepthChange: vi.fn(), onNbWidthChange: vi.fn(),
  onManualNotchWidthChange: vi.fn(), onAgcTimeChange: vi.fn(),
};

function idleFeedback(control: string): Readonly<CommandScalarFeedback> {
  return {
    confirmed: 0.5, target: null, requestedTarget: null, phase: 'idle', busy: false,
    availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration: 1, sessionEpoch: 7,
    scope: { control, receiver: 0 }, repeatPolicy: 'latest-target-wins',
  } as Readonly<CommandScalarFeedback>;
}

function unavailableFeedback(control: string): Readonly<CommandScalarFeedback> {
  return {
    confirmed: null, target: null, requestedTarget: null, phase: 'unavailable', busy: false,
    availability: 'unavailable', outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration: null, sessionEpoch: -1,
    scope: { control, receiver: 0 }, repeatPolicy: 'latest-target-wins',
  } as Readonly<CommandScalarFeedback>;
}

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveRfFrontEndProps: () => mockRfProps,
  getRfFrontEndHandlers: () => mockRfHandlers,
  getPreampArmed: () => ({ armed: false, value: null }),
  getAttenuatorArmed: () => ({ armed: false, value: null }),
  getRfSqlControlFeedback: () => null,
  deriveTxProps: () => mockTxProps,
  getTxHandlers: () => mockTxHandlers,
  getTxAuxControlFeedback: () => idleFeedback('tx-aux'),
  getRfPowerControlFeedback: () => idleFeedback('rf-power'),
  deriveRitXitProps: () => mockRitXitProps,
  getRitXitHandlers: () => mockRitXitHandlers,
  deriveScanProps: () => mockScanProps,
  getScanHandlers: () => mockScanHandlers,
  deriveDspProps: () => ({ ...mockDspProps }),
  getDspHandlers: () => mockDspHandlers,
  getAutoNotchArmed: () => ({ armed: false, value: null }),
  getManualNotchArmed: () => ({ armed: false, value: null }),
  getDspControlFeedback: (field: string) => unavailableFeedback(field),
  getAfLevelControlFeedback: () => idleFeedback('af-level'),
}));

const txHost = vi.hoisted(() => ({ current: undefined as unknown }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
vi.mock('$lib/runtime/adapters/mod-input-auto.svelte', () => ({
  deriveAutoLanModInputProps: () => ({ available: false, enabled: false }),
  setAutoLanModInputEnabled: vi.fn(),
}));

import RfFrontEnd from '../RfFrontEnd.svelte';
import TxPanel from '../TxPanel.svelte';
import RitXitPanel from '../RitXitPanel.svelte';
import ScanPanel from '../ScanPanel.svelte';
import DspPanel from '../DspPanel.svelte';
import EssentialsPanel from '../EssentialsPanel.svelte';

let components: ReturnType<typeof mount>[] = [];
let tx: ManagedAppTxHarness;

beforeEach(() => {
  components = [];
  setLocale('en-US');
  tx = new ManagedAppTxHarness();
  txHost.current = tx.controller as unknown;
  Object.assign(mockRfProps, {
    att: 0, digiSel: false, ipPlus: false,
    attAvailable: true, digiSelAvailable: true, ipPlusAvailable: true,
    showAtt: true, showDigiSel: true, showIpPlus: true,
  });
  Object.assign(mockTxProps, {
    atuActive: false, atuTuning: false, atuAvailable: true,
    voxActive: false, voxAvailable: true, hasVox: true,
    compActive: false, compLevel: 0, compAvailable: true, hasComp: true,
    monActive: false, monLevel: 0, monAvailable: true,
    hasTuner: true, hasMonitor: true,
  });
  Object.assign(mockRitXitProps, {
    ritActive: false, xitActive: false, hasRit: true, hasXit: true,
    ritKnown: true, xitKnown: true,
  });
  Object.assign(mockScanProps, { scanning: false, scanningKnown: true });
  Object.assign(mockDspProps, {
    nrMode: 0, nbActive: false, notchMode: 'off',
    hasNr: true, hasNb: true, hasNotch: true, hasAutoNotch: true,
    nbKnown: true, nrKnown: true, manualNotchKnown: true, autoNotchKnown: true,
  });
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
});

function mountPanel(Component: unknown, props?: Record<string, unknown>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  components.push(mount(Component as any, { target, props }));
  flushSync();
  return target;
}

function button(target: ParentNode, text: string): HTMLButtonElement {
  const found = Array.from(target.querySelectorAll<HTMLButtonElement>('button'))
    .find((b) => b.textContent?.trim() === text);
  if (!found) throw new Error(`button "${text}" not found`);
  return found;
}

function buttonStartsWith(target: ParentNode, prefix: string): HTMLButtonElement {
  const found = Array.from(target.querySelectorAll<HTMLButtonElement>('button'))
    .find((b) => b.textContent?.trim().startsWith(prefix));
  if (!found) throw new Error(`button starting with "${prefix}" not found`);
  return found;
}

const noop = () => {};

function essentialsProps(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    vfoOps: { splitActive: false, splitKnown: true },
    mode: { currentMode: 'USB', modes: [] },
    filter: { currentFilter: 1, filterLabels: [] },
    rxAudio: { monitorMode: 'local', afLevel: 0.5 },
    dsp: {
      nbActive: false, nrMode: 0, notchMode: 'off',
      nbKnown: true, nrKnown: true, manualNotchKnown: true, autoNotchKnown: true,
    },
    quickModes: [],
    onSplitToggle: noop, onSwap: noop, onEqual: noop, onModeChange: noop,
    onModeMore: noop, onFilterChange: noop, onFilterMore: noop,
    onMonitorModeChange: noop, onAfLevelChange: noop, onNbToggle: noop,
    onNrModeChange: noop, onNotchModeChange: noop,
    ...overrides,
  };
}

// ── RfFrontEnd ──

describe('RfFrontEnd toggle keys (MOR-2979)', () => {
  it('exposes the confirmed ATT state: "true" when on, "false" when off', () => {
    mockRfProps.att = 20;
    expect(button(mountPanel(RfFrontEnd), 'ATT').getAttribute('aria-pressed')).toBe('true');
    mockRfProps.att = 0;
    const target = mountPanel(RfFrontEnd);
    const key = button(target, 'ATT');
    expect(key.getAttribute('aria-pressed')).toBe('false');
    expect(key.disabled).toBe(false);
  });

  it('omits aria-pressed and disables ATT over an unread att', () => {
    Object.assign(mockRfProps, { att: 0, attAvailable: false, showAtt: true });
    const key = button(mountPanel(RfFrontEnd), 'ATT');
    expect(key.hasAttribute('aria-pressed')).toBe(false);
    expect(key.disabled).toBe(true);
  });

  it('exposes DIGI-SEL and IP+: "true" when on, "false" when off', () => {
    Object.assign(mockRfProps, { digiSel: true, ipPlus: true });
    const on = mountPanel(RfFrontEnd);
    expect(button(on, 'DIGI-SEL').getAttribute('aria-pressed')).toBe('true');
    expect(button(on, 'IP+').getAttribute('aria-pressed')).toBe('true');
    Object.assign(mockRfProps, { digiSel: false, ipPlus: false });
    const off = mountPanel(RfFrontEnd);
    expect(button(off, 'DIGI-SEL').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'IP+').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'DIGI-SEL').disabled).toBe(false);
    expect(button(off, 'IP+').disabled).toBe(false);
  });

  it('omits aria-pressed and disables DIGI-SEL/IP+ over unread readings', () => {
    Object.assign(mockRfProps, {
      digiSel: false, digiSelAvailable: false, showDigiSel: true,
      ipPlus: false, ipPlusAvailable: false, showIpPlus: true,
    });
    const target = mountPanel(RfFrontEnd);
    for (const label of ['DIGI-SEL', 'IP+']) {
      const key = button(target, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });
});

// ── TxPanel ──

describe('TxPanel toggle keys (MOR-2979)', () => {
  it('exposes ATU and TUNE: "true" when on, "false" when off', () => {
    Object.assign(mockTxProps, { atuActive: true, atuTuning: true });
    const on = mountPanel(TxPanel);
    expect(button(on, 'ATU').getAttribute('aria-pressed')).toBe('true');
    expect(button(on, 'TUNING…').getAttribute('aria-pressed')).toBe('true');
    Object.assign(mockTxProps, { atuActive: false, atuTuning: false });
    const off = mountPanel(TxPanel);
    expect(button(off, 'ATU').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'TUNE').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'ATU').disabled).toBe(false);
  });

  it('omits aria-pressed and disables ATU/TUNE over an unread tunerStatus', () => {
    Object.assign(mockTxProps, { atuActive: false, atuTuning: false, atuAvailable: false, hasTuner: true });
    const target = mountPanel(TxPanel);
    for (const label of ['ATU', 'TUNE']) {
      const key = button(target, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });

  it('exposes VOX, COMP and MON: "true" when on, "false" when off', () => {
    Object.assign(mockTxProps, { voxActive: true, compActive: true, monActive: true });
    const on = mountPanel(TxPanel);
    expect(button(on, 'VOX').getAttribute('aria-pressed')).toBe('true');
    expect(button(on, 'COMP').getAttribute('aria-pressed')).toBe('true');
    expect(button(on, 'MON').getAttribute('aria-pressed')).toBe('true');
    Object.assign(mockTxProps, { voxActive: false, compActive: false, monActive: false });
    const off = mountPanel(TxPanel);
    expect(button(off, 'VOX').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'COMP').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'MON').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'VOX').disabled).toBe(false);
  });

  it('omits aria-pressed and disables VOX/COMP/MON over unread readings', () => {
    Object.assign(mockTxProps, {
      voxActive: false, voxAvailable: false, hasVox: true,
      compActive: false, compAvailable: false, hasComp: true,
      monActive: false, monAvailable: false, hasMonitor: true,
    });
    const target = mountPanel(TxPanel);
    for (const label of ['VOX', 'COMP', 'MON']) {
      const key = button(target, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });
});

// ── RitXitPanel ──

describe('RitXitPanel toggle keys (MOR-2979)', () => {
  it('exposes RIT and XIT: "true" when on, "false" when off', () => {
    Object.assign(mockRitXitProps, { ritActive: true, xitActive: true });
    const on = mountPanel(RitXitPanel);
    expect(button(on, 'RIT').getAttribute('aria-pressed')).toBe('true');
    expect(button(on, 'XIT').getAttribute('aria-pressed')).toBe('true');
    Object.assign(mockRitXitProps, { ritActive: false, xitActive: false });
    const off = mountPanel(RitXitPanel);
    expect(button(off, 'RIT').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'XIT').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'RIT').disabled).toBe(false);
  });

  it('omits aria-pressed and disables RIT/XIT over unread readings', () => {
    Object.assign(mockRitXitProps, {
      ritActive: false, ritKnown: false, hasRit: true,
      xitActive: false, xitKnown: false, hasXit: true,
    });
    const target = mountPanel(RitXitPanel);
    for (const label of ['RIT', 'XIT']) {
      const key = button(target, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });
});

// ── ScanPanel ──

describe('ScanPanel STOP key (MOR-2979)', () => {
  it('exposes the confirmed scanning state: "true" while scanning, "false" when idle', () => {
    mockScanProps.scanning = true;
    expect(button(mountPanel(ScanPanel), 'STOP').getAttribute('aria-pressed')).toBe('true');
    mockScanProps.scanning = false;
    const target = mountPanel(ScanPanel);
    const key = button(target, 'STOP');
    expect(key.getAttribute('aria-pressed')).toBe('false');
    expect(key.disabled).toBe(false);
  });

  it('omits aria-pressed and disables STOP over an unread scanning state', () => {
    Object.assign(mockScanProps, { scanning: false, scanningKnown: false });
    const key = button(mountPanel(ScanPanel), 'STOP');
    expect(key.hasAttribute('aria-pressed')).toBe(false);
    expect(key.disabled).toBe(true);
  });
});

// ── DspPanel ──

describe('DspPanel toggle keys (MOR-2979)', () => {
  it('exposes NB and NR: "true" when on, "false" when off', () => {
    Object.assign(mockDspProps, { nbActive: true, nrMode: 1 });
    const on = mountPanel(DspPanel);
    expect(buttonStartsWith(on, 'NB').getAttribute('aria-pressed')).toBe('true');
    expect(buttonStartsWith(on, 'NR').getAttribute('aria-pressed')).toBe('true');
    Object.assign(mockDspProps, { nbActive: false, nrMode: 0 });
    const off = mountPanel(DspPanel);
    expect(button(off, 'NB').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'NR').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'NB').disabled).toBe(false);
  });

  it('omits aria-pressed and disables NB/NR over unread readings', () => {
    Object.assign(mockDspProps, {
      nbActive: false, nbKnown: false, hasNb: true,
      nrMode: 0, nrKnown: false, hasNr: true,
    });
    const target = mountPanel(DspPanel);
    for (const key of [button(target, 'NB'), button(target, 'NR')]) {
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });

  it('exposes NOTCH and A-NOTCH: "true" when selected, "false" otherwise', () => {
    mockDspProps.notchMode = 'manual';
    expect(button(mountPanel(DspPanel), 'NOTCH').getAttribute('aria-pressed')).toBe('true');
    mockDspProps.notchMode = 'auto';
    const auto = mountPanel(DspPanel);
    expect(button(auto, 'A-NOTCH').getAttribute('aria-pressed')).toBe('true');
    expect(button(auto, 'NOTCH').getAttribute('aria-pressed')).toBe('false');
    mockDspProps.notchMode = 'off';
    const off = mountPanel(DspPanel);
    expect(button(off, 'NOTCH').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'A-NOTCH').disabled).toBe(false);
  });

  it('omits aria-pressed and disables the notch keys over unread readings', () => {
    Object.assign(mockDspProps, {
      notchMode: 'off', manualNotchKnown: false, hasNotch: true,
      autoNotchKnown: false, hasAutoNotch: true,
    });
    const target = mountPanel(DspPanel);
    for (const label of ['NOTCH', 'A-NOTCH']) {
      const key = button(target, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });
});

// ── EssentialsPanel ──

describe('EssentialsPanel toggle keys (MOR-2979)', () => {
  it('exposes SPLIT: "true" when on, "false" when off', () => {
    const on = mountPanel(EssentialsPanel, essentialsProps({ vfoOps: { splitActive: true, splitKnown: true } }));
    expect(button(on, 'SPLIT').getAttribute('aria-pressed')).toBe('true');
    const off = mountPanel(EssentialsPanel, essentialsProps());
    const key = button(off, 'SPLIT');
    expect(key.getAttribute('aria-pressed')).toBe('false');
    expect(key.disabled).toBe(false);
  });

  it('omits aria-pressed and disables SPLIT over an unread split', () => {
    const target = mountPanel(
      EssentialsPanel,
      essentialsProps({ vfoOps: { splitActive: false, splitKnown: false } }),
    );
    const key = button(target, 'SPLIT');
    expect(key.hasAttribute('aria-pressed')).toBe(false);
    expect(key.disabled).toBe(true);
  });

  it('exposes NB, NR and NOTCH: "true" when on, "false" when off', () => {
    const on = mountPanel(EssentialsPanel, essentialsProps({
      dsp: {
        nbActive: true, nrMode: 1, notchMode: 'auto',
        nbKnown: true, nrKnown: true, manualNotchKnown: true, autoNotchKnown: true,
      },
    }));
    expect(button(on, 'NB').getAttribute('aria-pressed')).toBe('true');
    expect(button(on, 'NR').getAttribute('aria-pressed')).toBe('true');
    expect(button(on, 'NOTCH').getAttribute('aria-pressed')).toBe('true');
    const off = mountPanel(EssentialsPanel, essentialsProps());
    expect(button(off, 'NB').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'NR').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'NOTCH').getAttribute('aria-pressed')).toBe('false');
    expect(button(off, 'NB').disabled).toBe(false);
  });

  it('omits aria-pressed and disables NB/NR/NOTCH over unread readings', () => {
    const target = mountPanel(EssentialsPanel, essentialsProps({
      dsp: {
        nbActive: false, nrMode: 0, notchMode: 'off',
        nbKnown: false, nrKnown: false, manualNotchKnown: false, autoNotchKnown: true,
      },
    }));
    for (const label of ['NB', 'NR', 'NOTCH']) {
      const key = button(target, label);
      expect(key.hasAttribute('aria-pressed')).toBe(false);
      expect(key.disabled).toBe(true);
    }
  });
});
