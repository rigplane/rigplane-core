import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import VfoOps from '../VfoOps.svelte';
import { vfoSwapLabel, vfoCopyLabel, vfoEqualLabel, vfoTxLabel } from '../vfo-ops-utils';

// ---------------------------------------------------------------------------
// Pure utility functions
// ---------------------------------------------------------------------------

describe('vfoSwapLabel', () => {
  it('returns A↔B for ab scheme', () => {
    expect(vfoSwapLabel('ab')).toBe('A↔B');
  });

  it('returns M↔S for main_sub scheme', () => {
    expect(vfoSwapLabel('main_sub')).toBe('M↔S');
  });

  it('defaults to A↔B for unknown scheme', () => {
    expect(vfoSwapLabel('unknown')).toBe('A↔B');
  });
});

describe('vfoCopyLabel', () => {
  it('returns A→B for ab scheme', () => {
    expect(vfoCopyLabel('ab')).toBe('A→B');
  });

  it('returns M→S for main_sub scheme', () => {
    expect(vfoCopyLabel('main_sub')).toBe('M→S');
  });
});

describe('vfoEqualLabel', () => {
  // Retained for backward-compat of the utility; no longer rendered
  // in VfoOps (the M=S duplicate button was removed in epic #774).
  it('returns A=B for ab scheme', () => {
    expect(vfoEqualLabel('ab')).toBe('A=B');
  });

  it('returns M=S for main_sub scheme', () => {
    expect(vfoEqualLabel('main_sub')).toBe('M=S');
  });
});

describe('vfoTxLabel', () => {
  it('returns TX→A for main slot in ab scheme', () => {
    expect(vfoTxLabel('ab', 'main')).toBe('TX→A');
  });

  it('returns TX→B for sub slot in ab scheme', () => {
    expect(vfoTxLabel('ab', 'sub')).toBe('TX→B');
  });

  it('returns TX→M for main slot in main_sub scheme', () => {
    expect(vfoTxLabel('main_sub', 'main')).toBe('TX→M');
  });

  it('returns TX→S for sub slot in main_sub scheme', () => {
    expect(vfoTxLabel('main_sub', 'sub')).toBe('TX→S');
  });
});

// ---------------------------------------------------------------------------
// VfoOps component
// ---------------------------------------------------------------------------

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasDualReceiver: vi.fn(() => false),
  getVfoScheme: vi.fn(() => 'ab'),
  vfoLabel: vi.fn((slot: string) => (slot === 'A' ? 'VFO A' : 'VFO B')),
}));

import { hasDualReceiver, getVfoScheme } from '$lib/stores/capabilities.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountComponent(props: ComponentProps<typeof VfoOps>) {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(VfoOps, { target: t, props });
  flushSync();
  components.push(component);
  return t;
}

const baseProps: ComponentProps<typeof VfoOps> = {
  splitActive: false,
  dualWatchActive: false,
  txVfo: 'main',
  onSwap: vi.fn(),
  onEqual: vi.fn(),
  onSplitToggle: vi.fn(),
  onQuickSplit: vi.fn(),
  onDualWatchToggle: vi.fn(),
  onQuickDw: vi.fn(),
};

beforeEach(() => {
  components = [];
  vi.mocked(hasDualReceiver).mockReturnValue(false);
  vi.mocked(getVfoScheme).mockReturnValue('ab');
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

function getButtonLabels(t: HTMLElement): string[] {
  return Array.from(t.querySelectorAll('.bridge-button')).map((el) => el.textContent?.trim() ?? '');
}

describe('always-visible buttons (ab scheme)', () => {
  it('renders A↔B swap button', () => {
    const t = mountComponent(baseProps);
    expect(getButtonLabels(t)).toContain('A↔B');
  });

  it('renders A→B copy button', () => {
    const t = mountComponent(baseProps);
    expect(getButtonLabels(t)).toContain('A→B');
  });

  it('renders SPLIT button', () => {
    const t = mountComponent(baseProps);
    expect(getButtonLabels(t)).toContain('SPLIT');
  });

  it('does NOT render the removed A=B duplicate', () => {
    const t = mountComponent(baseProps);
    expect(getButtonLabels(t)).not.toContain('A=B');
  });
});

describe('always-visible buttons (main_sub scheme)', () => {
  beforeEach(() => {
    vi.mocked(getVfoScheme).mockReturnValue('main_sub');
  });

  it('renders M↔S swap button', () => {
    const t = mountComponent(baseProps);
    expect(getButtonLabels(t)).toContain('M↔S');
  });

  it('renders M→S copy button', () => {
    const t = mountComponent(baseProps);
    expect(getButtonLabels(t)).toContain('M→S');
  });

  it('does NOT render the removed M=S duplicate', () => {
    const t = mountComponent(baseProps);
    expect(getButtonLabels(t)).not.toContain('M=S');
  });
});

describe('SPLIT button state', () => {
  it('SPLIT button is inactive when splitActive is false', () => {
    const t = mountComponent(baseProps);
    const split = Array.from(t.querySelectorAll('.bridge-button')).find(
      (el) => el.textContent?.trim() === 'SPLIT',
    );
    expect(split?.getAttribute('data-active')).toBe('false');
  });

  it('SPLIT button is active when splitActive is true', () => {
    const t = mountComponent({ ...baseProps, splitActive: true });
    const split = Array.from(t.querySelectorAll('.bridge-button')).find(
      (el) => el.textContent?.trim() === 'SPLIT',
    );
    expect(split?.getAttribute('data-active')).toBe('true');
  });
});

describe('TX indicator (dual receiver, read-only)', () => {
  beforeEach(() => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
  });

  it('renders the TX indicator with TX→A label when txVfo=main (ab scheme)', () => {
    const t = mountComponent(baseProps);
    const indicator = t.querySelector('[data-testid="tx-indicator"]');
    expect(indicator?.textContent?.trim()).toBe('TX→A');
    expect(indicator?.getAttribute('data-tx')).toBe('main');
  });

  it('renders the TX indicator with TX→B when txVfo=sub (ab scheme)', () => {
    const t = mountComponent({ ...baseProps, txVfo: 'sub' });
    const indicator = t.querySelector('[data-testid="tx-indicator"]');
    expect(indicator?.textContent?.trim()).toBe('TX→B');
    expect(indicator?.getAttribute('data-tx')).toBe('sub');
  });

  it('renders TX→M / TX→S labels under main_sub scheme', () => {
    vi.mocked(getVfoScheme).mockReturnValue('main_sub');
    let t = mountComponent(baseProps);
    expect(t.querySelector('[data-testid="tx-indicator"]')?.textContent?.trim()).toBe('TX→M');
    t = mountComponent({ ...baseProps, txVfo: 'sub' });
    expect(t.querySelector('[data-testid="tx-indicator"]')?.textContent?.trim()).toBe('TX→S');
  });

  it('TX indicator is NOT rendered when hasDualReceiver is false', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(false);
    const t = mountComponent(baseProps);
    expect(t.querySelector('[data-testid="tx-indicator"]')).toBeNull();
  });

  it('does NOT render removed TX→A / TX→B buttons (only the indicator)', () => {
    const t = mountComponent(baseProps);
    const buttons = Array.from(t.querySelectorAll('button.bridge-button'));
    const txButton = buttons.find(
      (el) => el.textContent?.trim() === 'TX→A' || el.textContent?.trim() === 'TX→B',
    );
    expect(txButton).toBeUndefined();
  });
});

