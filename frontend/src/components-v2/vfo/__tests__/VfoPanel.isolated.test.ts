import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { createRawSnippet, mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import VfoPanel from '../VfoPanel.svelte';
import { formatRitOffset } from '../vfo-utils';

// ---------------------------------------------------------------------------
// formatRitOffset
// ---------------------------------------------------------------------------

describe('formatRitOffset', () => {
  it('positive offset shows + sign in Hz', () => {
    expect(formatRitOffset(120)).toBe('+120');
  });

  it('negative offset shows − sign in Hz', () => {
    expect(formatRitOffset(-250)).toBe('−250');
  });

  it('zero offset shows + sign in Hz', () => {
    expect(formatRitOffset(0)).toBe('+0');
  });

  it('rounds a fractional offset to whole Hz', () => {
    expect(formatRitOffset(5000)).toBe('+5000');
  });
});

// ---------------------------------------------------------------------------
// VfoPanel component
// ---------------------------------------------------------------------------

vi.mock('$lib/stores/capabilities.svelte', () => ({
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  getCapabilities: vi.fn(() => ({
    freqRanges: [
      {
        start: 14000000,
        end: 14350000,
        bands: [{ name: '20m', start: 14000000, end: 14350000, default: 14074000 }],
      },
      {
        start: 7000000,
        end: 7300000,
        bands: [{ name: '40m', start: 7000000, end: 7300000, default: 7074000 }],
      },
    ],
  })),
  hasDualReceiver: vi.fn(() => true),
  getSmeterCalibration: vi.fn(() => null),
  getSmeterRedline: vi.fn(() => null),
}));

import { getSmeterCalibration, receiverLabel } from '$lib/stores/capabilities.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountPanel(props: ComponentProps<typeof VfoPanel>) {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(VfoPanel, { target: t, props });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  vi.mocked(receiverLabel).mockImplementation((id: 'MAIN' | 'SUB') => id);
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

const bandSections = {
  tray: { band: { key: 'band', text: '20M', lit: true } },
  annunciators: [],
  dsp: [],
  under: {},
  tx: { lit: false, state: 'unknown' },
} satisfies ComponentProps<typeof VfoPanel>['sections'];

const baseProps: ComponentProps<typeof VfoPanel> = {
  receiver: 'main',
  receiverLabel: 'MAIN',
  slotTag: 'A',
  freq: 14074000,
  displayHz: 14074000,
  mode: 'USB',
  filter: '2.4k',
  sValue: 100,
  isActive: true,
  sections: bandSections,
  onModeClick: vi.fn(),
};

const emptySections = {
  tray: {},
  annunciators: [],
  dsp: [],
  under: {},
  tx: { lit: false, state: 'unknown' },
} satisfies ComponentProps<typeof VfoPanel>['sections'];

describe('panel structure', () => {
  it('renders the VFO label MAIN for receiver=main', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.vfo-label')?.textContent?.trim()).toBe('MAIN');
  });

  it('renders the VFO label SUB for receiver=sub', () => {
    const t = mountPanel({ ...baseProps, receiver: 'sub' });
    expect(t.querySelector('.vfo-label')?.textContent?.trim()).toBe('SUB');
  });

  it('renders mode chip with correct mode text', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.mode-badge-wrapper [data-chip="mode"]')?.textContent?.trim()).toBe('USB');
  });

  it('renders filter chip with correct filter text', () => {
    const t = mountPanel(baseProps);
    // MOR-2509: the filter chip is a large receiver-row chip beside the mode
    // chip; the tray and DSP group carry the remaining facts.
    const receiverRow = t.querySelector('[data-vfo-row="receiver"]');
    expect(Array.from(receiverRow?.querySelectorAll('[data-chip]') ?? [])
      .some((el) => el.textContent?.trim() === '2.4k')).toBe(true);
  });

  it('renders a .freq element (FrequencyDisplay)', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.freq')).not.toBeNull();
  });

  it('renders the S-meter svg', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('svg')).not.toBeNull();
  });

  // MOR-1451: `sValue` is the raw CI-V S-meter byte (0-255), not calibrated
  // dB-rel-S9. Before the fix, VfoPanel passed it straight into
  // `LinearSMeter` (which expects calibrated dB) — raw 53 rendered as
  // "S9+40" regardless of the actual signal. `getSmeterCalibration` is
  // mocked to `null` above (no radio profile curve in this suite), so the
  // honest-fallback path applies: the S meter must render no number at
  // all (MOR-2705 part 4a — the raw count is not an operator reading),
  // never a fabricated S-unit.
  it('does not render S9+40 for a raw sMeter reading (MOR-1451)', () => {
    const t = mountPanel({ ...baseProps, sValue: 53 });
    const text = t.querySelector('svg')?.textContent ?? '';
    expect(text).not.toContain('S9+40');
    expect(text).not.toContain('53');
    expect(text).not.toContain('uncalibrated');
  });

  it('renders the tray band chip from its sections', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('[data-tray-tab="band"]')?.textContent?.trim()).toBe('20M');
  });

  it('renders no tag chips in the receiver row (no BAR, no slot tag)', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.header-tag')).toBeNull();
    const names = t.querySelectorAll('.vfo-label');
    expect(names.length).toBe(1);
  });

  it('renders the fixed rows even when sections are empty', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('[data-vfo-row="tray"]')).not.toBeNull();
    expect(t.querySelector('[data-vfo-row="receiver"]')).not.toBeNull();
    expect(t.querySelector('[data-vfo-row="under"]')).not.toBeNull();
  });
});

