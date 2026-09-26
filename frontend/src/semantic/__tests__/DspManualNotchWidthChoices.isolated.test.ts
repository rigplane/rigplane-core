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
  pendingNotchWidth?: number | null;
};

function render(view: RadioViewModel, props: Props = {}) {
  const onLevelChange = vi.fn(props.onLevelChange ?? (() => {}));
  const component = mount(DspScalarHostFixture, {
    target,
    props: proxy({
      view, presentation: 'grouped', onLevelChange,
      notchWidthChoices: props.notchWidthChoices,
      pendingNotchWidth: props.pendingNotchWidth ?? null,
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
    // No slider for the width: neither inside the choice group nor as a
    // stray sibling row on the surface (a `dsp-manualNotchWidth` range
    // input anywhere fails this).
    expect(group!.querySelector('input[type="range"]')).toBeNull();
    expect(target.querySelectorAll('[data-testid="dsp-manualNotchWidth"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="dsp-manualNotchWidth"] input[type="range"]')).toHaveLength(0);
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
    const checked = (label: string) => [...r.width()!.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === label)!
      .getAttribute('aria-checked');
    expect(checked('NAR')).toBe('true');
    expect(checked('WIDE')).toBe('false');
    expect(checked('MID')).toBe('false');
    r.dispose();
  });

  it('marks the requested choice pending while keeping aria-checked on CONFIRMED', () => {
    const r = render(withWidthReading(base(), 0), {
      notchWidthChoices: [...IC7300_WIDTH_CHOICES], pendingNotchWidth: 1,
    });
    const group = r.width()!;
    const choice = (label: string) => [...group.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === label)!;
    expect(group.dataset.notchWidthStatus).toBe('pending');
    expect(choice('MID').dataset.pending).toBe('true');
    expect(choice('WIDE').dataset.pending).toBe('false');
    expect(choice('NAR').dataset.pending).toBe('false');
    expect(choice('WIDE').getAttribute('aria-checked')).toBe('true');
    expect(choice('MID').getAttribute('aria-checked')).toBe('false');
    expect(group.querySelector(`#${group.getAttribute('aria-describedby')}`)?.textContent)
      .toBe('Pending, not yet confirmed');
    r.dispose();
  });

  it('renders confirmed status and no announcement when nothing is pending', () => {
    const r = render(withWidthReading(base(), 0), { notchWidthChoices: [...IC7300_WIDTH_CHOICES] });
    expect(r.width()!.dataset.notchWidthStatus).toBe('confirmed');
    expect(r.width()!.querySelector('.sr-only')).toBeNull();
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
