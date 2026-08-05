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

import { sendCommand } from '$lib/transport/ws-client';
import { getRadioState } from '$lib/stores/radio.svelte';
import { makeModeHandlers, makeVfoHandlers } from '../command-bus';

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

describe('focus → mode handoff race (#720)', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
    stubModePanel();
    // Drain any lingering pending-focus from previous tests.  Because the
    // module-scoped cache auto-clears on first read, one call is enough.
    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);
    makeModeHandlers().onModeChange('__drain__');
    vi.mocked(sendCommand).mockClear();
  });

  afterEach(() => {
    document.querySelector = originalDocumentQuerySelector;
    vi.useRealTimers();
  });

  it('routes mode change to SUB after rapid MAIN → SUB mode-badge clicks', () => {
    const vfo = makeVfoHandlers();
    const mode = makeModeHandlers();

    // Rapid clicks: MAIN then SUB (store `active` may still lag)
    vfo.onMainModeClick();
    vfo.onSubModeClick();

    // `activeReceiverParam()` would mis-report MAIN if the store lagged.
    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);

    mode.onModeChange('CW');

    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'CW', receiver: 1 });
  });

  it('routes mode change to MAIN after a single MAIN mode-badge click', () => {
    const vfo = makeVfoHandlers();
    const mode = makeModeHandlers();

    vfo.onMainModeClick();

    // Even if `activeReceiverParam()` reports SUB, pending-focus wins.
    vi.mocked(getRadioState).mockReturnValue({ active: 'SUB' } as any);

    mode.onModeChange('USB');

    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'USB', receiver: 0 });
  });

  it('falls back to activeReceiverParam when no prior focus click', () => {
    const mode = makeModeHandlers();

    vi.mocked(getRadioState).mockReturnValue({ active: 'SUB' } as any);

    mode.onModeChange('FM');

    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'FM', receiver: 1 });
  });

  it('expires the pending-focus cache after ~300ms', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(1_700_000_000_000));

    const vfo = makeVfoHandlers();
    const mode = makeModeHandlers();

    vfo.onSubModeClick();

    // Advance past the TTL window.
    vi.setSystemTime(new Date(1_700_000_000_500));

    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);

    mode.onModeChange('CW');

    // Expired → falls back to activeReceiverParam (MAIN = 0)
    expect(sendCommand).toHaveBeenCalledWith('set_mode', { mode: 'CW', receiver: 0 });
  });

  it('consumes the pending-focus entry on first use (not reused)', () => {
    const vfo = makeVfoHandlers();
    const mode = makeModeHandlers();

    vfo.onSubModeClick();
    // Ignore the set_vfo side-effect — we only assert on set_mode calls.
    vi.mocked(sendCommand).mockClear();

    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN' } as any);

    mode.onModeChange('CW');
    mode.onModeChange('USB');

    // First call uses pending SUB; second falls back to active MAIN.
    expect(sendCommand).toHaveBeenNthCalledWith(1, 'set_mode', { mode: 'CW', receiver: 1 });
    expect(sendCommand).toHaveBeenNthCalledWith(2, 'set_mode', { mode: 'USB', receiver: 0 });
  });
});
