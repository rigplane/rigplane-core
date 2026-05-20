import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { txStatusColor } from '../tx-utils';

// ---------------------------------------------------------------------------
// txStatusColor
// ---------------------------------------------------------------------------

describe('txStatusColor', () => {

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

it('returns danger red when tuning', () => {
    expect(txStatusColor(true, true)).toBe('var(--v2-tx-tuning)');
  });

  it('returns danger red when tuning even if active is false', () => {
    expect(txStatusColor(false, true)).toBe('var(--v2-tx-tuning)');
  });

  it('returns TX orange when active and not tuning', () => {
    expect(txStatusColor(true, false)).toBe('var(--v2-tx-active)');
  });

  it('returns muted color when inactive and not tuning', () => {
    expect(txStatusColor(false, false)).toBe('var(--v2-tx-idle)');
  });

  it('tuning takes priority over active', () => {
    // tuning=true always wins, regardless of active
    expect(txStatusColor(true, true)).toBe('var(--v2-tx-tuning)');
    expect(txStatusColor(false, true)).toBe('var(--v2-tx-tuning)');
  });
});

// ---------------------------------------------------------------------------
// TxPanel component
// ---------------------------------------------------------------------------

const mockProps = {
  txActive: false,
  rfPower: 128,
  micGain: 128,
  atuActive: false,
  atuTuning: false,
  voxActive: false,
  compActive: false,
  compLevel: 64,
  monActive: false,
  monLevel: 64,
  driveGain: 128,
  hasTx: true,
  hasTuner: true,
  hasMonitor: true,
};

const mockHandlers = {
  onRfPowerChange: vi.fn(),
  onMicGainChange: vi.fn(),
  onAtuToggle: vi.fn(),
  onAtuTune: vi.fn(),
  onVoxToggle: vi.fn(),
  onCompToggle: vi.fn(),
  onCompLevelChange: vi.fn(),
  onMonToggle: vi.fn(),
  onMonLevelChange: vi.fn(),
  onDriveGainChange: vi.fn(),
  onPttOn: vi.fn(),
  onPttOff: vi.fn(),
};

const mockTxAudioControl = vi.hoisted(() => ({
  startTx: vi.fn(),
  stopTx: vi.fn(),
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveTxProps: () => mockProps,
  getTxHandlers: () => mockHandlers,
}));

vi.mock('$lib/runtime/adapters/tx-adapter', () => ({
  getTxAudioControl: () => mockTxAudioControl,
}));

import TxPanel from '../TxPanel.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountPanel(overrides?: Partial<typeof mockProps>) {
  if (overrides) Object.assign(mockProps, overrides);
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(TxPanel, { target: t });
  flushSync();
  components.push(component);
  return t;
}

function openTxSettings(container: HTMLElement) {
  const btn = Array.from(container.querySelectorAll<HTMLButtonElement>('.v2-control-button'))
    .find((b) => b.textContent?.includes('LEVELS'));
  btn?.click();
  flushSync();
}

beforeEach(() => {
  components = [];
  Object.assign(mockProps, {
    txActive: false, rfPower: 128, micGain: 128, atuActive: false,
    atuTuning: false, voxActive: false, compActive: false, compLevel: 64,
    monActive: false, monLevel: 64, driveGain: 128,
    hasTx: true, hasTuner: true, hasMonitor: true,
  });
  mockHandlers.onRfPowerChange = vi.fn();
  mockHandlers.onMicGainChange = vi.fn();
  mockHandlers.onAtuToggle = vi.fn();
  mockHandlers.onAtuTune = vi.fn();
  mockHandlers.onVoxToggle = vi.fn();
  mockHandlers.onCompToggle = vi.fn();
  mockHandlers.onCompLevelChange = vi.fn();
  mockHandlers.onMonToggle = vi.fn();
  mockHandlers.onMonLevelChange = vi.fn();
  mockHandlers.onDriveGainChange = vi.fn();
  mockHandlers.onPttOn = vi.fn();
  mockHandlers.onPttOff = vi.fn();
  mockTxAudioControl.startTx.mockReset();
  mockTxAudioControl.stopTx.mockReset();
  mockTxAudioControl.startTx.mockResolvedValue(null);
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('panel structure', () => {
  it('renders TX IDLE badge when txActive is false', () => {
    const t = mountPanel();
    const strip = t.querySelector('.tx-strip');
    expect(strip?.textContent?.trim()).toBe('○ RX');
  });

  it('renders TX ACTIVE badge when txActive is true', () => {
    const t = mountPanel({ txActive: true });
    const strip = t.querySelector('.tx-strip');
    expect(strip?.textContent?.trim()).toBe('● TX');
  });

  it('renders Mic Gain slider', () => {
    const t = mountPanel();
    openTxSettings(t);
    const labels = Array.from(t.querySelectorAll('.vc-label'));
    expect(labels.some((el) => el.textContent === 'Mic Gain')).toBe(true);
  });

  it('renders ATU toggle', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim().startsWith('TUNE'))).toBe(true);
  });

  it('renders TUNE button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim().startsWith('TUNE'))).toBe(true);
  });

  it('renders VOX toggle', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim() === 'VOX')).toBe(true);
  });

  it('renders COMP toggle', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim().startsWith('COMP'))).toBe(true);
  });

  it('renders MON toggle', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim().startsWith('MON'))).toBe(true);
  });
});

