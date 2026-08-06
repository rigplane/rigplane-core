/**
 * MOR-1311 — the semantic scope-controls surface (vocabulary slice 11B, the
 * scope toolbar — the LAST B-slice of the vocabulary program).
 *
 * Every test names the carry-forward/mutation it pins:
 *   (1) renders exclusively from `view.scopeControls` — no raw-state reach.
 *   (2) `receiver` is the ONE MAIN/SUB control; no second "source" control.
 *   (3) EDGE/SPAN visibility is a RENDERING decision (`isEdgeApplicable`/
 *       `isSpanApplicable`, reused from `spectrum-toolbar-logic.ts`) layered
 *       on facts that are always structurally available; an unread `mode`
 *       hides BOTH rows rather than guessing CTR.
 *   handler guards — pinned independently of `disabled` (MOR-1304 F3
 *       recipe): a direct dispatched click/input on a disabled control must
 *       not reach the callback.
 *   field identity — every choice/toggle control names ITS OWN field in the
 *       callback, not a neighbour's (the copy-paste-loop mutation class).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ScopeControlsSurface, {
  CHOICES, TOGGLES, UNKNOWN_TEXT, type ScopeChoiceField, type ScopeToggleField,
} from '../ScopeControlsSurface.svelte';
import { topologyFixtures, withScopeControls } from '../fixtures/topologies';
import type { Availability, RadioViewModel, ScopeControlsField, ScopeControlsViewModel } from '../radio-view-model';

const ON: Availability = { structural: true, operational: true };
const OFF: Availability = { structural: false, operational: false };
const unread = <T>(availability: Availability = ON): ScopeControlsField<T> =>
  ({ reading: { status: 'unknown' }, availability });
const known = <T>(value: T, availability: Availability = ON): ScopeControlsField<T> =>
  ({ reading: { status: 'known', value }, availability });

const base = (): RadioViewModel => withScopeControls(topologyFixtures['1/single']);
const withSc = (over: Partial<ScopeControlsViewModel>): RadioViewModel => {
  const view = base();
  return { ...view, scopeControls: { ...view.scopeControls!, ...over } };
};

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Handlers = {
  onToggleChange?: (field: ScopeToggleField, next: boolean) => void;
  onChoiceChange?: (field: ScopeChoiceField, value: number) => void;
  onSpanChange?: (span: number) => void;
  onSpeedChange?: (speed: number) => void;
  onRefChange?: (ref: number) => void;
};

function render(view: RadioViewModel, handlers: Handlers = {}) {
  const component = mount(ScopeControlsSurface, { target, props: { view, ...handlers } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="scope-controls-surface"]'),
    el: (id: string) => q<HTMLElement>(`[data-testid="${id}"]`),
  };
}
/** MOR-1304 F3 recipe: bypasses jsdom's disabled-button `.click()` no-op. */
const bypassClick = (el: HTMLElement) => el.dispatchEvent(new MouseEvent('click', { bubbles: true }));

describe('structural presence: absent group / absent leaves render nothing extra', () => {
  it('renders nothing when scopeControls is absent', () => {
    const r = render(topologyFixtures['1/single']);
    expect(r.root()).toBeNull();
    r.dispose();
  });

  it('hides a choice leaf whose structural availability is false', () => {
    const r = render(withSc({ mode: unread(OFF) }));
    expect(r.el('scope-mode')).toBeNull();
    r.dispose();
  });

  it('hides a toggle leaf whose structural availability is false', () => {
    const r = render(withSc({ hold: unread(OFF) }));
    expect(r.el('scope-hold')).toBeNull();
    r.dispose();
  });
});

