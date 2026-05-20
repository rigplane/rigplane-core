import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

// -- Child component stubs --
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const s = await import('./SpectrumPanelStub.svelte');
  return { default: s.default };
});
vi.mock('../panels/lcd/AmberLcdDisplay.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../display/FrequencyDisplay.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../meters/LinearSMeter.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/CollapsiblePanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/BottomSheet.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/BandSelector.svelte', () => ({ default: function S() { return {}; } }));
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
vi.mock('./KeyboardHandler.svelte', () => ({ default: function S() { return {}; } }));
// Note: ../panels/EssentialsPanel.svelte and ./mobile-chip-bar.svelte are intentionally
// NOT mocked — the chip-scroll IA contract (#839) is part of what these tests cover.
vi.mock('$lib/Button', () => ({ HardwareButton: function S() { return {}; } }));
vi.mock('lucide-svelte', () => {
  const S = function () { return {}; };
  return { Settings: S, ChevronLeft: S, ChevronRight: S, ChevronsLeft: S, ChevronsRight: S, Mic: S, MicOff: S, Sliders: S, Radio: S };
});
vi.mock('../controls/value-control', () => ({
  ValueControl: function S() { return {}; },
  rawToPercentDisplay: vi.fn((v: number) => `${Math.round(v / 255 * 100)}%`),
}));
vi.mock('./vfo-layout-tokens', () => ({
  resolveVfoLayoutProfile: vi.fn(() => 'baseline'),
  vfoLayoutStyleVars: vi.fn(() => ''),
}));

// -- Store mocks --
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null as { active?: 'MAIN' | 'SUB' } | null },
  getActiveReceiver: vi.fn(),
  getRadioState: vi.fn(),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => ({ connected: false })),
  getRadioPowerOn: vi.fn(() => null),
}));
vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({ volume: 50, muted: false, rxEnabled: false, txEnabled: false, micEnabled: false, bridgeRunning: false })),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: { start: vi.fn(), stop: vi.fn(), setVolume: vi.fn(), toggleMute: vi.fn() },
}));
vi.mock('$lib/utils/tx-permit', () => ({ getTxPermit: vi.fn(() => 'allowed') }));
vi.mock('$lib/stores/tuning.svelte', () => ({ applyModeDefault: vi.fn() }));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true), hasDualReceiver: vi.fn(() => false), hasAnyScope: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false), getCapabilities: vi.fn(() => ({ freqRanges: [], modes: [], filters: [] })),
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