describe('single-click callbacks', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('calls onSwap when swap button is clicked', () => {
    const onSwap = vi.fn();
    const t = mountComponent({ ...baseProps, onSwap });
    const btn = Array.from(t.querySelectorAll('.bridge-button')).find(
      (el) => el.textContent?.trim() === 'A↔B',
    ) as HTMLElement | undefined;
    btn?.click();
    expect(onSwap).toHaveBeenCalledOnce();
  });

  it('calls onEqual when M→S/A→B copy button is clicked', () => {
    const onEqual = vi.fn();
    const t = mountComponent({ ...baseProps, onEqual });
    const btn = Array.from(t.querySelectorAll('.bridge-button')).find(
      (el) => el.textContent?.trim() === 'A→B',
    ) as HTMLElement | undefined;
    btn?.click();
    expect(onEqual).toHaveBeenCalledOnce();
  });

  it('SPLIT single-click fires onSplitToggle after the double-click window closes', () => {
    const onSplitToggle = vi.fn();
    const onQuickSplit = vi.fn();
    const t = mountComponent({ ...baseProps, onSplitToggle, onQuickSplit });
    const btn = Array.from(t.querySelectorAll('.bridge-button')).find(
      (el) => el.textContent?.trim() === 'SPLIT',
    ) as HTMLElement | undefined;
    btn?.click();
    expect(onSplitToggle).not.toHaveBeenCalled();
    vi.advanceTimersByTime(300);
    expect(onSplitToggle).toHaveBeenCalledOnce();
    expect(onQuickSplit).not.toHaveBeenCalled();
  });

  it('DW single-click fires onDualWatchToggle after the double-click window closes', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const onDualWatchToggle = vi.fn();
    const onQuickDw = vi.fn();
    const t = mountComponent({ ...baseProps, onDualWatchToggle, onQuickDw });
    const btn = Array.from(t.querySelectorAll('.bridge-button')).find(
      (el) => el.textContent?.trim() === 'DW',
    ) as HTMLElement | undefined;
    btn?.click();
    expect(onDualWatchToggle).not.toHaveBeenCalled();
    vi.advanceTimersByTime(300);
    expect(onDualWatchToggle).toHaveBeenCalledOnce();
    expect(onQuickDw).not.toHaveBeenCalled();
  });
});

describe('double-click callbacks', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('SPLIT double-click fires onQuickSplit (not onSplitToggle)', () => {
    const onSplitToggle = vi.fn();
    const onQuickSplit = vi.fn();
    const t = mountComponent({ ...baseProps, onSplitToggle, onQuickSplit });
    const btn = Array.from(t.querySelectorAll('.bridge-button')).find(
      (el) => el.textContent?.trim() === 'SPLIT',
    ) as HTMLElement | undefined;
    btn?.click();
    vi.advanceTimersByTime(100);
    btn?.click();
    expect(onQuickSplit).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(500);
    expect(onSplitToggle).not.toHaveBeenCalled();
  });

  it('DW double-click fires onQuickDw (not onDualWatchToggle)', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const onDualWatchToggle = vi.fn();
    const onQuickDw = vi.fn();
    const t = mountComponent({ ...baseProps, onDualWatchToggle, onQuickDw });
    const btn = Array.from(t.querySelectorAll('.bridge-button')).find(
      (el) => el.textContent?.trim() === 'DW',
    ) as HTMLElement | undefined;
    btn?.click();
    vi.advanceTimersByTime(100);
    btn?.click();
    expect(onQuickDw).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(500);
    expect(onDualWatchToggle).not.toHaveBeenCalled();
  });
});