describe('active/inactive state', () => {
  it('lets an inactive panel header select its VFO without making the frequency area a selector', () => {
    const onSelectHeader = vi.fn();
    const t = mountPanel({
      receiver: 'main', receiverLabel: 'VFO B', slotTag: 'B', freq: 7150000,
      mode: 'LSB', filter: 'NARROW', sValue: null, meterPresent: false,
      isActive: false, sections: emptySections, onSelectHeader,
    });

    t.querySelector<HTMLButtonElement>('.panel-header')?.click();
    expect(onSelectHeader).toHaveBeenCalledOnce();
    t.querySelector<HTMLElement>('[data-vfo-freq]')?.click();
    t.querySelector<HTMLElement>('[data-vfo-freq]')?.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    t.querySelector<HTMLElement>('[data-vfo-freq]')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(onSelectHeader).toHaveBeenCalledOnce();
  });

  it('keeps inactive mode and frequency controls inert while the header remains selectable', () => {
    const onModeClick = vi.fn();
    const t = mountPanel({
      receiver: 'main', receiverLabel: 'VFO B', slotTag: 'B', freq: 7150000,
      mode: 'LSB', filter: 'NARROW', sValue: null, meterPresent: false,
      isActive: false, sections: emptySections, frequencyDisabled: true, controlsDisabled: true,
      onModeClick, onSelectHeader: vi.fn(),
    });
    const mode = t.querySelector<HTMLElement>('.mode-badge-wrapper')!;
    mode.click();
    mode.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(onModeClick).not.toHaveBeenCalled();
    expect(mode.getAttribute('aria-disabled')).toBe('true');
    expect(mode.getAttribute('title')).toBeNull();
    expect(t.querySelector('[data-vfo-freq]')?.getAttribute('data-freq-tunable')).toBe('false');
  });

  it('reserves the meter row for an inactive peer without painting a second meter', () => {
    const t = mountPanel({
      receiver: 'main', receiverLabel: 'VFO B', slotTag: 'B', freq: 7150000,
      mode: 'LSB', filter: 'NARROW', sValue: null, meterPresent: false,
      isActive: false, sections: emptySections, reserveMeterSpace: true,
    });

    expect(t.querySelector('[data-meter-space="reserved"]')).not.toBeNull();
    expect(t.querySelector('[data-testid="receiver-s-meter"]')).toBeNull();
    expect(t.querySelector('.header-tag')).toBeNull();
  });

  it('panel has active class when isActive=true', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.panel')?.classList.contains('active')).toBe(true);
  });

  it('panel does not have active class when isActive=false', () => {
    const t = mountPanel({ ...baseProps, isActive: false });
    expect(t.querySelector('.panel')?.classList.contains('active')).toBe(false);
  });

  it('FrequencyDisplay has inactive class when isActive=false', () => {
    const t = mountPanel({ ...baseProps, isActive: false });
    expect(t.querySelector('.freq')?.classList.contains('inactive')).toBe(true);
  });

  it('FrequencyDisplay does not have inactive class when isActive=true', () => {
    const t = mountPanel(baseProps);
    expect(t.querySelector('.freq')?.classList.contains('inactive')).toBe(false);
  });

  it('MAIN panel exposes cyan receiver accent vars for control chrome', () => {
    const t = mountPanel(baseProps);
    const style = t.querySelector('.panel')?.getAttribute('style') ?? '';
    expect(style).toContain('--receiver-accent: var(--v2-receiver-main-accent)');
    expect(style).toContain('--receiver-control-border: var(--v2-vfo-main-control-border)');
  });

  it('SUB panel exposes white receiver accent vars for control chrome', () => {
    const t = mountPanel({ ...baseProps, receiver: 'sub' });
    const style = t.querySelector('.panel')?.getAttribute('style') ?? '';
    expect(style).toContain('--receiver-accent: var(--v2-receiver-sub-accent)');
    expect(style).toContain('--receiver-control-border: var(--v2-vfo-sub-control-border)');
  });
});

