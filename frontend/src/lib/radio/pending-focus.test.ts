/**
 * Cross-module test: verifies that `$lib/radio/pending-focus` is a single
 * shared instance — VFO handler (command-bus) sets the pending focus and
 * Mode handler (panel-commands) consumes the same value.
 *
 * This is the regression guard for #1044: before the extract, each module
 * held its own private `let` so writes from one side were invisible to the
 * other, breaking the VFO → Mode focus handoff introduced in #720.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => null),
  getRadioState: vi.fn(() => ({ active: 'MAIN' })),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => null),
  getControlRange: vi.fn(() => null),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: vi.fn(),
    startRx: vi.fn(),
    stopRx: vi.fn(),
    rxEnabled: false,
    setRxVolume: vi.fn(),
  },
}));

vi.mock('$lib/stores/audio.svelte', () => ({
  setMuted: vi.fn(),
  setVolume: vi.fn(),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  getTuningStep: vi.fn(() => 100),
  adjustTuningStep: vi.fn(),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: vi.fn(),
    startRx: vi.fn(),
    stopRx: vi.fn(),
    rxEnabled: false,
    setRxVolume: vi.fn(),
  },
}));

import { sendCommand } from '$lib/transport/ws-client';
import { getRadioState } from '$lib/stores/radio.svelte';
import { makeVfoHandlers } from '../../components-v2/wiring/command-bus';
import { makeModeHandlers } from '../runtime/commands/panel-commands';
import { recordModeFilter, _resetModeFilterMemory } from './mode-filter-memory';

const originalDocumentQuerySelector = document.querySelector.bind(document);

function stubModePanel(): void {
  const modePanel = document.createElement('div');
  modePanel.scrollIntoView = vi.fn();
  document.querySelector = vi.fn((selector: string) => {
    if (selector === '[data-mode-panel="true"]') {
      return modePanel;
    }
    return originalDocumentQuerySelector(selector);
  }) as typeof document.querySelector;
}

describe('shared pending-focus — cross-module handoff (#1044)', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
    _resetModeFilterMemory();
    stubModePanel();
    // Drain any lingering pending-focus from previous tests
    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);
    makeModeHandlers().onModeChange('__drain__');
    vi.mocked(sendCommand).mockClear();
  });

  afterEach(() => {
    document.querySelector = originalDocumentQuerySelector;
    vi.useRealTimers();
  });

  it('VFO handler (command-bus) sets focus that Mode handler (panel-commands) consumes', () => {
    const vfo = makeVfoHandlers();
    const mode = makeModeHandlers();

    // Simulate a SUB mode-badge click via the wiring layer
    vfo.onSubModeClick();

    // Store lags — still reports MAIN; pending-focus must win
    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);
    vi.mocked(sendCommand).mockClear();

    // Mode change is handled by panel-commands (the runtime layer)
    mode.onModeChange('USB');

    // receiver=1 means SUB — proves the shared state was read correctly
    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'USB', receiver: 1 });
  });

  it('VFO handler sets MAIN focus that Mode handler (panel-commands) consumes', () => {
    const vfo = makeVfoHandlers();
    const mode = makeModeHandlers();

    vfo.onMainModeClick();

    // Store lags — still reports SUB; pending-focus must win
    vi.mocked(getRadioState).mockReturnValue({ active: 'SUB' } as any);
    vi.mocked(sendCommand).mockClear();

    mode.onModeChange('CW');

    // receiver=0 means MAIN
    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'CW', receiver: 0 });
  });

  it('pending focus is consumed once — second call falls back to activeReceiverParam', () => {
    const vfo = makeVfoHandlers();
    const mode = makeModeHandlers();

    vfo.onSubModeClick();
    vi.mocked(sendCommand).mockClear();

    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);

    mode.onModeChange('CW');  // consumes SUB focus → receiver=1
    mode.onModeChange('USB'); // no pending focus → falls back to MAIN → receiver=0

    expect(sendCommand).toHaveBeenNthCalledWith(1, 'set_mode', { mode: 'CW', receiver: 1 });
    expect(sendCommand).toHaveBeenNthCalledWith(2, 'set_mode', { mode: 'USB', receiver: 0 });
  });

  it('recalls the remembered filter on the panel-commands path (MOR-495)', () => {
    // Desktop-v2 live path: ModePanel → panel-commands.makeModeHandlers.
    // A previously-observed USB(FIL1) must re-send filter 1 (2-byte 0x06)
    // rather than emit a mode-only frame that the radio defaults to FIL2.
    recordModeFilter('USB', 1);
    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);
    vi.mocked(sendCommand).mockClear();

    makeModeHandlers().onModeChange('USB');

    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'USB', filter: 1, receiver: 0 });
  });

  it('pending focus expires after 300ms and falls back to activeReceiverParam', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(1_700_000_000_000));

    const vfo = makeVfoHandlers();
    const mode = makeModeHandlers();

    vfo.onSubModeClick();
    vi.mocked(sendCommand).mockClear();

    // Advance past TTL
    vi.setSystemTime(new Date(1_700_000_000_500));

    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);

    mode.onModeChange('FM');

    // Expired → falls back to MAIN → receiver=0
    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'FM', receiver: 0 });
  });
});