// -- Wiring mocks --
const {
  onMainVfoClickSpy,
  onSubVfoClickSpy,
  mobileSystemHandlers,
  mobileTxAudioControl,
} = vi.hoisted(() => ({
  onMainVfoClickSpy: vi.fn(),
  onSubVfoClickSpy: vi.fn(),
  mobileSystemHandlers: {
    onPowerOff: vi.fn(),
    onPttOn: vi.fn(),
    onPttOff: vi.fn(),
  },
  mobileTxAudioControl: {
    startTx: vi.fn(),
    stopTx: vi.fn(),
  },
}));
vi.mock('../../wiring/command-bus', () => {
  const n = vi.fn();
  return {
    makeVfoHandlers: () => ({
      onMainFreqChange: n, onSubFreqChange: n, onVfoSwap: n, onVfoEqual: n, onReceiverSelect: n,
      onMainVfoClick: onMainVfoClickSpy, onSubVfoClick: onSubVfoClickSpy,
    }),
    makeMeterHandlers: () => ({ onMeterSourceChange: n }), makeKeyboardHandlers: () => ({ dispatch: n }),
    makeModeHandlers: () => ({ onModeChange: n, onDataModeChange: n }),
    makeFilterHandlers: () => ({ onFilterChange: n, onFilterWidthChange: n }),
    makeBandHandlers: () => ({ onBandSelect: n }), makePresetHandlers: () => ({ onPresetSelect: n }),
    makeRxAudioHandlers: () => ({ onAfLevelChange: n, onMonitorModeChange: n }),
    makeTxHandlers: () => ({ onPttChange: n, onPowerChange: n, onTuneStart: n, onAtuToggle: n, onRfPowerChange: n, onMicGainChange: n, onAtuTune: n, onVoxToggle: n, onCompToggle: n, onCompLevelChange: n, onMonToggle: n, onMonLevelChange: n, onDriveGainChange: n }),
    makeRfFrontEndHandlers: () => ({ onAttChange: n, onPreChange: n, onRfGainChange: n }),
    makeAgcHandlers: () => ({ onAgcModeChange: n }),
    makeRitXitHandlers: () => ({ onRitToggle: n, onRitClear: n, onXitToggle: n, onXitClear: n }),
    makeDspHandlers: () => ({ onNrToggle: n, onNbToggle: n, onNotchToggle: n }),
    makeCwPanelHandlers: () => ({ onSpeedChange: n }),
    makeAntennaHandlers: () => ({ onAntennaSelect: n }),
    makeScanHandlers: () => ({ onScanStart: n, onScanStop: n, onDfSpanChange: n, onResumeChange: n }),
    makeSystemHandlers: () => mobileSystemHandlers,
  };
});
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({
  getTxAudioControl: () => mobileTxAudioControl,
}));
vi.mock('../wiring/state-adapter', () => {
  const vfo = { freq: 14074000, mode: 'USB', filter: 'FIL1', sValue: 0, badges: {}, receiver: 'main', isActive: true };
  return {
    toVfoProps: vi.fn(() => vfo), toVfoOpsProps: vi.fn(() => ({ split: false, dualWatch: false })),
    toMeterProps: vi.fn(() => ({ signal: 0, rfPower: 0, swr: 0, alc: 0, txActive: false, meterSource: 'S' })),
    toModeProps: vi.fn(() => ({ currentMode: 'USB', modes: ['USB', 'LSB', 'CW', 'AM', 'FM'], dataMode: 0 })),
    toFilterProps: vi.fn(() => ({ currentFilter: 1, filterLabels: ['FIL1', 'FIL2', 'FIL3'] })),
    toBandSelectorProps: vi.fn(() => ({ currentFreq: 14074000 })),
    toRxAudioProps: vi.fn(() => ({ afLevel: 128, monitorMode: 'local' })),
    toTxProps: vi.fn(() => ({ rfPower: 128, txActive: false, atuActive: false, atuTuning: false })),
    toRfFrontEndProps: vi.fn(() => ({ att: 0, preamp: 0, rfGain: 100 })),
    toAgcProps: vi.fn(() => ({ agcMode: 3 })), toRitXitProps: vi.fn(() => ({ ritOn: false, ritOffset: 0, xitOn: false, xitOffset: 0 })),
    toDspProps: vi.fn(() => ({ nr: false, nb: false, notch: false })), toCwProps: vi.fn(() => ({ speed: 20 })),
    toAntennaProps: vi.fn(() => ({ selected: 1 })),
    toScanProps: vi.fn(() => ({ scanning: false, scanType: 'off', scanResumeMode: 'time' })),
  };
});

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import { hasTx, hasDualReceiver } from '$lib/stores/capabilities.svelte';
import { radio } from '$lib/stores/radio.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountMobile(): HTMLElement {
  const t = document.createElement('div');
  document.body.appendChild(t);
  components.push(mount(MobileRadioLayout, { target: t }));
  flushSync();
  return t;
}

beforeEach(() => {
  components = [];
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 844 });
  vi.mocked(hasTx).mockReturnValue(true);
  vi.mocked(hasDualReceiver).mockReturnValue(false);
  (radio as unknown as { current: { active?: 'MAIN' | 'SUB' } | null }).current = null;
  onMainVfoClickSpy.mockClear();
  onSubVfoClickSpy.mockClear();
  mobileSystemHandlers.onPowerOff.mockReset();
  mobileSystemHandlers.onPttOn.mockReset();
  mobileSystemHandlers.onPttOff.mockReset();
  mobileTxAudioControl.startTx.mockReset();
  mobileTxAudioControl.stopTx.mockReset();
  mobileTxAudioControl.startTx.mockResolvedValue(null);
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('MobileRadioLayout structure', () => {
  it('mounts without errors', () => {
    expect(mountMobile().children.length).toBeGreaterThan(0);
  });

  it('renders .m-layout root in portrait mode', () => {
    expect(mountMobile().querySelector('.m-layout')).not.toBeNull();
  });

  it('renders VFO header bar', () => {
    expect(mountMobile().querySelector('.m-vfo-bar')).not.toBeNull();
  });

  it('renders VFO frequency row', () => {
    expect(mountMobile().querySelector('.m-vfo-row')).not.toBeNull();
  });

  it('renders S-meter bar', () => {
    expect(mountMobile().querySelector('.m-smeter-bar')).not.toBeNull();
  });

  it('renders scrollable main content area', () => {
    expect(mountMobile().querySelector('.m-content')).not.toBeNull();
  });

  it('renders tuning strip', () => {
    expect(mountMobile().querySelector('.m-tuning-strip')).not.toBeNull();
  });

  it('renders section panels inside m-content', () => {
    expect(mountMobile().querySelectorAll('.m-content .m-section').length).toBeGreaterThan(0);
  });

  it('renders TX indicator', () => {
    expect(mountMobile().querySelector('.m-tx-indicator')).not.toBeNull();
  });

  it('renders settings button', () => {
    expect(mountMobile().querySelector('.m-settings-btn')).not.toBeNull();
  });
});

