import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ControlFeedback } from '$lib/runtime/adapters/panel-adapters';
import type { ControlDisplayDomain } from '$lib/radio/filter-controls';

const mockProps = {
  cwPitch: 600,
  keySpeed: 12,
  breakIn: 0,
  breakInDelay: 0,
  apfMode: 0,
  twinPeak: false,
  currentMode: 'CW',
  apfDisabled: false,
  tpfDisabled: false,
  hasCw: true,
  hasBreakIn: true,
  hasApf: true,
  hasTwinPeak: true,
  autoTuneAvailable: false,
  cwPitchDomain: null as ControlDisplayDomain | null,
  keySpeedDomain: null as ControlDisplayDomain | null,
};

const mockHandlers = {
  onCwPitchChange: vi.fn(),
  onKeySpeedChange: vi.fn(),
  onBreakInToggle: vi.fn(),
  onBreakInModeChange: vi.fn(),
  onBreakInDelayChange: vi.fn(),
  onApfChange: vi.fn(),
  onTwinPeakToggle: vi.fn(),
  onAutoTune: vi.fn(),
};

const mockFeedback = {
  confirmed: 64, target: null, requestedTarget: null, phase: 'idle', busy: false,
  availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
  sessionEpoch: 1, scope: { control: 'break-in-delay', receiver: 0 },
  repeatPolicy: 'latest-target-wins',
} as ControlFeedback<number>;
const mockPitchFeedback = {
  ...mockFeedback, confirmed: 600, scope: { control: 'cw-pitch', receiver: 0 },
} as ControlFeedback<number>;
const mockSpeedFeedback = {
  ...mockFeedback, confirmed: 12, scope: { control: 'keyer-speed', receiver: 0 },
} as ControlFeedback<number>;

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveCwProps: () => mockProps,
  getCwHandlers: () => mockHandlers,
  getBreakInDelayControlFeedback: () => mockFeedback,
  getCwPitchControlFeedback: () => mockPitchFeedback,
  getKeySpeedControlFeedback: () => mockSpeedFeedback,
}));

