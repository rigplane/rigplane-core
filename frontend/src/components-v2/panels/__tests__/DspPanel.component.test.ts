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

const runtimeState = vi.hoisted(() => ({
  state: null as {
    active: 'MAIN' | 'SUB';
    main: Record<string, unknown>;
    sub: Record<string, unknown>;
    observationSeq?: number;
  } | null,
  notify: () => {},
}));
const mockProjection = vi.hoisted(() => ({ notify: () => {} }));

vi.mock('$lib/runtime/frontend-runtime', async () => {
  const { createSubscriber } = await import('svelte/reactivity');
  let update = () => {};
  const subscribe = createSubscriber((notify) => { update = notify; return () => {}; });
  runtimeState.notify = () => update();
  return {
    runtime: {
      get state() { subscribe(); return runtimeState.state; },
      get caps() { return null; },
    },
  };
});

vi.mock('$lib/runtime/adapters/panel-adapters', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/adapters/panel-adapters')>();
  const { createSubscriber } = await import('svelte/reactivity');
  let update = () => {};
  const subscribe = createSubscriber((notify) => { update = notify; return () => {}; });
  mockProjection.notify = () => update();
  return {
    ...actual,
    deriveDspProps: () => {
      subscribe();
      return { ...mockProps };
    },
    getDspHandlers: () => mockHandlers,
  };
});

import DspPanel from '../DspPanel.svelte';
import {
  acknowledgeCommand,
  beginCommand,
  failCommand,
  resetCommandLifecycle,
} from '$lib/stores/commands.svelte';

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
  resetCommandLifecycle();
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
  runtimeState.state = {
    active: 'MAIN',
    main: { autoNotch: false, manualNotch: false },
    sub: {},
    observationSeq: 1,
  };
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  resetCommandLifecycle();
  runtimeState.state = null;
  vi.useRealTimers();
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