describe('MobileRadioLayout TX gating', () => {
  it('renders TX chip when hasTx is true (#839)', () => {
    vi.mocked(hasTx).mockReturnValue(true);
    const t = mountMobile();
    const chipBar = t.querySelector('.m-chip-bar');
    expect(chipBar).not.toBeNull();
    const labels = Array.from(chipBar?.querySelectorAll('.m-chip') ?? []).map(
      (b) => b.textContent?.trim(),
    );
    expect(labels).toContain('TX');
  });

  it('omits TX chip when hasTx is false (#839)', () => {
    vi.mocked(hasTx).mockReturnValue(false);
    const t = mountMobile();
    const chipBar = t.querySelector('.m-chip-bar');
    expect(chipBar).not.toBeNull();
    const labels = Array.from(chipBar?.querySelectorAll('.m-chip') ?? []).map(
      (b) => b.textContent?.trim(),
    );
    expect(labels).not.toContain('TX');
    // And the TX-only PTT is not mounted on cold-open (ESSENTIALS active by default).
    expect(t.querySelector('.m-ptt-btn')).toBeNull();
  });

  it('auto-resets active chip to ESSENTIALS when TX capability drops at runtime (#839)', () => {
    // Back the hasTx mock with a reactive $state so the component's $derived
    // txCapable re-evaluates when we flip capability mid-session.
    const txState = $state({ on: true });
    vi.mocked(hasTx).mockImplementation(() => txState.on);

    const t = mountMobile();
    // Select TX chip while TX-capable.
    const txChip = Array.from(t.querySelectorAll<HTMLButtonElement>('.m-chip')).find(
      (b) => b.textContent?.trim() === 'TX',
    );
    expect(txChip, 'TX chip should be present when hasTx=true').toBeDefined();
    txChip!.click();
    flushSync();
    expect(t.querySelector('#m-chip-panel-tx')).not.toBeNull();

    // Simulate capability refresh: TX disappears.
    txState.on = false;
    flushSync();

    // Guard $effect must reset activeChipId back to ESSENTIALS, so no panel goes blank.
    expect(t.querySelector('#m-chip-panel-tx')).toBeNull();
    expect(t.querySelector('#m-chip-panel-essentials')).not.toBeNull();
    // ESSENTIALS chip should now be the active one in the chip bar.
    const active = t.querySelector('.m-chip-bar .m-chip-active');
    expect(active?.textContent?.trim()).toBe('ESSENTIALS');
  });

  it('does not key PTT when released before TX audio startup finishes', async () => {
    vi.useFakeTimers();
    let resolveStart!: (value: string | null) => void;
    mobileTxAudioControl.startTx.mockReturnValueOnce(new Promise((resolve) => {
      resolveStart = resolve;
    }));

    const t = mountMobile();
    const ptt = t.querySelector<HTMLButtonElement>('.ptt-fab')!;
    ptt.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1 }));
    vi.advanceTimersByTime(60);
    ptt.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }));
    resolveStart(null);
    await Promise.resolve();
    await Promise.resolve();
    flushSync();

    expect(mobileSystemHandlers.onPttOn).not.toHaveBeenCalled();
    expect(mobileTxAudioControl.stopTx).toHaveBeenCalledOnce();

    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });
});