import CwPanel from '../CwPanel.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountPanel(overrides?: Partial<typeof mockProps>) {
  if (overrides) Object.assign(mockProps, overrides);
  const syncFeedback = (feedback: ControlFeedback<number>, value: number) => Object.assign(feedback, {
    confirmed: Number.isSafeInteger(value) ? value : null,
    availability: Number.isSafeInteger(value) ? 'available' : 'unavailable',
    phase: Number.isSafeInteger(value) ? 'idle' : 'unavailable',
  });
  syncFeedback(mockPitchFeedback, mockProps.cwPitch);
  syncFeedback(mockSpeedFeedback, mockProps.keySpeed);
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(CwPanel, { target: t });
  flushSync();
  components.push(component);
  return t;
}

  beforeEach(() => {
    components = [];
    Object.assign(mockProps, {
      cwPitch: 600, keySpeed: 12, breakIn: 0, breakInDelay: 0,
      apfMode: 0, twinPeak: false, currentMode: 'CW',
      apfDisabled: false, tpfDisabled: false,
      hasCw: true, hasBreakIn: true, hasApf: true, hasTwinPeak: true,
      autoTuneAvailable: false, cwPitchDomain: null, keySpeedDomain: null,
    });
  Object.values(mockHandlers).forEach((fn) => fn.mockClear());
  Object.assign(mockFeedback, {
    confirmed: 64, target: null, requestedTarget: null, phase: 'idle', busy: false,
    availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
  });
  Object.assign(mockPitchFeedback, {
    confirmed: 600, target: null, requestedTarget: null, phase: 'idle', busy: false,
    availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
    sessionEpoch: 1, scope: { control: 'cw-pitch', receiver: 0 },
  });
  Object.assign(mockSpeedFeedback, {
    confirmed: 12, target: null, requestedTarget: null, phase: 'idle', busy: false,
    availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
    sessionEpoch: 1, scope: { control: 'keyer-speed', receiver: 0 },
  });
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('CwPanel component rendering', () => {
  it('mounts without errors', () => {
    const t = mountPanel();
    expect(t.querySelector('.panel-body')).not.toBeNull();
  });

  it('renders RX mode line with current mode', () => {
    const t = mountPanel();
    expect(t.querySelector('.cw-mode-value')?.textContent).toBe('CW');
  });

  it('renders CW Pitch control', () => {
    const t = mountPanel();
    const labels = Array.from(t.querySelectorAll('.vc-label'));
    expect(labels.some((el) => el.textContent === 'CW Pitch')).toBe(true);
  });

  it('renders Key Speed control', () => {
    const t = mountPanel();
    const labels = Array.from(t.querySelectorAll('.vc-label'));
    expect(labels.some((el) => el.textContent === 'Key Speed')).toBe(true);
    expect(t.querySelector('.vc-discrete')).not.toBeNull();
  });

  it('dispatches Key Speed through its Discrete facade binding', () => {
    vi.useFakeTimers();
    const t = mountPanel({ keySpeed: 12 });
    const control = t.querySelector<HTMLElement>('[aria-label="Key Speed"]')!;

    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(50);

    expect(mockHandlers.onKeySpeedChange).toHaveBeenCalledExactlyOnceWith(13);
    vi.useRealTimers();
  });

  it('keeps rapid Key Speed keys on one debounce and uses the newest target', () => {
    vi.useFakeTimers();
    const t = mountPanel({ keySpeed: 12 });
    const control = t.querySelector<HTMLElement>('[aria-label="Key Speed"]')!;
    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(50);
    expect(mockHandlers.onKeySpeedChange).toHaveBeenCalledExactlyOnceWith(14);
    vi.useRealTimers();
  });

  it('preserves immediate wheel requests for both controls', () => {
    const t = mountPanel({ cwPitch: 600, keySpeed: 12 });
    t.querySelector<HTMLElement>('[aria-label="CW Pitch"]')!
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    t.querySelector<HTMLElement>('[aria-label="CW Pitch"]')!
      .dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true, cancelable: true }));
    t.querySelector<HTMLElement>('[aria-label="Key Speed"]')!
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    t.querySelector<HTMLElement>('[aria-label="Key Speed"]')!
      .dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true, cancelable: true }));
    expect(mockHandlers.onCwPitchChange).toHaveBeenCalledExactlyOnceWith(605);
    expect(mockHandlers.onKeySpeedChange).toHaveBeenCalledExactlyOnceWith(13);
  });

  it('does not dispatch either CW scalar while mounting or unmounting', () => {
    mountPanel();
    unmount(components.pop()!);
    expect(mockHandlers.onCwPitchChange).not.toHaveBeenCalled();
    expect(mockHandlers.onKeySpeedChange).not.toHaveBeenCalled();
  });

  it('renders SEMI break-in button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim() === 'SEMI')).toBe(true);
  });

  it('renders FULL break-in button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim() === 'FULL')).toBe(true);
  });

  it('renders APF button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim() === 'APF')).toBe(true);
  });

  it('renders TPF (twin peak) button', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim() === 'TPF')).toBe(true);
  });

  it('does not render AUTO TUNE when RX-assisted correction is unavailable', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim() === 'AUTO TUNE')).toBe(false);
  });

  it('renders AUTO TUNE when RX-assisted correction is available and delegates exactly once', () => {
    const t = mountPanel({ autoTuneAvailable: true });
    findButton(t, 'AUTO TUNE').click();
    expect(mockHandlers.onAutoTune).toHaveBeenCalledTimes(1);
  });

  it('unmounts cleanly', () => {
    const t = mountPanel();
    const comp = components.pop()!;
    unmount(comp);
    expect(t.innerHTML).toBe('');
  });
});