describe('hasTx gating', () => {
  it('renders panel content when hasTx prop is true', () => {
    const t = mountPanel({ hasTx: true });
    expect(t.querySelector('.tx-panel')).not.toBeNull();
  });

  it('hides panel content when hasTx prop is false', () => {
    const t = mountPanel({ hasTx: false });
    expect(t.querySelector('.tx-panel')).toBeNull();
  });
});

describe('COMP slider visibility', () => {
  it('does not render Comp Level slider when compActive is false', () => {
    const t = mountPanel();
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).not.toContain('Comp Level');
  });

  it('renders Comp Level slider when compActive is true', () => {
    const t = mountPanel({ compActive: true });
    openTxSettings(t);
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).toContain('Comp Level');
  });
});

describe('MON slider visibility', () => {
  it('does not render Mon Level slider when monActive is false', () => {
    const t = mountPanel();
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).not.toContain('Mon Level');
  });

  it('renders Mon Level slider when monActive is true', () => {
    const t = mountPanel({ monActive: true });
    openTxSettings(t);
    const labels = Array.from(t.querySelectorAll('.vc-label')).map((el) => el.textContent);
    expect(labels).toContain('Mon Level');
  });
});

describe('tuning state', () => {
  it('adds tuning class to TUNE button when atuTuning is true', () => {
    const t = mountPanel({ atuTuning: true });
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim().startsWith('TUNING'))).toBe(true);
  });

  it('does not add tuning class when atuTuning is false', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('.v2-control-button'));
    expect(buttons.some((el) => el.textContent?.trim() === 'TUNE')).toBe(true);
  });
});

describe('callbacks', () => {

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('calls onAtuTune when TUNE button is clicked', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll<HTMLElement>('.v2-control-button'));
    const tuneBtn = buttons.find((el) => el.textContent?.trim().startsWith('TUNE'));
    tuneBtn?.click();
    expect(mockHandlers.onAtuTune).toHaveBeenCalledOnce();
  });

  it('calls onMicGainChange when Mic Gain slider changes', () => {
    const t = mountPanel();
    // Open the settings modal to reveal sliders
    const levelsBtn = Array.from(t.querySelectorAll<HTMLElement>('.v2-control-button'))
      .find((b) => b.textContent?.includes('LEVELS'));
    levelsBtn?.click();
    flushSync();
    // Find the Mic Gain slider (second [role="slider"], after RF Power)
    const sliders = t.querySelectorAll<HTMLElement>('[role="slider"]');
    const micSlider = sliders[1]; // RF Power is [0], Mic Gain is [1]
    if (micSlider) {
      micSlider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    }
    vi.advanceTimersByTime(60);

    expect(mockHandlers.onMicGainChange).toHaveBeenCalled();
  });

  it('starts TX audio before keying PTT', async () => {
    const order: string[] = [];
    mockTxAudioControl.startTx.mockImplementationOnce(async () => {
      order.push('audio');
      return null;
    });
    mockHandlers.onPttOn.mockImplementationOnce(() => {
      order.push('ptt');
    });

    const t = mountPanel();
    const ptt = t.querySelector<HTMLButtonElement>('.ptt-button')!;
    ptt.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
    flushSync();

    expect(mockTxAudioControl.startTx).toHaveBeenCalledOnce();
    expect(mockHandlers.onPttOn).toHaveBeenCalledOnce();
    expect(order).toEqual(['audio', 'ptt']);
  });

  it('does not key PTT when TX audio startup fails', async () => {
    mockTxAudioControl.startTx.mockResolvedValueOnce('TX MIC: microphone capture not supported');
    const t = mountPanel();
    const ptt = t.querySelector<HTMLButtonElement>('.ptt-button')!;
    ptt.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
    flushSync();

    expect(mockHandlers.onPttOn).not.toHaveBeenCalled();
    expect(t.textContent).toContain('TX MIC: microphone capture not supported');
  });

  it('does not key PTT when released before TX audio startup finishes', async () => {
    vi.useFakeTimers();
    let resolveStart!: (value: string | null) => void;
    mockTxAudioControl.startTx.mockReturnValueOnce(new Promise((resolve) => {
      resolveStart = resolve;
    }));

    const t = mountPanel();
    const ptt = t.querySelector<HTMLButtonElement>('.ptt-button')!;
    ptt.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    flushSync();
    expect(ptt.disabled).toBe(false);
    expect(ptt.getAttribute('aria-disabled')).toBe('true');
    ptt.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
    resolveStart(null);
    await Promise.resolve();
    await Promise.resolve();
    flushSync();

    expect(mockHandlers.onPttOn).not.toHaveBeenCalled();
    expect(mockTxAudioControl.stopTx).toHaveBeenCalledOnce();

    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });
});
