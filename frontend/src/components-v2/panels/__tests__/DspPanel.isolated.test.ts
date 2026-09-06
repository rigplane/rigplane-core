import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { buildNrOptions, buildNotchOptions } from '../dsp-utils';
import { rawToPercentDisplay } from '../../../primitives/scalar/value-control-core';

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
const feedbackOverrides = vi.hoisted(() => new Map<string, Record<string, unknown>>());
vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveDspProps: () => mockProps,
  getDspHandlers: () => mockHandlers,
  getAutoNotchArmed: () => unarmed,
  getManualNotchArmed: () => unarmed,
  getDspControlFeedback: (field: 'nbLevel' | 'nbWidth' | 'notchFilter' | 'agcTimeConstant') => ({
    confirmed: field === 'notchFilter' ? mockProps.notchFreq : mockProps[field],
    target: null, requestedTarget: null, phase: 'idle' as const,
    busy: false, availability: 'available' as const, outcome: null,
    lifecycleId: null, transitionId: null, providerGeneration: 1, sessionEpoch: 7,
    scope: {
      control: field === 'nbLevel' ? 'nb-level'
        : field === 'nbWidth' ? 'nb-width'
          : field === 'notchFilter' ? 'notch-position' : 'agc-time',
      receiver: 0 as const,
    },
    repeatPolicy: 'latest-target-wins' as const,
    ...feedbackOverrides.get(field),
  }),
}));

import DspPanel from '../DspPanel.svelte';

