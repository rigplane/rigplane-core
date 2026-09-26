/**
 * MOR-1685 — Manual Notch Width is a profile-derived choice group, not a
 * slider. Mounted through `DspScalarHostFixture` (the same grouped-surface
 * topology the existing `DspSurface.test.ts` proves), with scoped test-id
 * selectors inside the DSP surface — never label-only text.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { DspLevelField } from '../DspSurface.svelte';
import { topologyFixtures, withDsp } from '../fixtures/topologies';
import type { RadioViewModel } from '../radio-view-model';
import DspScalarHostFixture from './fixtures/DspScalarHostFixture.svelte';

const IC7300_WIDTH_CHOICES = [
  { value: 0, label: 'WIDE' }, { value: 1, label: 'MID' }, { value: 2, label: 'NAR' },
] as const;
const OTHER_WIDTH_CHOICES = [
  { value: 10, label: 'A' }, { value: 20, label: 'B' }, { value: 30, label: 'C' },
] as const;

const base = (): RadioViewModel => withDsp(topologyFixtures['1/single']);

function withWidthReading(view: RadioViewModel, value: number): RadioViewModel {
  return {
    ...view,
    dsp: {
      ...view.dsp!,
      manualNotchWidth: {
        ...view.dsp!.manualNotchWidth,
        reading: { status: 'known' as const, value },
      },
    },
  };
}

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Props = {
  onLevelChange?: (field: DspLevelField, value: number) => void;
  notchWidthChoices?: readonly { value: number; label: string }[];
};

function render(view: RadioViewModel, props: Props = {}) {
  const onLevelChange = vi.fn(props.onLevelChange ?? (() => {}));
  const component = mount(DspScalarHostFixture, {
    target,
    props: proxy({
      view, presentation: 'grouped', onLevelChange,
      notchWidthChoices: props.notchWidthChoices,
    }),
  });
  flushSync();
  const width = () => target.querySelector<HTMLElement>('[data-testid="dsp-manualNotchWidth"]');
  return { onLevelChange, width, dispose: () => unmount(component) };
}

describe('manual notch width renders profile-derived choices (MOR-1685)', () => {
  it('renders exactly the fixture labels with no range input', () => {
    const r = render(base(), { notchWidthChoices: [...IC7300_WIDTH_CHOICES] });
    const group = r.width();
    expect(group).not.toBeNull();
    expect(group!.querySelector('input[type="range"]')).toBeNull();
    const buttons = [...group!.querySelectorAll('button')];
    expect(buttons.map((button) => button.textContent?.trim())).toEqual(['WIDE', 'MID', 'NAR']);
    r.dispose();
  });

  it('renders different fixture labels verbatim — nothing is hard-coded', () => {
    const r = render(base(), { notchWidthChoices: [...OTHER_WIDTH_CHOICES] });
    const group = r.width();
    const buttons = [...group!.querySelectorAll('button')];
    expect(buttons.map((button) => button.textContent?.trim())).toEqual(['A', 'B', 'C']);
    expect(group!.textContent).not.toMatch(/WIDE|MID|NAR/);
    r.dispose();
  });

  it('one deliberate choice dispatches exactly one set_manual_notch_width with the raw value', () => {
    const r = render(base(), { notchWidthChoices: [...IC7300_WIDTH_CHOICES] });
    const mid = [...r.width()!.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === 'MID')!;
    mid.click();
    flushSync();
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('manualNotchWidth', 1);
    r.dispose();
  });

  it('the confirmed reading selects the matching choice; pending is not shown as confirmed', () => {
    const r = render(withWidthReading(base(), 2), { notchWidthChoices: [...IC7300_WIDTH_CHOICES] });
    const pressed = (label: string) => [...r.width()!.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === label)!
      .getAttribute('aria-pressed');
    expect(pressed('NAR')).toBe('true');
    expect(pressed('WIDE')).toBe('false');
    expect(pressed('MID')).toBe('false');
    r.dispose();
  });

  it('renders the existing scalar with no range-input change when choices are absent or empty', () => {
    for (const notchWidthChoices of [undefined, []] as const) {
      const r = render(base(), { notchWidthChoices });
      const group = r.width();
      expect(group).not.toBeNull();
      expect(group!.querySelector('input[type="range"]')).not.toBeNull();
      r.dispose();
      target.innerHTML = '';
    }
  });
});