describe('DspPanel mobile notch dialog (MOR-1631)', () => {
  let lifecycleSerial = 0;

  function beginNotchCommand(
    strand: 'auto' | 'manual', on: boolean, timeoutMs = 5_000,
  ) {
    const name = strand === 'auto' ? 'set_auto_notch' : 'set_manual_notch';
    lifecycleSerial += 1;
    return beginCommand({
      id: `${name}-${lifecycleSerial}`,
      name,
      params: { on, receiver: 0 },
      originalEpoch: 1,
      timeoutMs,
    });
  }

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

  function modeButton(t: HTMLElement, mode: string): HTMLButtonElement {
    const modeValue = mode === 'MAN' ? 'manual' : mode.toLowerCase();
    const button = t.querySelector<HTMLButtonElement>(
      `[aria-label="Notch filter settings"] [data-notch-mode-choice="${modeValue}"] button`,
    );
    expect(button).toBeDefined();
    return button!;
  }

  it('uses the actual mobile tuning-strip bottom bound without a duplicated fixed height', () => {
    const originalWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 375 });
    const tuningStrip = document.createElement('nav');
    tuningStrip.className = 'm-tuning-strip';
    tuningStrip.getBoundingClientRect = () => ({ top: 610 } as DOMRect);
    document.body.appendChild(tuningStrip);

    try {
      const t = mountPanel({ notchMode: 'manual', notchFreq: 128 });
      openNotchModal(t);
      const dialog = t.querySelector<HTMLElement>('[aria-label="Notch filter settings"]');
      expect(dialog?.style.top).toBe('8px');
      expect(dialog?.style.bottom).toBe('calc(100dvh + 8px - 610px)');
      expect(dialog?.textContent).toContain('Notch Position');
      expect(dialog?.querySelector('[aria-label="Notch Position"]')).not.toBeNull();
    } finally {
      tuningStrip.remove();
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth });
    }
  });

  it.each([
    ['off', 'OFF'],
    ['auto', 'AUTO'],
    ['manual', 'MAN'],
  ] as const)('renders only confirmed %s mode as selected', (notchMode, selectedLabel) => {
    const t = mountPanel({ notchMode });
    openNotchModal(t);

    for (const mode of ['OFF', 'AUTO', 'MAN']) {
      expect(modeButton(t, mode).dataset.active).toBe(String(mode === selectedLabel));
    }
    expect(t.querySelector('[aria-label="Notch Position"]') !== null).toBe(notchMode === 'manual');
  });

  it('does not optimistically select a requested mode before the confirmed radio state changes', () => {
    const t = mountPanel({ notchMode: 'off' });
    openNotchModal(t);

    modeButton(t, 'MAN').click();
    flushSync();

    expect(mockHandlers.onNotchModeChange).toHaveBeenCalledExactlyOnceWith('manual');
    expect(modeButton(t, 'OFF').dataset.active).toBe('true');
    expect(modeButton(t, 'MAN').dataset.active).toBe('false');
    expect(t.querySelector('[aria-label="Notch Position"]')).toBeNull();
  });

  it.each([
    ['OFF', 'off'],
    ['AUTO', 'auto'],
    ['MAN', 'manual'],
  ] as const)('uses the dialog-scoped %s choice selector and preserves its exact command target', (label, targetMode) => {
    const t = mountPanel({ notchMode: 'off' });
    openNotchModal(t);

    modeButton(t, label).click();

    expect(mockHandlers.onNotchModeChange).toHaveBeenCalledExactlyOnceWith(targetMode);
  });

  it.each([
    ['AUTO', 'auto'],
    ['MAN', 'manual'],
  ] as const)('marks the delayed %s confirmation on its exact dialog choice without changing confirmed selection', (label, strand) => {
    const t = mountPanel({ notchMode: 'off' });
    openNotchModal(t);
    beginNotchCommand(strand, true);
    flushSync();

    const choice = t.querySelector<HTMLElement>(
      `[aria-label="Notch filter settings"] [data-notch-mode-choice="${label === 'MAN' ? 'manual' : label.toLowerCase()}"]`,
    );
    expect(choice?.getAttribute('aria-busy')).toBe('true');
    expect(modeButton(t, label).dataset.armed).toBe('true');
    expect(modeButton(t, 'OFF').dataset.active).toBe('true');
    expect(modeButton(t, label).dataset.active).toBe('false');
    expect(t.querySelector('[data-notch-mode-live]')?.textContent).toContain('Pending');
  });

  it('treats OFF as one pending choice only after both false command strands are armed', () => {
    const t = mountPanel({ notchMode: 'manual' });
    openNotchModal(t);

    beginNotchCommand('auto', false);
    flushSync();
    expect(t.querySelector('[data-notch-mode-choice="off"]')?.getAttribute('aria-busy')).toBe('false');
    expect(modeButton(t, 'OFF').dataset.armed).toBeUndefined();

    beginNotchCommand('manual', false);
    flushSync();
    expect(t.querySelector('[data-notch-mode-choice="off"]')?.getAttribute('aria-busy')).toBe('true');
    expect(modeButton(t, 'OFF').dataset.armed).toBe('true');
    expect(modeButton(t, 'MAN').dataset.active).toBe('true');
  });

  it('does not resurrect an older AUTO target after its newer same-key command fails and is retained', () => {
    const t = mountPanel({ notchMode: 'off' });
    openNotchModal(t);
    vi.useFakeTimers();
    vi.setSystemTime(1_000);

    beginNotchCommand('auto', true, 60_000);
    flushSync();
    expect(modeButton(t, 'AUTO').dataset.armed).toBe('true');

    vi.setSystemTime(1_001);
    const newer = beginNotchCommand('auto', false, 60_000);
    flushSync();
    expect(modeButton(t, 'AUTO').dataset.armed).toBeUndefined();
    expect(modeButton(t, 'OFF').dataset.armed).toBeUndefined();

    failCommand(newer.id, newer.originalEpoch, 1, 'expected component-test failure');
    flushSync();
    expect(modeButton(t, 'AUTO').dataset.armed).toBeUndefined();
    expect(t.querySelector('[data-notch-mode-live]')).toBeNull();

    vi.advanceTimersByTime(5_001);
    flushSync();
    expect(modeButton(t, 'AUTO').dataset.armed).toBeUndefined();
  });

  it('does not resurrect an older MAN target after its newer same-key command times out', () => {
    const t = mountPanel({ notchMode: 'off' });
    openNotchModal(t);
    vi.useFakeTimers();

    beginNotchCommand('manual', true, 60_000);
    flushSync();
    expect(modeButton(t, 'MAN').dataset.armed).toBe('true');

    beginNotchCommand('manual', false, 100);
    flushSync();
    expect(modeButton(t, 'MAN').dataset.armed).toBeUndefined();

    vi.advanceTimersByTime(101);
    flushSync();
    expect(modeButton(t, 'MAN').dataset.armed).toBeUndefined();
    expect(t.querySelector('[data-notch-mode-live]')).toBeNull();
  });

  it('moves pending feedback to the newest same-key target without reviving the superseded target', () => {
    const t = mountPanel({ notchMode: 'off' });
    openNotchModal(t);

    beginNotchCommand('auto', true);
    flushSync();
    expect(modeButton(t, 'AUTO').dataset.armed).toBe('true');

    beginNotchCommand('auto', false);
    flushSync();
    expect(modeButton(t, 'AUTO').dataset.armed).toBeUndefined();
    expect(modeButton(t, 'OFF').dataset.armed).toBeUndefined();

    beginNotchCommand('manual', false);
    flushSync();
    expect(modeButton(t, 'OFF').dataset.armed).toBe('true');
    expect(modeButton(t, 'AUTO').dataset.armed).toBeUndefined();
  });

  it('clears pending and selects an out-of-band confirmed AUTO observation', () => {
    const t = mountPanel({ notchMode: 'off' });
    openNotchModal(t);
    const command = beginNotchCommand('auto', true);
    flushSync();
    expect(modeButton(t, 'AUTO').dataset.armed).toBe('true');
    expect(modeButton(t, 'OFF').dataset.active).toBe('true');

    acknowledgeCommand(command.id, command.originalEpoch, 1);
    flushSync();
    expect(modeButton(t, 'AUTO').dataset.armed).toBe('true');

    runtimeState.state = {
      active: 'MAIN',
      main: { autoNotch: true, manualNotch: false },
      sub: {},
      observationSeq: 2,
    };
    mockProps.notchMode = 'auto';
    runtimeState.notify();
    mockProjection.notify();
    flushSync();

    expect(modeButton(t, 'AUTO').dataset.armed).toBeUndefined();
    expect(modeButton(t, 'AUTO').dataset.active).toBe('true');
    expect(modeButton(t, 'OFF').dataset.active).toBe('false');
    expect(t.querySelector('[data-notch-mode-live]')).toBeNull();
  });

  it('keeps an out-of-band confirmed mode selected while a superseding target is pending', () => {
    const t = mountPanel({ notchMode: 'manual' });
    openNotchModal(t);
    beginNotchCommand('auto', true);
    flushSync();

    expect(modeButton(t, 'MAN').dataset.active).toBe('true');
    expect(modeButton(t, 'AUTO').dataset.active).toBe('false');
    expect(modeButton(t, 'AUTO').dataset.armed).toBe('true');
    expect(t.querySelector('[aria-label="Notch Position"]')).not.toBeNull();
  });
});