describe('MobileRadioLayout chip-scroll IA (#839)', () => {
  it('renders chip bar inside m-content with ESSENTIALS default-active', () => {
    const t = mountMobile();
    const bar = t.querySelector('.m-content .m-chip-bar');
    expect(bar).not.toBeNull();
    const active = bar?.querySelector('.m-chip-active');
    expect(active?.textContent?.trim()).toBe('ESSENTIALS');
    expect(t.querySelector('#m-chip-panel-essentials')).not.toBeNull();
  });

  it('renders exactly one active chip panel at a time', () => {
    const t = mountMobile();
    const panels = t.querySelectorAll('[id^="m-chip-panel-"]');
    expect(panels.length).toBe(1);
  });
});

describe('MobileRadioLayout unmount', () => {
  it('unmounts cleanly without errors', () => {
    const t = mountMobile();
    expect(t.querySelector('.m-layout')).not.toBeNull();
    expect(() => unmount(components.pop()!)).not.toThrow();
  });
});

describe('MobileRadioLayout receiver selector (#719)', () => {
  it('does not render selector on single-receiver radios', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(false);
    expect(mountMobile().querySelector('.m-receiver-selector')).toBeNull();
  });

  it('renders MAIN/SUB pills when dual-RX', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const t = mountMobile();
    const group = t.querySelector('.m-receiver-selector');
    expect(group).not.toBeNull();
    expect(group?.getAttribute('role')).toBe('group');
    expect(group?.getAttribute('aria-label')).toBe('Receiver selector');
    const pills = t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill');
    expect(pills.length).toBe(2);
    expect(pills[0].textContent?.trim()).toBe('MAIN');
    expect(pills[1].textContent?.trim()).toBe('SUB');
  });

  it('marks the active receiver with aria-pressed=true (MAIN default)', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const t = mountMobile();
    const [mainPill, subPill] = Array.from(
      t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill'),
    );
    expect(mainPill.getAttribute('aria-pressed')).toBe('true');
    expect(subPill.getAttribute('aria-pressed')).toBe('false');
    expect(mainPill.classList.contains('m-receiver-pill-active')).toBe(true);
  });

  it('reflects SUB as active when radioState.active === SUB', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    (radio as unknown as { current: { active?: 'MAIN' | 'SUB' } | null }).current = { active: 'SUB' };
    const t = mountMobile();
    const [mainPill, subPill] = Array.from(
      t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill'),
    );
    expect(mainPill.getAttribute('aria-pressed')).toBe('false');
    expect(subPill.getAttribute('aria-pressed')).toBe('true');
    expect(subPill.classList.contains('m-receiver-pill-active')).toBe(true);
  });

  it('pills are focusable buttons (keyboard a11y)', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const t = mountMobile();
    const pills = t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill');
    pills.forEach((p) => {
      expect(p.tagName).toBe('BUTTON');
      expect(p.getAttribute('type')).toBe('button');
    });
    pills[0].focus();
    expect(document.activeElement).toBe(pills[0]);
    pills[1].focus();
    expect(document.activeElement).toBe(pills[1]);
  });

  it('tapping MAIN pill dispatches onMainVfoClick', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const t = mountMobile();
    const [mainPill] = Array.from(
      t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill'),
    );
    mainPill.click();
    expect(onMainVfoClickSpy).toHaveBeenCalledTimes(1);
    expect(onSubVfoClickSpy).not.toHaveBeenCalled();
  });

  it('tapping SUB pill dispatches onSubVfoClick and scrolls VFO display', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const scrollSpy = vi.fn();
    // Patch Element.prototype.scrollIntoView for this test
    const origScroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrollSpy as unknown as typeof Element.prototype.scrollIntoView;
    try {
      const t = mountMobile();
      const pills = Array.from(t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill'));
      pills[1].click();
      expect(onSubVfoClickSpy).toHaveBeenCalledTimes(1);
      expect(onMainVfoClickSpy).not.toHaveBeenCalled();
      expect(scrollSpy).toHaveBeenCalled();
      const arg = scrollSpy.mock.calls[0]?.[0];
      expect(arg).toMatchObject({ behavior: 'smooth', block: 'center' });
    } finally {
      Element.prototype.scrollIntoView = origScroll;
    }
  });
});
