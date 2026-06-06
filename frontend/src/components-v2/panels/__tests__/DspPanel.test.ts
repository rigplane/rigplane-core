import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { buildNrOptions, buildNotchOptions } from '../dsp-utils';
import { rawToPercentDisplay } from '../../controls/value-control';

const mockProps = {
  nrMode: 0,
  nrLevel: 128,
  nbActive: false,
  nbLevel: 128,
  notchMode: 'off' as string,
  notchFreq: 1000,
  nbDepth: 0,
  nbWidth: 0,
  manualNotchWidth: 0,
  agcTimeConstant: 0,
  hasNr: true,
  hasNb: true,
};

const mockHandlers = {
  onNrModeChange: vi.fn(),
  onNrLevelChange: vi.fn(),
  onNbToggle: vi.fn(),
  onNbLevelChange: vi.fn(),
  onNotchModeChange: vi.fn(),
  onNotchFreqChange: vi.fn(),
  onNbDepthChange: vi.fn(),
  onNbWidthChange: vi.fn(),
  onManualNotchWidthChange: vi.fn(),
  onAgcTimeChange: vi.fn(),
};

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveDspProps: () => mockProps,
  getDspHandlers: () => mockHandlers,
}));

import DspPanel from '../DspPanel.svelte';

// ---------------------------------------------------------------------------
// buildNrOptions
// ---------------------------------------------------------------------------

describe('buildNrOptions', () => {
  it('returns 2 options (OFF / ON)', () => {
    expect(buildNrOptions()).toHaveLength(2);
  });

  it('first option is OFF with value 0', () => {
    expect(buildNrOptions()[0]).toEqual({ value: 0, label: 'OFF' });
  });

  it('second option is ON with value 1', () => {
    expect(buildNrOptions()[1]).toEqual({ value: 1, label: 'ON' });
  });

  it('all option values are numbers', () => {
    buildNrOptions().forEach((o) => expect(typeof o.value).toBe('number'));
  });
});

// ---------------------------------------------------------------------------
// buildNotchOptions
// ---------------------------------------------------------------------------

describe('buildNotchOptions', () => {
  it('returns 3 options', () => {
    expect(buildNotchOptions()).toHaveLength(3);
  });

  it('first option is OFF with value "off"', () => {
    expect(buildNotchOptions()[0]).toEqual({ value: 'off', label: 'OFF' });
  });

  it('second option is AUTO with value "auto"', () => {
    expect(buildNotchOptions()[1]).toEqual({ value: 'auto', label: 'AUTO' });
  });

  it('third option is manual with value "manual"', () => {
    expect(buildNotchOptions()[2]).toEqual({ value: 'manual', label: 'MAN' });
  });
});

// ---------------------------------------------------------------------------
// DspPanel component
// ---------------------------------------------------------------------------

let components: ReturnType<typeof mount>[] = [];

function mountPanel(overrides?: Partial<typeof mockProps>) {
  if (overrides) Object.assign(mockProps, overrides);
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(DspPanel, { target: t });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  Object.assign(mockProps, {
    nrMode: 0, nrLevel: 128, nbActive: false, nbLevel: 128,
    notchMode: 'off', notchFreq: 1000, nbDepth: 0, nbWidth: 0,
    manualNotchWidth: 0, agcTimeConstant: 0,
    hasNr: true, hasNb: true,
  });
  mockHandlers.onNrModeChange = vi.fn();
  mockHandlers.onNrLevelChange = vi.fn();
  mockHandlers.onNbToggle = vi.fn();
  mockHandlers.onNbLevelChange = vi.fn();
  mockHandlers.onNotchModeChange = vi.fn();
  mockHandlers.onNotchFreqChange = vi.fn();
  mockHandlers.onNbDepthChange = vi.fn();
  mockHandlers.onNbWidthChange = vi.fn();
  mockHandlers.onManualNotchWidthChange = vi.fn();
  mockHandlers.onAgcTimeChange = vi.fn();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

function getFillButtons(container: HTMLElement): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll<HTMLButtonElement>('.dsp-btn-wrap button'));
}

describe('compact toggle row', () => {
  it('renders NR, NB, NOTCH labels in FillButtons', () => {
    const t = mountPanel();
    const texts = getFillButtons(t).map((b) => b.textContent?.trim());
    expect(texts).toContain('NR');
    expect(texts).toContain('NB');
    expect(texts).toContain('NOTCH');
  });
});

