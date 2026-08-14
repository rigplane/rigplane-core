import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const mockProps = {
  nrMode: 0,
  nrLevel: 5,
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
  hasNbDepth: true,
  hasNbWidth: true,
  nbLevelMax: 255,
  nbLevelPercent: true,
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

// MOR-1536: DspPanel now also reads the notch armed signal — default
// unarmed here, this file's tests are not about that behavior (covered by
// `mor1536-armed-adoption.test.ts`).
const unarmed = { armed: false, value: null };

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveDspProps: () => mockProps,
  getDspHandlers: () => mockHandlers,
  getAutoNotchArmed: () => unarmed,
  getManualNotchArmed: () => unarmed,
}));

import DspPanel from '../DspPanel.svelte';

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
    nrMode: 0, nrLevel: 5, nbActive: false, nbLevel: 128,
    notchMode: 'off', notchFreq: 1000, nbDepth: 0, nbWidth: 0,
    manualNotchWidth: 0, agcTimeConstant: 0,
    hasNr: true, hasNb: true,
    hasNbDepth: true, hasNbWidth: true, nbLevelMax: 255, nbLevelPercent: true,
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

describe('DspPanel component rendering', () => {
  it('mounts without errors', () => {
    const t = mountPanel();
    expect(t.querySelector('.dsp-panel')).not.toBeNull();
  });

  it('renders NB button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim().startsWith('NB'))).toBe(true);
  });

  it('renders NR button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim().startsWith('NR'))).toBe(true);
  });

  it('renders NOTCH button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim() === 'NOTCH')).toBe(true);
  });

  it('renders A-NOTCH button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim() === 'A-NOTCH')).toBe(true);
  });

  it('renders AGC-T button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim().startsWith('AGC-T'))).toBe(true);
  });

  it('unmounts cleanly', () => {
    const t = mountPanel();
    const comp = components.pop()!;
    unmount(comp);
    expect(t.innerHTML).toBe('');
  });
});

describe('DspPanel NB modal depth/width gating (MOR-502)', () => {
  function openNbModal(t: HTMLElement): void {
    vi.useFakeTimers();
    try {
      const nbBtn = Array.from(t.querySelectorAll<HTMLButtonElement>('.dsp-btn-wrap button')).find(
        (b) => b.textContent?.trim().startsWith('NB'),
      );
      nbBtn?.dispatchEvent(new Event('pointerdown', { bubbles: true }));
      vi.advanceTimersByTime(600);
      flushSync();
    } finally {
      vi.useRealTimers();
    }
  }

  it('renders NB Depth and NB Width in the modal when both capabilities are present', () => {
    const t = mountPanel({ nbActive: true, hasNbDepth: true, hasNbWidth: true });
    openNbModal(t);
    const modal = t.querySelector('[aria-label="Noise blanker settings"]');
    expect(modal?.textContent).toContain('NB Depth');
    expect(modal?.textContent).toContain('NB Width');
  });

  it('omits NB Depth and NB Width in the modal when both capabilities are absent', () => {
    const t = mountPanel({ nbActive: true, hasNbDepth: false, hasNbWidth: false });
    openNbModal(t);
    const modal = t.querySelector('[aria-label="Noise blanker settings"]');
    expect(modal?.textContent).not.toContain('NB Depth');
    expect(modal?.textContent).not.toContain('NB Width');
    expect(modal?.textContent).toContain('NB Level');
  });
});

describe('DspPanel manual-notch position (MOR-1633)', () => {
  function openNotchModal(t: HTMLElement): void {
    vi.useFakeTimers();
    try {
      const notchBtn = Array.from(t.querySelectorAll<HTMLButtonElement>('.dsp-btn-wrap button')).find(
        (b) => b.textContent?.trim() === 'NOTCH',
      );
      notchBtn?.dispatchEvent(new Event('pointerdown', { bubbles: true }));
      vi.advanceTimersByTime(600);
      flushSync();
    } finally {
      vi.useRealTimers();
    }
  }

  function manualNotchPosition(t: HTMLElement): HTMLElement {
    const slider = t.querySelector<HTMLElement>('[aria-label="Notch Position"]');
    expect(slider).not.toBeNull();
    return slider!;
  }

  it.each([0, 128, 255])('renders raw manual-notch position %i without Hz conversion', (notchFreq) => {
    const t = mountPanel({ notchMode: 'manual', notchFreq });
    openNotchModal(t);
    const slider = manualNotchPosition(t);

    expect(slider.getAttribute('aria-valuemin')).toBe('0');
    expect(slider.getAttribute('aria-valuemax')).toBe('255');
    expect(slider.getAttribute('aria-valuenow')).toBe(String(notchFreq));
    const control = slider.closest('.vc-hbar');
    expect(control?.querySelector('.vc-label')?.textContent).toBe('Notch Position');
    expect(control?.querySelector('.vc-value')?.textContent).toBe(String(notchFreq));
    expect(t.textContent).not.toContain('Hz');
  });

  it('emits raw manual-notch boundaries and midpoint unchanged', () => {
    const t = mountPanel({ notchMode: 'manual', notchFreq: 127 });
    openNotchModal(t);
    const slider = manualNotchPosition(t);

    vi.useFakeTimers();
    try {
      slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      vi.advanceTimersByTime(50);
      slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
      vi.advanceTimersByTime(50);
      slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
      vi.advanceTimersByTime(50);
    } finally {
      vi.useRealTimers();
    }

    expect(mockHandlers.onNotchFreqChange).toHaveBeenNthCalledWith(1, 128);
    expect(mockHandlers.onNotchFreqChange).toHaveBeenNthCalledWith(2, 0);
    expect(mockHandlers.onNotchFreqChange).toHaveBeenNthCalledWith(3, 255);
  });
});
