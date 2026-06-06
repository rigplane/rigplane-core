import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => null),
  getRadioState: vi.fn(() => null),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: vi.fn(),
    startRx: vi.fn(),
    stopRx: vi.fn(),
    setRxVolume: vi.fn(),
    rxEnabled: false,
  },
}));

import { sendCommand } from '$lib/transport/ws-client';
import {
  getActiveReceiver, getRadioState, patchActiveReceiver, patchRadioState, patchReceiver,
} from '$lib/stores/radio.svelte';
import { audioManager } from '$lib/audio/audio-manager';
import { toVfoOpsProps } from '../state-adapter';
import { makeBandHandlers, makeFilterHandlers, makeModeHandlers, makeRitXitHandlers, makeVfoHandlers } from '../command-bus';
import { recordModeFilter, _resetModeFilterMemory } from '$lib/radio/mode-filter-memory';

const originalDocumentQuerySelector = document.querySelector.bind(document);

describe('toVfoOpsProps', () => {
  // TX follows split, not the active receiver.  IC-7610 manual p. 3-2:
  // "you can transmit on only the Main band (except in Split
  // Frequency operation)."  Under split RX=MAIN, TX=SUB.  This is
  // independent of which receiver is currently "selected".
  it('reports txVfo=main when split is off regardless of active receiver', () => {
    expect(
      toVfoOpsProps(
        { active: 'MAIN', split: false, dualWatch: false, mainSubTracking: false } as any,
        null,
      ).txVfo,
    ).toBe('main');

    expect(
      toVfoOpsProps(
        { active: 'SUB', split: false, dualWatch: false, mainSubTracking: false } as any,
        null,
      ).txVfo,
    ).toBe('main');
  });

  it('reports txVfo=sub when split is on regardless of active receiver', () => {
    expect(
      toVfoOpsProps(
        { active: 'MAIN', split: true, dualWatch: false, mainSubTracking: false } as any,
        null,
      ).txVfo,
    ).toBe('sub');

    expect(
      toVfoOpsProps(
        { active: 'SUB', split: true, dualWatch: false, mainSubTracking: false } as any,
        null,
      ).txVfo,
    ).toBe('sub');
  });
});