describe('carry-forward (3): EDGE/SPAN visibility is a rendering decision on top of facts', () => {
  it('shows EDGE and hides SPAN when mode is known FIX (1)', () => {
    const r = render(withSc({ mode: known(1) }));
    expect(r.el('scope-edge')).not.toBeNull();
    expect(r.el('scope-span')).toBeNull();
    r.dispose();
  });

  it('shows SPAN and hides EDGE when mode is known CTR (0)', () => {
    const r = render(withSc({ mode: known(0) }));
    expect(r.el('scope-span')).not.toBeNull();
    expect(r.el('scope-edge')).toBeNull();
    r.dispose();
  });

  // Carry-forward (3), the exact wording: an unobserved mode hides BOTH
  // rows rather than rendering either with a fabricated guess.
  it('hides BOTH EDGE and SPAN when mode is unread, never fabricating CTR', () => {
    const r = render(withSc({ mode: unread() }));
    expect(r.el('scope-edge')).toBeNull();
    expect(r.el('scope-span')).toBeNull();
    r.dispose();
  });
});

describe('unread leaves render honestly, never fabricated', () => {
  // `centerType` has no mode-applicability gate (unlike `mode` itself), so
  // it isolates the choice-leaf honesty story from carry-forward 3 above.
  it('an unread choice leaf shows no aria-checked="true" option and stays disabled', () => {
    const r = render(withSc({ centerType: unread() }));
    expect(r.el('scope-centerType-0')!.getAttribute('aria-checked')).toBe('false');
    expect(r.el('scope-centerType-0')!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('an unread toggle OMITS aria-pressed entirely, never "false" (MOR-1358 class)', () => {
    const r = render(withSc({ hold: unread() }));
    expect(r.el('scope-hold')!.hasAttribute('aria-pressed')).toBe(false);
    expect(r.el('scope-hold')!.textContent).toContain(UNKNOWN_TEXT);
    r.dispose();
  });

  it('an unread stepper shows unknown text, never a v2-fabricated default', () => {
    const r = render(withSc({ refDb: unread() }));
    expect(r.el('scope-ref-value')!.textContent).toBe(UNKNOWN_TEXT);
    r.dispose();
  });
});

describe('handler guards are pinned independently of `disabled` (MOR-1304 F3)', () => {
  it('refuses a choice click dispatched directly at a disabled option', () => {
    const onChoiceChange = vi.fn();
    const r = render(withSc({ centerType: unread() }), { onChoiceChange });
    bypassClick(r.el('scope-centerType-0')!);
    flushSync();
    expect(onChoiceChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('refuses a toggle click dispatched directly at a disabled control', () => {
    const onToggleChange = vi.fn();
    const r = render(withSc({ hold: unread() }), { onToggleChange });
    bypassClick(r.el('scope-hold')!);
    flushSync();
    expect(onToggleChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('refuses a stepper click dispatched directly at a disabled control', () => {
    const onSpanChange = vi.fn();
    const r = render(withSc({ mode: known(0), span: unread() }), { onSpanChange });
    bypassClick(r.el('scope-span')!.querySelector('button')!);
    flushSync();
    expect(onSpanChange).not.toHaveBeenCalled();
    r.dispose();
  });

  /**
   * The `usable()` gate is structural && operational && known — a field can
   * be `reading.status === 'known'` (a stale, previously-observed value)
   * while `operational: false` (unwritable right now). These three pin THAT
   * half specifically: a guard that checks only `status === 'known'` (a
   * plausible partial mutation) still passes the tests above but must fail
   * these, since none of them is unread.
   */
  const STALE: Availability = { structural: true, operational: false };

  it('refuses a choice click on a KNOWN but operationally-stale field', () => {
    const onChoiceChange = vi.fn();
    const r = render(withSc({ centerType: known(1, STALE) }), { onChoiceChange });
    bypassClick(r.el('scope-centerType-1')!);
    flushSync();
    expect(onChoiceChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('refuses a toggle click on a KNOWN but operationally-stale field', () => {
    const onToggleChange = vi.fn();
    const r = render(withSc({ hold: known(true, STALE) }), { onToggleChange });
    bypassClick(r.el('scope-hold')!);
    flushSync();
    expect(onToggleChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('refuses a stepper click on a KNOWN but operationally-stale field', () => {
    const onSpanChange = vi.fn();
    const r = render(withSc({ mode: known(0), span: known(3, STALE) }), { onSpanChange });
    bypassClick(r.el('scope-span')!.querySelector('button')!);
    flushSync();
    expect(onSpanChange).not.toHaveBeenCalled();
    r.dispose();
  });
});

/** First option value per `CHOICES` field, precomputed once. */
const CHOICE_FIRST_VALUE = Object.fromEntries(
  CHOICES.map(([field, , options]) => [field, options[0][0]]),
) as Record<(typeof CHOICES)[number][0], number>;

describe('field identity: every control names ITS OWN field, never a neighbour\'s', () => {
  // MUTATION KILLED: a copy-paste-loop bug that closes over the wrong `field`
  // — every CHOICES entry is driven and must report its own name.
  it.each(CHOICES.map(([field]) => field))('choice group "%s" reports its own field', (field) => {
    const onChoiceChange = vi.fn();
    const r = render(base(), { onChoiceChange });
    r.el(`scope-${field}-${CHOICE_FIRST_VALUE[field]}`)!.click();
    flushSync();
    expect(onChoiceChange).toHaveBeenCalledExactlyOnceWith(field, CHOICE_FIRST_VALUE[field]);
    r.dispose();
  });

  it.each(TOGGLES.map(([field]) => field))('toggle "%s" reports its own field', (field) => {
    const onToggleChange = vi.fn();
    const r = render(base(), { onToggleChange });
    r.el(`scope-${field}`)!.click();
    flushSync();
    expect(onToggleChange).toHaveBeenCalledExactlyOnceWith(field, expect.any(Boolean));
    r.dispose();
  });

  // `mode` uses the imported MODE_BUTTONS table rather than CHOICES, so it is
  // exercised separately.
  it('mode reports "mode" with the clicked button\'s own value', () => {
    const onChoiceChange = vi.fn();
    const r = render(base(), { onChoiceChange });
    r.el('scope-mode-2')!.click();
    flushSync();
    expect(onChoiceChange).toHaveBeenCalledExactlyOnceWith('mode', 2);
    r.dispose();
  });
});

describe('steppers compute the next value with the shipped clamp functions', () => {
  it('SPAN increments through clampSpan, not a re-derived formula', () => {
    const onSpanChange = vi.fn();
    const r = render(withSc({ mode: known(0), span: known(3) }), { onSpanChange });
    r.el('scope-span')!.querySelectorAll('button')[1]!.click();
    flushSync();
    expect(onSpanChange).toHaveBeenCalledExactlyOnceWith(4);
    r.dispose();
  });

  it('SPEED decrements through clampSpeed (inverted delta, per the shipped function)', () => {
    const onSpeedChange = vi.fn();
    const r = render(withSc({ speed: known(1) }), { onSpeedChange });
    r.el('scope-speed')!.querySelectorAll('button')[0]!.click();
    flushSync();
    expect(onSpeedChange).toHaveBeenCalledExactlyOnceWith(2);
    r.dispose();
  });

  it('REF steps by 5 through clampRef', () => {
    const onRefChange = vi.fn();
    const r = render(withSc({ refDb: known(0) }), { onRefChange });
    r.el('scope-ref')!.querySelectorAll('button')[1]!.click();
    flushSync();
    expect(onRefChange).toHaveBeenCalledExactlyOnceWith(5);
    r.dispose();
  });
});

describe('carry-forward (2): receiver is the ONE MAIN/SUB control', () => {
  it('renders exactly one scope-receiver control and no separate "source" control', () => {
    const r = render(base());
    expect(r.el('scope-receiver')).not.toBeNull();
    expect(target.querySelectorAll('[data-testid^="scope-source"]').length).toBe(0);
    r.dispose();
  });

  it('reports the receiver choice through the same onChoiceChange path as any other choice', () => {
    const onChoiceChange = vi.fn();
    const r = render(withSc({ receiver: known(0) }), { onChoiceChange });
    r.el('scope-receiver-1')!.click();
    flushSync();
    expect(onChoiceChange).toHaveBeenCalledExactlyOnceWith('receiver', 1);
    r.dispose();
  });
});