describe('RIT display', () => {
  const ritSections = (rit: { text: string; lit: boolean }) => ({
    ...bandSections,
    under: { rit: { key: 'rit', ...rit } },
  });

  it('keeps the under row without a RIT chip when sections.under is empty', () => {
    const t = mountPanel({ ...baseProps, sections: { ...bandSections, under: {} } });
    expect(t.querySelector('[data-chip="rit"]')).toBeNull();
    expect(t.querySelector('[data-vfo-row="under"]')).not.toBeNull();
  });

  it('renders the RIT chip unlit in place when lit=false', () => {
    const t = mountPanel({ ...baseProps, sections: ritSections({ text: 'RIT', lit: false }) });
    const rit = t.querySelector('[data-chip="rit"]');
    expect(rit).not.toBeNull();
    expect(rit?.getAttribute('data-lit')).toBe('false');
    expect(rit?.textContent?.trim()).toBe('RIT');
  });

  it('renders the RIT chip lit with its signed Hz offset when active', () => {
    const t = mountPanel({ ...baseProps, sections: ritSections({ text: 'RIT +120', lit: true }) });
    expect(t.querySelector('[data-chip="rit"]')?.textContent?.trim()).toBe('RIT +120');
    expect(t.querySelector('[data-chip="rit"]')?.getAttribute('data-lit')).toBe('true');
  });

  it('shows a negative RIT offset in Hz correctly', () => {
    const t = mountPanel({ ...baseProps, sections: ritSections({ text: 'RIT −250', lit: true }) });
    expect(t.querySelector('[data-chip="rit"]')?.textContent?.trim()).toBe('RIT −250');
  });
});

describe('callbacks', () => {
  it('calls onModeClick when mode badge is clicked', () => {
    const onModeClick = vi.fn();
    const t = mountPanel({ ...baseProps, onModeClick });
    t.querySelector<HTMLElement>('.mode-badge-wrapper')?.click();
    expect(onModeClick).toHaveBeenCalledOnce();
  });
});