describe('CwPanel CW pitch domain (MOR-1682)', () => {
  const stepPitch = (t: HTMLElement) => {
    t.querySelector<HTMLElement>('[aria-label="CW Pitch"]')!
      .dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(50);
  };

  it('ranges and steps the pitch control by the profile exact domain (FTX-1: 300..1050, step 10)', () => {
    vi.useFakeTimers();
    const t = mountPanel({ cwPitchDomain: { min: 300, max: 1050, step: 10, origin: 300 } });
    const slider = t.querySelector<HTMLElement>('[aria-label="CW Pitch"][role="slider"]')!;
    expect(slider.getAttribute('aria-valuemin')).toBe('300');
    expect(slider.getAttribute('aria-valuemax')).toBe('1050');
    stepPitch(t);
    expect(mockHandlers.onCwPitchChange).toHaveBeenCalledExactlyOnceWith(610);
    vi.useRealTimers();
  });

  it('keeps the 5 Hz step for a legacy-domain radio (IC-7300 shape)', () => {
    vi.useFakeTimers();
    const t = mountPanel({ cwPitchDomain: { min: 300, max: 900, step: 5, origin: 300 } });
    stepPitch(t);
    expect(mockHandlers.onCwPitchChange).toHaveBeenCalledExactlyOnceWith(605);
    vi.useRealTimers();
  });

  it('keeps the 5 Hz step when the profile publishes no cw_pitch domain', () => {
    vi.useFakeTimers();
    const t = mountPanel({ cwPitchDomain: null });
    stepPitch(t);
    expect(mockHandlers.onCwPitchChange).toHaveBeenCalledExactlyOnceWith(605);
    vi.useRealTimers();
  });
});

describe('CwPanel key-speed domain (MOR-2475 F1)', () => {
  const slider = (t: HTMLElement) =>
    t.querySelector<HTMLElement>('[aria-label="Key Speed"][role="slider"]')!;

  it('ranges and steps the key-speed control by the profile domain', () => {
    vi.useFakeTimers();
    const t = mountPanel({ keySpeedDomain: { min: 5, max: 50, step: 1, origin: 5 } });
    expect(slider(t).getAttribute('aria-valuemin')).toBe('5');
    expect(slider(t).getAttribute('aria-valuemax')).toBe('50');
    slider(t).dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    vi.advanceTimersByTime(50);
    expect(mockHandlers.onKeySpeedChange).toHaveBeenCalledExactlyOnceWith(50);
    vi.useRealTimers();
  });

  it('keeps the 6..48 fallback when the profile publishes no key_speed domain', () => {
    vi.useFakeTimers();
    const t = mountPanel({ keySpeedDomain: null });
    expect(slider(t).getAttribute('aria-valuemin')).toBe('6');
    expect(slider(t).getAttribute('aria-valuemax')).toBe('48');
    slider(t).dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    vi.advanceTimersByTime(50);
    expect(mockHandlers.onKeySpeedChange).toHaveBeenCalledExactlyOnceWith(48);
    vi.useRealTimers();
  });
});

function findButton(t: HTMLElement, label: string): HTMLButtonElement {
  const buttons = Array.from(t.querySelectorAll('button'));
  const btn = buttons.find((b) => b.textContent?.trim() === label);
  if (!btn) throw new Error(`button ${label} not found`);
  return btn as HTMLButtonElement;
}

describe('CwPanel APF/TPF mode gating (MOR-492)', () => {
  it('enables the APF button when apfDisabled is false (CW) and forwards clicks', () => {
    const t = mountPanel({ currentMode: 'CW', apfDisabled: false });
    const apf = findButton(t, 'APF');
    expect(apf.disabled).toBe(false);
    apf.click();
    expect(mockHandlers.onApfChange).toHaveBeenCalled();
  });

  it('disables the APF button when apfDisabled is true and swallows clicks', () => {
    const t = mountPanel({ currentMode: 'USB', apfDisabled: true });
    const apf = findButton(t, 'APF');
    expect(apf.disabled).toBe(true);
    apf.click();
    expect(mockHandlers.onApfChange).not.toHaveBeenCalled();
  });

  it('enables the TPF button when tpfDisabled is false (RTTY) and forwards clicks', () => {
    const t = mountPanel({ currentMode: 'RTTY', tpfDisabled: false });
    const tpf = findButton(t, 'TPF');
    expect(tpf.disabled).toBe(false);
    tpf.click();
    expect(mockHandlers.onTwinPeakToggle).toHaveBeenCalled();
  });

  it('disables the TPF button when tpfDisabled is true and swallows clicks', () => {
    const t = mountPanel({ currentMode: 'USB', tpfDisabled: true });
    const tpf = findButton(t, 'TPF');
    expect(tpf.disabled).toBe(true);
    tpf.click();
    expect(mockHandlers.onTwinPeakToggle).not.toHaveBeenCalled();
  });
});