describe('makeVfoHandlers', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
    vi.mocked(patchRadioState).mockClear();
  });

  afterEach(() => {
    document.querySelector = originalDocumentQuerySelector;
  });

  it('sends explicit split=false when toggling from active split state', () => {
    vi.mocked(getRadioState).mockReturnValue({ split: true } as any);

    makeVfoHandlers().onSplitToggle();

    expect(patchRadioState).toHaveBeenCalledWith({ split: false });
    expect(sendCommand).toHaveBeenCalledWith('set_split', { on: false });
  });

  it('sends explicit split=true when toggling from inactive split state', () => {
    vi.mocked(getRadioState).mockReturnValue({ split: false } as any);

    makeVfoHandlers().onSplitToggle();

    expect(patchRadioState).toHaveBeenCalledWith({ split: true });
    expect(sendCommand).toHaveBeenCalledWith('set_split', { on: true });
  });

  it('selects MAIN and scrolls the mode panel when the main mode badge is clicked', () => {
    const scrollIntoView = vi.fn();
    const modePanel = document.createElement('div');
    modePanel.scrollIntoView = scrollIntoView;
    document.querySelector = vi.fn((selector: string) => {
      if (selector === '[data-mode-panel="true"]') {
        return modePanel;
      }
      return originalDocumentQuerySelector(selector);
    }) as typeof document.querySelector;

    makeVfoHandlers().onMainModeClick();

    expect(patchRadioState).toHaveBeenCalledWith({ active: 'MAIN' });
    expect(sendCommand).toHaveBeenCalledWith('set_vfo', { vfo: 'MAIN' });
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it('selects SUB and scrolls the mode panel when the sub mode badge is clicked', () => {
    const scrollIntoView = vi.fn();
    const modePanel = document.createElement('div');
    modePanel.scrollIntoView = scrollIntoView;
    document.querySelector = vi.fn((selector: string) => {
      if (selector === '[data-mode-panel="true"]') {
        return modePanel;
      }
      return originalDocumentQuerySelector(selector);
    }) as typeof document.querySelector;

    makeVfoHandlers().onSubModeClick();

    expect(patchRadioState).toHaveBeenCalledWith({ active: 'SUB' });
    expect(sendCommand).toHaveBeenCalledWith('set_vfo', { vfo: 'SUB' });
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it('sends quick_dualwatch on onQuickDw (backend composes equalize + DW ON)', () => {
    makeVfoHandlers().onQuickDw();
    expect(sendCommand).toHaveBeenCalledWith('quick_dualwatch', {});
  });

  it('sends quick_split on onQuickSplit (backend composes equalize + SPLIT ON)', () => {
    makeVfoHandlers().onQuickSplit();
    expect(sendCommand).toHaveBeenCalledWith('quick_split', {});
  });

  it('sends set_main_sub_tracking with the on flag', () => {
    makeVfoHandlers().onTrackingToggle(true);
    expect(sendCommand).toHaveBeenCalledWith('set_main_sub_tracking', { on: true });

    makeVfoHandlers().onTrackingToggle(false);
    expect(sendCommand).toHaveBeenCalledWith('set_main_sub_tracking', { on: false });
  });

  // Optimistic updates + audio focus follow for live MAIN/SUB UX.
  describe('optimistic updates + audio focus', () => {
    beforeEach(() => {
      vi.mocked(patchReceiver).mockClear();
      vi.mocked(audioManager.setAudioConfig).mockClear();
    });

    it('onEqual optimistically copies MAIN freq/mode/filter to SUB before the poll', () => {
      vi.mocked(getRadioState).mockReturnValue({
        main: { freqHz: 14_074_000, mode: 'USB', filter: 2 },
        sub: { freqHz: 28_500_000, mode: 'AM', filter: 3 },
      } as any);

      makeVfoHandlers().onEqual();

      expect(patchReceiver).toHaveBeenCalledWith(
        1,
        { freqHz: 14_074_000, mode: 'USB', filter: 2 },
      );
      expect(sendCommand).toHaveBeenCalledWith('vfo_equalize', {});
    });

    it('onSwap optimistically exchanges freq/mode/filter between MAIN and SUB', () => {
      vi.mocked(getRadioState).mockReturnValue({
        main: { freqHz: 14_074_000, mode: 'USB', filter: 2 },
        sub: { freqHz: 28_500_000, mode: 'AM', filter: 3 },
      } as any);

      makeVfoHandlers().onSwap();

      // MAIN gets SUB's former values; SUB gets MAIN's former values.
      expect(patchReceiver).toHaveBeenCalledWith(
        0,
        { freqHz: 28_500_000, mode: 'AM', filter: 3 },
      );
      expect(patchReceiver).toHaveBeenCalledWith(
        1,
        { freqHz: 14_074_000, mode: 'USB', filter: 2 },
      );
      expect(sendCommand).toHaveBeenCalledWith('vfo_swap', {});
    });

    it('onEqual without state does nothing optimistic but still fires command', () => {
      vi.mocked(getRadioState).mockReturnValue(null);
      makeVfoHandlers().onEqual();
      expect(patchReceiver).not.toHaveBeenCalled();
      expect(sendCommand).toHaveBeenCalledWith('vfo_equalize', {});
    });

    it('onMainVfoClick couples audio focus to MAIN', () => {
      makeVfoHandlers().onMainVfoClick();
      expect(patchRadioState).toHaveBeenCalledWith({ active: 'MAIN' });
      expect(sendCommand).toHaveBeenCalledWith('set_vfo', { vfo: 'MAIN' });
      expect(audioManager.setAudioConfig).toHaveBeenCalledWith({ focus: 'main' });
    });

    it('onSubVfoClick couples audio focus to SUB', () => {
      makeVfoHandlers().onSubVfoClick();
      expect(patchRadioState).toHaveBeenCalledWith({ active: 'SUB' });
      expect(sendCommand).toHaveBeenCalledWith('set_vfo', { vfo: 'SUB' });
      expect(audioManager.setAudioConfig).toHaveBeenCalledWith({ focus: 'sub' });
    });
  });
});

describe('makeModeHandlers', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
    _resetModeFilterMemory();
  });

  it('emits set_mode for the active receiver', () => {
    vi.mocked(getRadioState).mockReturnValue({ active: 'SUB' } as any);

    makeModeHandlers().onModeChange('CW');

    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'CW', receiver: 1 });
  });

  it('recalls the remembered filter for a previously-observed mode (MOR-495)', () => {
    // The radio kept USB on FIL1; switching back to USB must re-send that
    // filter (2-byte 0x06) instead of letting the radio apply its USB default.
    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);
    recordModeFilter('USB', 1);

    makeModeHandlers().onModeChange('USB');

    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'USB', filter: 1, receiver: 0 });
  });

  it('emits mode-only set_mode for an unseen mode (MOR-495)', () => {
    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);

    makeModeHandlers().onModeChange('AM');

    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'AM', receiver: 0 });
  });

  it('emits numeric set_data_mode values for the active receiver', () => {
    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);

    makeModeHandlers().onDataModeChange(3);

    expect(sendCommand).toHaveBeenCalledWith('set_data_mode', { mode: 3, receiver: 0 });
  });
});