describe('explicit presentation contract', () => {
  const explicit = {
    receiver: 'main' as const,
    receiverLabel: 'MAIN',
    slotTag: 'A',
    freq: 14_074_000,
    displayHz: 14_075_000,
    pendingDisplayHz: 14_076_000,
    contextKey: '2/main_sub:MAIN:A',
    frequencyDisabled: false,
    mode: 'USB',
    filter: 'FIL1',
    sValue: 0,
    meterPresent: true,
    meterOperational: true,
    isActive: true,
    sections: emptySections,
  } satisfies ComponentProps<typeof VfoPanel>;

  it('uses confirmed truth as the tuning base while display and pending remain presentation-only', () => {
    const onFreqChange = vi.fn();
    const t = mountPanel({ ...explicit, onFreqChange });
    expect(t.querySelector('.freq')?.textContent?.replace(/\s/g, '')).toBe('14.076.000');
    const digits = t.querySelectorAll<HTMLElement>('.digit');
    digits[digits.length - 1]?.click();
    digits[digits.length - 1]?.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    expect(onFreqChange).toHaveBeenCalledExactlyOnceWith(14_074_001);
  });

  it('opens from the whole readout without tuning', () => {
    const onFrequencyClick = vi.fn();
    const onFreqChange = vi.fn();
    const t = mountPanel({ ...explicit, onFrequencyClick, onFreqChange });
    const trigger = t.querySelector<HTMLElement>('[data-vfo-freq]')!;
    const readout = t.querySelector<HTMLElement>('[data-vfo-freq] .freq')!;
    const digit = t.querySelectorAll<HTMLElement>('.digit').item(4);

    digit.click();
    expect(onFrequencyClick).toHaveBeenCalledExactlyOnceWith(trigger);
    t.querySelector<HTMLElement>('.sep')?.click();
    readout.click();
    expect(onFrequencyClick).toHaveBeenCalledTimes(3);
    expect(onFreqChange).not.toHaveBeenCalled();
  });

  it.each(['Enter', ' '] as const)(
    'exposes inactive direct entry as an enabled button and opens with %s without tuning',
    (key) => {
      const onFrequencyClick = vi.fn();
      const onFreqChange = vi.fn();
      const t = mountPanel({
        ...explicit, receiverLabel: 'MAIN B', isActive: false,
        frequencyDisabled: true, controlsDisabled: true,
        onFrequencyClick, onFreqChange,
      });
      const trigger = t.querySelector<HTMLElement>('[data-vfo-freq]')!;
      const readout = trigger.querySelector<HTMLElement>('.freq')!;

      expect(trigger.getAttribute('role')).toBe('button');
      expect(trigger.getAttribute('aria-label')).toBe('Set frequency — MAIN B');
      expect(trigger.getAttribute('aria-disabled')).toBeNull();
      expect(trigger.getAttribute('tabindex')).toBe('0');
      expect(readout.parentElement?.getAttribute('aria-hidden')).toBe('true');
      trigger.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));

      expect(onFrequencyClick).toHaveBeenCalledExactlyOnceWith(trigger);
      expect(onFreqChange).not.toHaveBeenCalled();
    },
  );

  it('opens inactive direct entry by pointer without digit selection or wheel and arrow tuning', () => {
    const onFrequencyClick = vi.fn();
    const onFreqChange = vi.fn();
    const t = mountPanel({
      ...explicit, receiverLabel: 'MAIN B', isActive: false,
      frequencyDisabled: true, controlsDisabled: true,
      onFrequencyClick, onFreqChange,
    });
    const trigger = t.querySelector<HTMLElement>('[data-vfo-freq]')!;
    const readout = trigger.querySelector<HTMLElement>('.freq')!;
    const digit = readout.querySelector<HTMLElement>('.digit')!;

    digit.click();
    expect(onFrequencyClick).toHaveBeenCalledExactlyOnceWith(trigger);
    expect(digit.classList.contains('selected')).toBe(false);
    digit.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(onFreqChange).not.toHaveBeenCalled();
  });

  it('does not expose unsupported inactive frequency as an entry button', () => {
    const t = mountPanel({
      ...explicit, isActive: false, frequencyDisabled: true, controlsDisabled: true,
    });
    const wrapper = t.querySelector<HTMLElement>('[data-vfo-freq]')!;
    expect(wrapper.getAttribute('role')).toBeNull();
    expect(wrapper.getAttribute('tabindex')).toBeNull();
    expect(wrapper.querySelector('.freq')?.parentElement?.getAttribute('aria-hidden')).toBeNull();
  });

  // MOR-2425/R29+R40: a HELD frequency stays tunable. `frequencyState` alone
  // no longer disables the arithmetic control; a display carrying no value
  // ('unknown'/'unsupported') still does, as does `frequencyDisabled`.
  it.each(['current', 'stale'] as const)('keeps the frequency control tunable while the display is %s', (frequencyState) => {
    const onFreqChange = vi.fn();
    const t = mountPanel({ ...explicit, frequencyState, onFreqChange });
    const digits = t.querySelectorAll<HTMLElement>('.digit');
    digits[digits.length - 1]?.click();
    digits[digits.length - 1]?.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    expect(onFreqChange).toHaveBeenCalledExactlyOnceWith(14_074_001);
  });

  it.each(['unknown', 'unsupported'] as const)('locks the frequency control while the display is %s', (frequencyState) => {
    const onFreqChange = vi.fn();
    const t = mountPanel({ ...explicit, frequencyState, onFreqChange });
    const digits = t.querySelectorAll<HTMLElement>('.digit');
    digits[digits.length - 1]?.click();
    digits[digits.length - 1]?.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    expect(onFreqChange).not.toHaveBeenCalled();
  });

  it('does not mount an arithmetic frequency control when confirmed truth is unknown', () => {
    const t = mountPanel({ ...explicit, freq: null, displayHz: null, pendingDisplayHz: null });
    expect(t.querySelector('.digit')).toBeNull();
    // MOR-2509: the unobserved readout paints no glyph at all — the fixed
    // slot keeps its width and the dim unknown paint.
    expect(t.querySelector('[data-vfo-freq]')?.textContent?.trim()).toBe('');
  });

  // MOR-2688 S5b: with confirmed truth unknown, the static readout prints a
  // finite display value and nothing for a non-finite one.
  it.each([
    [14_074_000, '14.074.000'],
    [Number.NaN, ''],
  ] as const)('prints display value %s in the static readout as %j', (displayHz, text) => {
    const t = mountPanel({ ...explicit, freq: null, displayHz, pendingDisplayHz: null });
    expect(t.querySelector('.unknown-frequency')?.textContent).toBe(text);
  });

  it('keeps the established wrapper hook and the real frequency control in tab order', () => {
    const t = mountPanel(explicit);
    expect(t.querySelector('[data-vfo-freq]')?.classList.contains('vfo-freq')).toBe(true);
    expect(t.querySelector('[data-vfo-freq]')?.getAttribute('data-freq-tunable')).toBe('true');
    expect(t.querySelector('.freq')?.getAttribute('tabindex')).toBe('0');
  });

  it('keeps known zero distinct from unknown and structural absence', () => {
    const known = mountPanel(explicit);
    expect(known.querySelector('svg')?.getAttribute('aria-label') ?? '').not.toContain('?');
    const unknown = mountPanel({ ...explicit, sValue: null, meterOperational: false });
    expect(unknown.querySelector('[data-testid="receiver-s-meter"]')?.getAttribute('aria-label')).toBe('MAIN S meter');
    const absent = mountPanel({ ...explicit, meterPresent: false });
    expect(absent.querySelector('[data-testid="receiver-s-meter"]')).toBeNull();
  });

  // MOR-2688 S5b: the meter draws a finite value and a non-finite one as
  // unread, seen through the meter's accessible name on a calibrated radio.
  it.each([
    [60, 'S meter S9+60'],
    [Number.NaN, 'S meter'],
  ] as const)('names S-meter value %s as %j on a calibrated radio', (sValue, name) => {
    vi.mocked(getSmeterCalibration).mockReturnValue([
      { raw: 0, actual: -54, label: 'S0' },
      { raw: 120, actual: 0, label: 'S9' },
      { raw: 255, actual: 60, label: 'S9+60' },
    ]);
    try {
      const t = mountPanel({ ...explicit, sValue });
      expect(t.querySelector('[data-testid="receiver-s-meter"] svg')?.getAttribute('aria-label')).toBe(name);
    } finally {
      vi.mocked(getSmeterCalibration).mockReturnValue(null);
    }
  });

  it('places caller-owned frequency and meter snippets in the established seats', () => {
    const frequency = createRawSnippet(() => ({
      render: () => '<span data-hosted-frequency>host frequency</span>',
    }));
    const sMeter = createRawSnippet(() => ({
      render: () => '<span data-hosted-s-meter>host meter</span>',
    }));
    const t = mountPanel({ ...explicit, frequency, frequencyDisabled: true, sMeter });
    expect(t.querySelector('[data-hosted-frequency]')?.closest('[data-vfo-freq]')).not.toBeNull();
    expect(t.querySelector('[data-vfo-freq]')?.getAttribute('data-freq-tunable')).toBe('false');
    expect(t.querySelector('[data-hosted-s-meter]')?.closest('[data-testid="receiver-s-meter"]')).not.toBeNull();
    expect(t.querySelector('.freq.interactive')).toBeNull();
    expect(t.querySelector('[data-testid="receiver-s-meter"] svg')).toBeNull();
  });

  it('emits the exact explicit slot choice key without inventing A/B', () => {
    const onSelectSlot = vi.fn();
    const t = mountPanel({
      ...explicit,
      slotChoices: [{
        key: 'SUB:B', receiver: 'sub', slot: 'B', label: 'SUB B',
        frequencyText: '21.295.000', active: false, activeSlot: false,
        txTarget: false, disabled: false,
      }],
      onSelectSlot,
    });
    t.querySelector<HTMLButtonElement>('[data-vfo-select]')?.click();
    expect(onSelectSlot).toHaveBeenCalledExactlyOnceWith('SUB:B');
  });

  // MOR-1331: the card's own clamp at its seam — a +1 MHz wheel step past
  // the band's tuneMaxHz clamps to the edge, and a half-known bound pair
  // keeps the legacy wide-open clamp exactly.
  it('clamps a digit step to tuneMaxHz when both band edges are known', () => {
    const onFreqChange = vi.fn();
    const t = mountPanel({
      ...explicit, freq: 14_250_000, tuneMinHz: 14_200_000, tuneMaxHz: 14_300_000, onFreqChange,
    });
    const digits = t.querySelectorAll<HTMLElement>('.digit');
    const mhzDigit = [...digits].find((digit) => digit.textContent === '4')!;
    mhzDigit.click();
    mhzDigit.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    expect(onFreqChange).toHaveBeenCalledExactlyOnceWith(14_300_000);
  });

  it('keeps the legacy wide clamp while one band edge is unknown', () => {
    const onFreqChange = vi.fn();
    const t = mountPanel({
      ...explicit, freq: 14_250_000, tuneMinHz: 30_000, tuneMaxHz: null, onFreqChange,
    });
    // 14.250.000 renders as "14.250.000": the first digit is the 10 MHz '1'.
    // Stepping it stays inside the legacy clamp and proves the band edge did
    // not engage (a clamped read would pin to tuneMaxHz=null, i.e. nothing).
    const digits = t.querySelectorAll<HTMLElement>('.digit');
    const tenMhzDigit = digits[0];
    expect(tenMhzDigit.textContent).toBe('1');
    tenMhzDigit.click();
    tenMhzDigit.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    expect(onFreqChange).toHaveBeenCalledExactlyOnceWith(14_250_000 + 10_000_000);
  });

  it('contains no capability, runtime, or store imports', () => {
    const source = readFileSync('src/components-v2/vfo/VfoPanel.svelte', 'utf8');
    expect(source).not.toMatch(/stores\/|runtime\/|capabilities/);
  });

  it('keeps local meter context on the meter fallback', () => {
    const panel = readFileSync('src/components-v2/vfo/VfoPanel.svelte', 'utf8');
    const meter = panel.match(/<LinearSMeter([\s\S]*?)\/>/)?.[1] ?? '';
    expect(meter).toMatch(/source=\{meterSource\}/);
    expect(meter).toMatch(/session=\{continuitySession\}/);
  });
});

