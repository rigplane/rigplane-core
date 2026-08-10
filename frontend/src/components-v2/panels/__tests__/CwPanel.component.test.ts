import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

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

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveCwProps: () => mockProps,
  getCwHandlers: () => mockHandlers,
}));

import CwPanel from '../CwPanel.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountPanel(overrides?: Partial<typeof mockProps>) {
  if (overrides) Object.assign(mockProps, overrides);
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
  });
  Object.values(mockHandlers).forEach((fn) => fn.mockClear());
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

  it('renders AUTO TUNE button (software CW auto-tune, #675)', () => {
    const t = mountPanel();
    const buttons = Array.from(t.querySelectorAll('button'));
    expect(buttons.some((b) => b.textContent?.trim() === 'AUTO TUNE')).toBe(true);
  });

  it('unmounts cleanly', () => {
    const t = mountPanel();
    const comp = components.pop()!;
    unmount(comp);
    expect(t.innerHTML).toBe('');
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
 * — a connected receiver that has never reported `cwPitch`/`keySpeed`
 * (optional fields) passes the `hasCw` capability gate with `NaN` values
 * (panel-props.ts no longer fabricates `?? 600`/`?? 12`; `p.cwPitch ?? 600`
 * in this component's own script does not catch `NaN` either — only
 * `null`/`undefined`). Unguarded, the default `ValueControl` display
 * renders the literal "NaN Hz"/"NaN WPM" (verifier-executed probe on the
 * unguarded candidate). The local `formatCwPitchDisplay`/
 * `formatKeySpeedDisplay` guards must render the established
 * '---'-family placeholder instead.
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