describe('MOR-2423 DSP scalar feedback wiring contract', () => {
  it('adopts only the four leased scalar lanes and leaves manual notch width raw', () => {
    const source = readFileSync(path.resolve(process.cwd(), 'src/components-v2/panels/DspPanel.svelte'), 'utf8');

    expect(source.match(/getDspControlFeedback\('/g)).toHaveLength(4);
    expect(source).toContain("getDspControlFeedback('nbLevel')");
    expect(source).toContain("getDspControlFeedback('nbWidth')");
    expect(source).toContain("getDspControlFeedback('notchFilter')");
    expect(source).toContain("getDspControlFeedback('agcTimeConstant')");
    expect(source).not.toContain("getDspControlFeedback('manualNotchWidth')");
    expect(source).toContain("command: 'set_nb_level'");
    expect(source).toContain("command: 'set_nb_width'");
    expect(source).toContain("command: 'set_notch_filter'");
    expect(source).toContain("command: 'set_agc_time_constant'");
    expect(source.match(/const view = binding\.view;/g)).toHaveLength(1);
    expect(source).not.toContain('notchToggleActive');
  });
});

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
  feedbackOverrides.clear();
  Object.assign(mockProps, {
    nrMode: 0, nrLevel: 128, nbActive: false, nbLevel: 128,
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

  it('shows the NB level as percent on a 0-255 rig (IC-7610) when nbLevelPercent is set', () => {
    const t = mountPanel({ nbActive: true, nbLevel: 76, nbLevelPercent: true, nbLevelMax: 255 });
    const nbBtn = getFillButtons(t).find((b) => b.textContent?.trim().startsWith('NB'));
    const label = nbBtn?.textContent?.trim();
    // rawToPercentDisplay(76) === '30%'
    expect(label).toBe(`NB ${rawToPercentDisplay(76)}`);
    expect(label).not.toContain('76');
  });

  it('shows the NB level as the raw native value on a 0-10 rig (FTX-1) when nbLevelPercent is false', () => {
    // MOR-502: FTX-1 NB is a native 0-10 level — no nb_level 0-255 control
    // range — so the label must show the raw integer, matching the LCD skin.
    const t = mountPanel({ nbActive: true, nbLevel: 5, nbLevelPercent: false, nbLevelMax: 10 });
    const nbBtn = getFillButtons(t).find((b) => b.textContent?.trim().startsWith('NB'));
    const label = nbBtn?.textContent?.trim();
    expect(label).toBe('NB 5');
    expect(label).not.toContain('%');
  });
});

// ---------------------------------------------------------------------------
// NB depth/width capability gating (MOR-502)
// ---------------------------------------------------------------------------

function openNbModal(t: HTMLElement): void {
  vi.useFakeTimers();
  try {
    const nbBtn = getFillButtons(t).find((b) => b.textContent?.trim().startsWith('NB'));
    nbBtn?.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    vi.advanceTimersByTime(600);
    flushSync();
  } finally {
    vi.useRealTimers();
  }
}

describe('NB depth/width capability gating', () => {
  it('shows NB Depth and NB Width controls when hasNbDepth/hasNbWidth are true (IC-7610)', () => {
    const t = mountPanel({ nbActive: true, hasNbDepth: true, hasNbWidth: true });
    openNbModal(t);
    const modal = t.querySelector('[aria-label="Noise blanker settings"]');
    expect(modal).not.toBeNull();
    const text = modal?.textContent ?? '';
    expect(text).toContain('NB Depth');
    expect(text).toContain('NB Width');
  });

  it('hides NB Depth and NB Width controls when hasNbDepth/hasNbWidth are false (FTX-1)', () => {
    const t = mountPanel({ nbActive: true, hasNbDepth: false, hasNbWidth: false });
    openNbModal(t);
    const modal = t.querySelector('[aria-label="Noise blanker settings"]');
    expect(modal).not.toBeNull();
    const text = modal?.textContent ?? '';
    expect(text).not.toContain('NB Depth');
    expect(text).not.toContain('NB Width');
    // NB Level remains present regardless of depth/width.
    expect(text).toContain('NB Level');
  });
});

describe('MOR-2423 supplied feedback phase projection', () => {
  it.each([
    'unavailable', 'idle', 'submitted', 'queued', 'dispatched', 'awaiting-confirmation',
    'confirmed', 'failed', 'timed-out', 'cancelled', 'superseded',
  ] as const)('projects the supplied %s DTO without inventing source lifecycle evidence', (phase) => {
    const busy = ['submitted', 'queued', 'dispatched', 'awaiting-confirmation'].includes(phase);
    const terminal = ['confirmed', 'failed', 'timed-out', 'cancelled', 'superseded'].includes(phase);
    feedbackOverrides.set('nbLevel', {
      phase, busy, availability: phase === 'unavailable' ? 'unavailable' : 'available',
      confirmed: phase === 'unavailable' ? null : 128,
      target: busy ? 129 : null, requestedTarget: phase === 'idle' ? null : 129,
      outcome: terminal ? { phase, ...(phase === 'failed' ? { error: 'radio rejected' } : {}) } : null,
      lifecycleId: phase === 'idle' || phase === 'unavailable' ? null : `life-${phase}`,
      transitionId: phase === 'idle' || phase === 'unavailable' ? null : `life-${phase}:${phase}`,
    });
    const t = mountPanel({ nbActive: true });
    openNbModal(t);
    const control = t.querySelector<HTMLElement>('[aria-label="NB Level"]')!;

    expect(control.dataset.commandPhase).toBe(phase);
    expect(control.getAttribute('aria-busy')).toBe(String(busy));
    expect(control.getAttribute('aria-disabled')).toBe(String(phase === 'unavailable'));
    expect(t.querySelectorAll('[data-control-feedback-status]')).toHaveLength(
      phase === 'idle' || phase === 'unavailable' ? 0 : 1,
    );
  });

  it.each([
    ['nbLevel', { nbActive: true }, 'NB', 'NB Level', 'hbar'],
    ['nbWidth', { nbActive: true }, 'NB', 'NB Width', 'hbar'],
    ['notchFilter', { notchMode: 'manual', notchFreq: 127 }, 'NOTCH', 'Notch Position', 'hbar'],
    ['agcTimeConstant', { agcTimeConstant: 4 }, 'AGC-T', 'AGC Time', 'discrete'],
  ] as const)(
    'retains hidden %s failure facts across repeated modal opens without a second live owner',
    (field, props, buttonLabel, controlLabel, renderer) => {
      feedbackOverrides.set(field, {
        phase: 'failed', busy: false, target: null, requestedTarget: 4,
        outcome: { phase: 'failed', error: `${field} rejected` },
        lifecycleId: `${field}-life`, transitionId: `${field}-failed`,
      });
      const t = mountPanel(props);

      const open = () => {
        const button = getFillButtons(t).find((candidate) =>
          candidate.textContent?.trim().startsWith(buttonLabel));
        if (buttonLabel === 'AGC-T') {
          button?.click();
        } else {
          vi.useFakeTimers();
          button?.dispatchEvent(new Event('pointerdown', { bubbles: true }));
          vi.advanceTimersByTime(600);
          vi.useRealTimers();
        }
        flushSync();
      };
      const assertCurrent = () => {
        const control = t.querySelector<HTMLElement>(`[aria-label="${controlLabel}"]`)!;
        expect(control.dataset.commandPhase).toBe('failed');
        if (renderer === 'discrete') {
          expect(t.querySelector('[data-control-feedback-current-status]')?.textContent)
            .toContain(`${field} rejected`);
          expect(t.querySelector('[data-control-feedback-status]')).toBeNull();
        } else {
          expect(control.closest('.vc-hbar')?.querySelectorAll('[data-control-feedback-status]'))
            .toHaveLength(1);
          expect(control.closest('.vc-hbar')?.querySelector('[data-control-feedback-status]')?.textContent)
            .toContain(`${field} rejected`);
        }
      };

      open();
      assertCurrent();
      t.querySelector<HTMLButtonElement>('[aria-label="Close DSP settings"]')!.click();
      flushSync();
      expect(t.querySelector(`[aria-label="${controlLabel}"]`)).toBeNull();

      open();
      assertCurrent();
    },
  );
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