describe('unread mode/filter sentinel renders as an unlit legend (MOR-2673)', () => {
  // `toVfoProps` now reports `''` for an unread mode/filter (MOR-2673). The
  // panel maps it exactly like its pre-existing `null` treatment: the unlit
  // MODE/FIL legend — an empty LCD segment with the slot kept — never a dash
  // run and never a lit chip.
  const unreadProps = {
    receiver: 'main' as const,
    receiverLabel: 'MAIN',
    slotTag: 'A',
    freq: 14_074_000,
    displayHz: 14_074_000,
    mode: '',
    filter: '',
    sValue: 0,
    isActive: true,
    sections: emptySections,
    onModeClick: () => {},
  } satisfies ComponentProps<typeof VfoPanel>;

  it('renders the unlit MODE/FIL legends for the empty-string sentinel, like null', () => {
    for (const mode of ['', null] as const) {
      const chip = mountPanel({ ...unreadProps, mode }).querySelector('[data-chip="mode"]');
      expect(chip?.textContent?.trim()).toBe('MODE');
      expect(chip?.getAttribute('data-lit')).toBe('false');
    }
    for (const filter of ['', null] as const) {
      const chip = mountPanel({ ...unreadProps, filter }).querySelector('[data-chip="filter"]');
      expect(chip?.textContent?.trim()).toBe('FIL');
      expect(chip?.getAttribute('data-lit')).toBe('false');
    }
  });

  it('keeps the change-mode title free of the sentinel for an unread mode', () => {
    const title = mountPanel(unreadProps).querySelector('.mode-badge-wrapper')?.getAttribute('title');
    expect(title).toBe('Change mode');
    expect(title).not.toContain('---');
  });

  it('still renders a real mode lit, with its reading in the title', () => {
    const t = mountPanel({ ...unreadProps, mode: 'USB', filter: 'FIL1' });
    const chip = t.querySelector('[data-chip="mode"]');
    expect(chip?.textContent?.trim()).toBe('USB');
    expect(chip?.getAttribute('data-lit')).toBe('true');
    expect(t.querySelector('.mode-badge-wrapper')?.getAttribute('title')).toBe('Change mode (current: USB)');
  });
});