describe('makeBandHandlers', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
  });

  it('emits set_band when bsrCode is provided', () => {
    makeBandHandlers().onBandSelect('20m', 14_225_000, 5);

    expect(sendCommand).toHaveBeenCalledWith('set_band', { band: 5 });
  });

  it('falls back to set_freq when bsrCode is missing (e.g. 60m band)', () => {
    makeBandHandlers().onBandSelect('60m', 5_357_000);

    expect(sendCommand).toHaveBeenCalledWith('set_freq', { freq: 5_357_000, receiver: 0 });
  });
});

describe('makeRitXitHandlers', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
    vi.mocked(patchRadioState).mockClear();
  });

  it('emits set_rit_frequency for RIT offset changes', () => {
    makeRitXitHandlers().onRitOffsetChange(350);

    expect(patchRadioState).toHaveBeenCalledWith({ ritFreq: 350 });
    expect(sendCommand).toHaveBeenCalledWith('set_rit_frequency', { freq: 350 });
  });

  it('emits set_rit_frequency for XIT offset changes', () => {
    makeRitXitHandlers().onXitOffsetChange(-450);

    expect(patchRadioState).toHaveBeenCalledWith({ ritFreq: -450 });
    expect(sendCommand).toHaveBeenCalledWith('set_rit_frequency', { freq: -450 });
  });
});

describe('makeFilterHandlers', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
    vi.mocked(patchActiveReceiver).mockClear();
  });

  it('emits set_filter_shape for the active receiver and patches optimistic state', () => {
    vi.mocked(getRadioState).mockReturnValue({ active: 'SUB' } as any);

    makeFilterHandlers().onFilterShapeChange?.(1);

    expect(patchActiveReceiver).toHaveBeenCalledWith({ filterShape: 1 }, true);
    expect(sendCommand).toHaveBeenCalledWith('set_filter_shape', { shape: 1, receiver: 1 });
  });

  it('restores the active filter width after resetting defaults', () => {
    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);
    vi.mocked(getActiveReceiver).mockReturnValue({ filter: 2 } as any);

    makeFilterHandlers().onFilterDefaults?.([3000, 2400, 1800]);

    expect(sendCommand).toHaveBeenCalledWith('set_filter', { filter: 2, receiver: 0 });
    expect(patchActiveReceiver).toHaveBeenCalledWith({ filterWidth: 2400 }, true);
  });
});