function vcValueFor(t: HTMLElement, label: string): string {
  const headers = Array.from(t.querySelectorAll('.vc-header'));
  const header = headers.find(
    (h) => h.querySelector('.vc-label')?.textContent === label,
  );
  if (!header) throw new Error(`ValueControl labeled "${label}" not found`);
  return header.querySelector('.vc-value')?.textContent ?? '';
}

/**
 * A12 (MOR-1409, Core #2317, coordinator adjudication comment 5246487510)
 * — unavailable command feedback must preserve the established
 * '---'-family placeholder rather than leak a non-finite value. The local
 * formatters also preserve the exact finite-value/unit rendering.
 */
describe('CwPanel — no "NaN" leak for unobserved pitch/speed (MOR-1409 A12)', () => {
  it('does not render a "NaN" substring for CW Pitch when cwPitch is non-finite', () => {
    const t = mountPanel({ cwPitch: Number.NaN });
    expect(vcValueFor(t, 'CW Pitch')).not.toMatch(/NaN/);
  });

  it('renders the established "---"-family placeholder for a non-finite CW Pitch', () => {
    const t = mountPanel({ cwPitch: Number.NaN });
    expect(vcValueFor(t, 'CW Pitch')).toBe('---\u00a0Hz');
  });

  it('does not render a "NaN" substring for Key Speed when keySpeed is non-finite', () => {
    const t = mountPanel({ keySpeed: Number.NaN });
    expect(vcValueFor(t, 'Key Speed')).not.toMatch(/NaN/);
  });

  it('renders the established "---"-family placeholder for a non-finite Key Speed', () => {
    const t = mountPanel({ keySpeed: Number.NaN });
    expect(vcValueFor(t, 'Key Speed')).toBe('---\u00a0WPM');
  });

  it('still renders the real formatted values for finite pitch/speed', () => {
    const t = mountPanel({ cwPitch: 700, keySpeed: 25 });
    expect(vcValueFor(t, 'CW Pitch')).toBe('700\u00a0Hz');
    expect(vcValueFor(t, 'Key Speed')).toBe('25\u00a0WPM');
  });
});

describe('CwPanel Break-in Delay release gesture (MOR-1754)', () => {
  const delay = (t: HTMLElement) =>
    t.querySelector<HTMLInputElement>('[data-testid="cw-break-in-delay"]')!;

  it.each(['pointer', 'keyboard'] as const)(
    'shows draft input immediately and commits one bounded command on %s release',
    (gesture) => {
    const t = mountPanel({ breakIn: 1 });
    const input = delay(t);
    input.dispatchEvent(gesture === 'pointer'
      ? new PointerEvent('pointerdown', { bubbles: true })
      : new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    for (const value of ['80', '96', '111']) {
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    flushSync();
    expect(mockHandlers.onBreakInDelayChange).not.toHaveBeenCalled();
    expect(t.querySelector('[data-testid="cw-break-in-delay-value"]')?.textContent).toBe('44%');
    input.dispatchEvent(gesture === 'pointer'
      ? new PointerEvent('pointerup', { bubbles: true })
      : new KeyboardEvent('keyup', { key: 'ArrowRight', bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    expect(mockHandlers.onBreakInDelayChange).toHaveBeenCalledExactlyOnceWith(111);
    },
  );

  it('cancels pointer and keyboard gestures without a trailing commit', () => {
    for (const event of [
      new Event('pointercancel', { bubbles: true }),
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    ]) {
      const t = mountPanel({ breakIn: 1 });
      const input = delay(t);
      input.value = '111';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(event);
      input.dispatchEvent(new Event('change', { bubbles: true }));
      flushSync();
      expect(input.value).toBe('64');
    }
    expect(mockHandlers.onBreakInDelayChange).not.toHaveBeenCalled();
  });

  it('disables the actual range when canonical feedback is unavailable', () => {
    Object.assign(mockFeedback, { confirmed: null, phase: 'unavailable', availability: 'unavailable' });
    const t = mountPanel({ breakIn: 1 });
    expect(delay(t).disabled).toBe(true);
    expect(delay(t).getAttribute('aria-valuetext')).toMatch(/unavailable/i);
  });
});
