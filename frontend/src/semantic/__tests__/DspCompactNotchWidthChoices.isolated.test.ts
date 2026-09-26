/**
 * MOR-1685 — the COMPACT path: `DspScalarHost`'s own `manualNotchWidth`
 * choice branch (reached through the scalar handle, the same call the
 * surface's compact settings "notch" panel makes), not `DspSurface`'s
 * native row. Mounted through `DspScalarHostFixture` with the
 * `compact-notch` presentation; scoped `data-testid` selectors only.
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
  { value: 0, label: 'A' }, { value: 5, label: 'B' }, { value: 9, label: 'C' },
] as const;

const base = (): RadioViewModel => withDsp(topologyFixtures['1/single']);

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

function render(
  view: RadioViewModel,
  notchWidthChoices?: readonly { value: number; label: string }[],
  pendingNotchWidth: number | null = null,
) {
  const onLevelChange = vi.fn((_field: DspLevelField, _value: number) => {});
  const component = mount(DspScalarHostFixture, {
    target,
    props: proxy({ view, presentation: 'compact-notch', onLevelChange, notchWidthChoices, pendingNotchWidth }),
  });
  flushSync();
  const section = () => target.querySelector<HTMLElement>('[data-testid="compact-notch-width"]');
  const group = () => section()?.querySelector<HTMLElement>('[data-testid="dsp-manualNotchWidth"]') ?? null;
  return { onLevelChange, section, width: group, dispose: () => unmount(component) };
}

describe('compact manual notch width renders profile-derived choices (MOR-1685)', () => {
  it('renders exactly the fixture labels with no range input', () => {
    const r = render(base(), [...IC7300_WIDTH_CHOICES]);
    const group = r.width();
    expect(group).not.toBeNull();
    expect(group!.querySelector('input[type="range"]')).toBeNull();
    expect(sectionHasNoRange(r)).toBe(true);
    const buttons = [...group!.querySelectorAll('button')];
    expect(buttons.map((button) => button.textContent?.trim())).toEqual(['WIDE', 'MID', 'NAR']);
    r.dispose();
  });

  it('one deliberate choice dispatches exactly one onLevelChange with the raw value', () => {
    const r = render(base(), [...IC7300_WIDTH_CHOICES]);
    const nar = [...r.width()!.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === 'NAR')!;
    nar.click();
    flushSync();
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('manualNotchWidth', 2);
    r.dispose();
  });

  it('renders a non-IC-7300 domain verbatim and dispatches its declared raw value', () => {
    // A literal `["WIDE","MID","NAR"][i]` hard-code in the host passes the
    // IC-7300 cases above but fails here: labels must come from the list.
    const r = render(base(), [...OTHER_WIDTH_CHOICES]);
    const group = r.width();
    const buttons = [...group!.querySelectorAll('button')];
    expect(buttons.map((button) => button.textContent?.trim())).toEqual(['A', 'B', 'C']);
    expect(group!.textContent).not.toMatch(/WIDE|MID|NAR/);
    const b = buttons.find((button) => button.textContent?.trim() === 'B')!;
    b.click();
    flushSync();
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('manualNotchWidth', 5);
    r.dispose();
  });

  it('the confirmed reading selects the matching choice', () => {
    const view = {
      ...base(),
      dsp: {
        ...base().dsp!,
        manualNotchWidth: {
          ...base().dsp!.manualNotchWidth,
          reading: { status: 'known' as const, value: 1 },
        },
      },
    };
    const r = render(view, [...IC7300_WIDTH_CHOICES]);
    const checked = (label: string) => [...r.width()!.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === label)!
      .getAttribute('aria-checked');
    expect(checked('MID')).toBe('true');
    expect(checked('WIDE')).toBe('false');
    expect(checked('NAR')).toBe('false');
    r.dispose();
  });

  it('marks the requested choice pending while keeping aria-checked on CONFIRMED', () => {
    const view = {
      ...base(),
      dsp: {
        ...base().dsp!,
        manualNotchWidth: {
          ...base().dsp!.manualNotchWidth,
          reading: { status: 'known' as const, value: 0 },
        },
      },
    };
    const r = render(view, [...IC7300_WIDTH_CHOICES], 2);
    const group = r.width()!;
    const choice = (label: string) => [...group.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === label)!;
    expect(group.dataset.notchWidthStatus).toBe('pending');
    expect(choice('NAR').dataset.pending).toBe('true');
    expect(choice('WIDE').dataset.pending).toBe('false');
    expect(choice('MID').dataset.pending).toBe('false');
    expect(choice('WIDE').getAttribute('aria-checked')).toBe('true');
    expect(choice('NAR').getAttribute('aria-checked')).toBe('false');
    expect(group.querySelector(`#${group.getAttribute('aria-describedby')}`)?.textContent)
      .toBe('Pending, not yet confirmed');
    r.dispose();
  });

  it('renders confirmed status and no announcement when nothing is pending', () => {
    const r = render(base(), [...IC7300_WIDTH_CHOICES]);
    expect(r.width()!.dataset.notchWidthStatus).toBe('confirmed');
    expect(r.width()!.getAttribute('aria-describedby')).toBeNull();
    r.dispose();
  });

  it('renders the existing scalar host row when choices are absent or empty', () => {
    for (const notchWidthChoices of [undefined, []] as const) {
      const r = render(base(), notchWidthChoices);
      expect(r.section()).not.toBeNull();
      // The scalar host's own row (data-scalar-field), not the choice
      // group: no choice buttons at all. `ValueControl` renders through
      // its appearance renderer, never a bare range input, so the
      // absence of buttons is the fallback pin here.
      expect(r.width()).not.toBeNull();
      expect(r.width()!.querySelectorAll('button')).toHaveLength(0);
      r.dispose();
      target.innerHTML = '';
    }
  });
});

function sectionHasNoRange(r: ReturnType<typeof render>): boolean {
  return r.section()?.querySelector('input[type="range"]') == null;
}