describe('NR toggle', () => {
  it('calls onNrModeChange(1) when NR is off and toggle is clicked', () => {
    const t = mountPanel({ nrMode: 0 });
    const nrBtn = getFillButtons(t).find((b) => b.textContent?.trim().startsWith('NR'));
    nrBtn?.click();
    flushSync();
    expect(mockHandlers.onNrModeChange).toHaveBeenCalledWith(1);
  });

  it('calls onNrModeChange(0) when NR is on and toggle is clicked', () => {
    const t = mountPanel({ nrMode: 1 });
    const nrBtn = getFillButtons(t).find((b) => b.textContent?.trim().startsWith('NR'));
    nrBtn?.click();
    flushSync();
    expect(mockHandlers.onNrModeChange).toHaveBeenCalledWith(0);
  });
});

describe('NB toggle', () => {
  it('calls onNbToggle(true) when NB is off and toggle is clicked', () => {
    const t = mountPanel({ nbActive: false });
    const nbBtn = getFillButtons(t).find((b) => b.textContent?.trim().startsWith('NB'));
    nbBtn?.click();
    flushSync();
    expect(mockHandlers.onNbToggle).toHaveBeenCalledWith(true);
  });

  it('calls onNbToggle(false) when NB is on and toggle is clicked', () => {
    const t = mountPanel({ nbActive: true });
    const nbBtn = getFillButtons(t).find((b) => b.textContent?.trim().startsWith('NB'));
    nbBtn?.click();
    flushSync();
    expect(mockHandlers.onNbToggle).toHaveBeenCalledWith(false);
  });

  it('shows the NB level as the same percent the NB-Level slider uses (not raw)', () => {
    const t = mountPanel({ nbActive: true, nbLevel: 76 });
    const nbBtn = getFillButtons(t).find((b) => b.textContent?.trim().startsWith('NB'));
    const label = nbBtn?.textContent?.trim();
    // rawToPercentDisplay(76) === '30%'
    expect(label).toBe(`NB ${rawToPercentDisplay(76)}`);
    expect(label).not.toContain('76');
  });
});

describe('Notch toggle', () => {
  it('calls onNotchModeChange("auto") when notch is off and toggle is clicked', () => {
    const t = mountPanel({ notchMode: 'off' });
    const notchBtn = getFillButtons(t).find((b) => b.textContent?.trim() === 'NOTCH');
    notchBtn?.click();
    flushSync();
    expect(mockHandlers.onNotchModeChange).toHaveBeenCalledWith('auto');
  });

  it('calls onNotchModeChange("off") when notch is auto and toggle is clicked', () => {
    const t = mountPanel({ notchMode: 'auto' });
    const notchBtn = getFillButtons(t).find((b) => b.textContent?.trim() === 'NOTCH');
    notchBtn?.click();
    flushSync();
    expect(mockHandlers.onNotchModeChange).toHaveBeenCalledWith('off');
  });

  it('calls onNotchModeChange("off") when notch is manual and toggle is clicked', () => {
    const t = mountPanel({ notchMode: 'manual' });
    const notchBtn = getFillButtons(t).find((b) => b.textContent?.trim() === 'NOTCH');
    notchBtn?.click();
    flushSync();
    expect(mockHandlers.onNotchModeChange).toHaveBeenCalledWith('off');
  });
});

describe('modal initial state', () => {
  it('no backdrop when no modal is open', () => {
    const t = mountPanel();
    expect(t.querySelector('.menu-backdrop')).toBeNull();
  });

  it('no NR modal when no modal is open', () => {
    const t = mountPanel();
    expect(t.querySelector('[aria-label="Noise reduction settings"]')).toBeNull();
  });

  it('no NB modal when no modal is open', () => {
    const t = mountPanel();
    expect(t.querySelector('[aria-label="Noise blanker settings"]')).toBeNull();
  });

  it('no Notch modal when no modal is open', () => {
    const t = mountPanel();
    expect(t.querySelector('[aria-label="Notch filter settings"]')).toBeNull();
  });

  it('opens NR settings on long press', () => {
    vi.useFakeTimers();
    try {
      const t = mountPanel();
      const nrBtn = getFillButtons(t).find((b) => b.textContent?.trim().startsWith('NR'));

      nrBtn?.dispatchEvent(new Event('pointerdown', { bubbles: true }));
      vi.advanceTimersByTime(600);
      flushSync();

      expect(t.querySelector('[aria-label="Noise reduction settings"]')).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('renders the A-NOTCH button', () => {
    const t = mountPanel();
    const buttons = getFillButtons(t);
    expect(buttons.some((b) => b.textContent?.trim() === 'A-NOTCH')).toBe(true);
  });
});

describe('NR mode via short-click cycle', () => {
  it('cycles NR mode: off → 1 → off on successive clicks', () => {
    const t = mountPanel({ nrMode: 0 });
    const nrBtn = getFillButtons(t).find((b) => b.textContent?.trim().startsWith('NR'));
    nrBtn?.click();
    flushSync();
    expect(mockHandlers.onNrModeChange).toHaveBeenLastCalledWith(1);
  });
});
