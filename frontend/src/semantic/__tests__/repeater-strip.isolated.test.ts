/**
 * MOR-2111 PR2 — the per-receiver repeater strip's render behavior.
 *
 * The strip lives in `VfoPanel`'s under-row (the RIT/XIT/SPLIT row) and is
 * gated in `VfoSurface::standardPanelSections` on the receiver's own
 * `repeater.<main|sub>.inRepeaterBand` reading: known and true ⇒ the strip
 * is drawn; HF (known and false) ⇒ not drawn at all (the owner rule "what
 * the radio does not have is not drawn"). Unread facts keep every chip in
 * place, unlit, with its label — never a placeholder.
 *
 * The deck height must not move when a receiver enters/leaves a repeater
 * band: the strip reuses the under-row's existing 22px chip rhythm (the
 * slate value is 20px, inside the same row) and the row never wraps, so the
 * strip contributes no new vertical space. Pinned below as source pins,
 * since jsdom performs no layout.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { flushSync, mount, unmount } from 'svelte';
import type { ComponentProps } from 'svelte';
import VfoPanel, { type VfoPanelRepeaterStrip, type VfoPanelSections } from '../../components-v2/vfo/VfoPanel.svelte';
import VfoSurface from '../VfoSurface.svelte';
import { topologyFixtures } from '../fixtures/topologies';
import type { RadioViewModel, RepeaterField, RepeaterShift, RepeaterToneMode, RepeaterViewModel } from '../radio-view-model';
import { setLocale } from '$lib/i18n/store.svelte';

vi.mock('$lib/stores/capabilities.svelte', () => ({
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  getCapabilities: vi.fn(() => ({ freqRanges: [] })),
  getSmeterCalibration: vi.fn(() => null),
  getSmeterRedline: vi.fn(() => null),
  hasDualReceiver: vi.fn(() => true),
}));

let components: Array<ReturnType<typeof mount>> = [];

function mountPanel(
  sections: Partial<VfoPanelSections>,
  props: Partial<ComponentProps<typeof VfoPanel>> = {},
): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const full: VfoPanelSections = {
    tray: {}, annunciators: [], dsp: [], under: {},
    tx: { lit: false, state: 'unknown' },
    ...sections,
  };
  const component = mount(VfoPanel, {
    target,
    props: { receiver: 'sub', receiverLabel: 'SUB', isActive: false, mode: null, filter: null, sections: full, ...props },
  });
  flushSync();
  components.push(component);
  return target;
}

const strip = (overrides: Partial<VfoPanelRepeaterStrip> = {}): VfoPanelRepeaterStrip => ({
  toneMode: 'off',
  pendingToneMode: null,
  shift: 'simplex',
  pendingShift: null,
  toneFreqText: '88.5',
  toneFreqPending: false,
  ...overrides,
});

function withRepeater(fixture: RadioViewModel, main: boolean, sub: boolean): RadioViewModel {
  const field = (value: boolean): RepeaterField<boolean> =>
    ({ reading: { status: 'known', value }, availability: { structural: true, operational: true } });
  const toneMode = (value: RepeaterToneMode): RepeaterField<RepeaterToneMode> =>
    ({ reading: { status: 'known', value }, availability: { structural: true, operational: true } });
  const toneFreq = (value: number): RepeaterField<number> =>
    ({ reading: { status: 'known', value }, availability: { structural: true, operational: true } });
  const shift = (value: RepeaterShift): RepeaterField<RepeaterShift> =>
    ({ reading: { status: 'known', value }, availability: { structural: true, operational: true } });
  const receiver = (inBand: boolean): RepeaterViewModel['main'] => ({
    inRepeaterBand: field(inBand), toneMode: toneMode('off'),
    toneFreq: toneFreq(8850), shift: shift('simplex'),
  });
  const repeater: RepeaterViewModel = { main: receiver(main), sub: receiver(sub) };
  return { ...fixture, repeater };
}

beforeEach(() => {
  components = [];
  setLocale('en-US');
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
});

describe('the strip renders only while the receiver is in a repeater band', () => {
  it('MAIN on HF, SUB in band: the strip is drawn on SUB alone', () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(VfoSurface, {
      target,
      props: { viewModel: withRepeater(topologyFixtures['2/main_sub'], false, true), appearance: 'standard' },
    });
    flushSync();
    components.push(component);
    expect(target.querySelector('[data-receiver-instrument="SUB"] [data-vfo-row="repeater"]')).not.toBeNull();
    expect(target.querySelector('[data-receiver-instrument="MAIN"] [data-vfo-row="repeater"]')).toBeNull();
  });

  it('both receivers on HF: no strip on either panel', () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(VfoSurface, {
      target,
      props: { viewModel: withRepeater(topologyFixtures['2/main_sub'], false, false), appearance: 'standard' },
    });
    flushSync();
    components.push(component);
    expect(target.querySelector('[data-vfo-row="repeater"]')).toBeNull();
  });

  it('a radio with no repeater group draws no strip at all', () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(VfoSurface, {
      target,
      props: { viewModel: topologyFixtures['2/main_sub'], appearance: 'standard' },
    });
    flushSync();
    components.push(component);
    expect(target.querySelector('[data-vfo-row="repeater"]')).toBeNull();
  });
});

describe('the strip renders the deck vocabulary (MOR-2111)', () => {
  it('renders the OFF/TONE/TSQL tone selector, the shift selector and the value', () => {
    const t = mountPanel({ repeater: strip({ toneMode: 'tone', shift: 'plus' }) });
    const tone = Array.from(t.querySelectorAll('[data-repeater-tone]'));
    expect(tone.map((el) => el.textContent?.trim())).toEqual(['OFF', 'TONE', 'TSQL']);
    expect(tone.find((el) => el.getAttribute('data-repeater-tone') === 'tone')?.getAttribute('data-lit')).toBe('true');
    expect(tone.find((el) => el.getAttribute('data-repeater-tone') === 'off')?.getAttribute('data-lit')).toBe('false');
    const shift = Array.from(t.querySelectorAll('[data-repeater-shift]'));
    expect(shift.map((el) => el.textContent?.trim())).toEqual(['SIMP', '−', '+']);
    expect(shift.find((el) => el.getAttribute('data-repeater-shift') === 'plus')?.getAttribute('data-lit')).toBe('true');
    expect(t.querySelector('.tone-value-text')?.textContent).toBe('88.5');
  });

  it('an unread strip keeps every chip in place, unlit, with no placeholder text', () => {
    const t = mountPanel({ repeater: strip({ toneMode: null, shift: null, toneFreqText: null }) });
    const tone = Array.from(t.querySelectorAll('[data-repeater-tone]'));
    expect(tone.map((el) => el.textContent?.trim())).toEqual(['OFF', 'TONE', 'TSQL']);
    expect(tone.every((el) => el.getAttribute('data-lit') === 'false')).toBe(true);
    expect(tone.every((el) => el.getAttribute('aria-pressed') === null)).toBe(true);
    expect(t.querySelector('.tone-value-text')?.textContent).toBe('');
    expect(t.textContent).not.toMatch(/[?—]/);
  });

  it('clicking a tone chip and a shift chip emits the matching callbacks', () => {
    const onToneModeChange = vi.fn();
    const onShiftChange = vi.fn();
    const onToneFreqStep = vi.fn();
    const t = mountPanel(
      { repeater: strip({ toneMode: 'off', shift: 'simplex' }) },
      { onToneModeChange, onShiftChange, onToneFreqStep },
    );
    t.querySelector<HTMLButtonElement>('[data-repeater-tone="tsql"]')?.click();
    expect(onToneModeChange).toHaveBeenCalledWith('tsql');
    t.querySelector<HTMLButtonElement>('[data-repeater-shift="minus"]')?.click();
    expect(onShiftChange).toHaveBeenCalledWith('minus');
    t.querySelectorAll<HTMLButtonElement>('.tone-step')[1]?.click();
    expect(onToneFreqStep).toHaveBeenCalledWith(1);
    t.querySelectorAll<HTMLButtonElement>('.tone-step')[0]?.click();
    expect(onToneFreqStep).toHaveBeenCalledWith(-1);
  });

  it('marks the pending target chip and the pending value', () => {
    const t = mountPanel({ repeater: strip({
      toneMode: 'off', pendingToneMode: 'tsql', pendingShift: 'minus', toneFreqPending: true,
    }) });
    expect(t.querySelector('[data-repeater-tone="tsql"]')?.getAttribute('data-pending-status')).toBe('pending');
    expect(t.querySelector('[data-repeater-shift="minus"]')?.getAttribute('data-pending-status')).toBe('pending');
    expect(t.querySelector('[data-repeater-tone-value]')?.getAttribute('data-pending-status')).toBe('pending');
  });
});

describe('the strip keeps the deck height fixed (source pins)', () => {
  const source = readFileSync('src/components-v2/vfo/VfoPanel.svelte', 'utf8');

  it('tone and shift chips use the under-row 22px rhythm, the value 20px, and nothing wraps', () => {
    for (const selector of ['.chip-tone, .chip-shift']) {
      expect(source).toMatch(new RegExp(`${selector.replace(/[.]/g, '\\.')}[^{]*\\{[^}]*height:\\s*22px`, 's'));
    }
    expect(source).toMatch(/\.chip-tone-value[^{]*\{[^}]*height:\s*20px/s);
    const repeaterStrip = source.match(/\.repeater-strip\s*\{([^}]*)\}/s)?.[1] ?? '';
    expect(repeaterStrip).not.toMatch(/flex-wrap/);
    expect(repeaterStrip).not.toMatch(/margin-block-(start|end)/);
  });
});
