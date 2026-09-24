/**
 * MOR-1311 — the semantic scope-controls surface (vocabulary slice 11B, the
 * scope toolbar — the LAST B-slice of the vocabulary program), retargeted by
 * MOR-2545 PR1 to the owner-approved ONE-ROW + More split.
 *
 * Every test names the carry-forward/mutation it pins:
 *   (1) renders exclusively from `view.scopeControls` — no raw-state reach.
 *   (2) `receiver` is the ONE MAIN/SUB control; no second "source" control.
 *   (3) EDGE/SPAN visibility is a RENDERING decision (`isEdgeApplicable`/
 *       `isSpanApplicable`, reused from `spectrum-toolbar-logic.ts`) layered
 *       on facts that are always structurally available; an unread `mode`
 *       hides BOTH rather than guessing CTR.
 *   handler guards — pinned independently of `disabled` (MOR-1304 F3
 *       recipe): a direct dispatched click/input on a disabled control must
 *       not reach the callback.
 *   field identity — every choice/toggle control names ITS OWN field in the
 *       callback, not a neighbour's (the copy-paste-loop mutation class).
 *   MOR-2545 owner rules — one row; no `NAME: true/false` text; no `—`/`?`
 *       placeholders; unread = drawn unlit in place with its label; MAIN/SUB
 *       absent without the structural receiver fact; More opens on the
 *       [MORE ▾] key, closes on Esc and outside click, and returns focus.
 *   MOR-2545 PR3 — the hosted look (HOSTED mounts only, gated on the
 *   host toolbar's rowTail): CTR|FIX and MAIN|SUB are ONE keyed group
 *   each; a lit segment carries `data-lit='true'`; the More key's
 *   label comes from the i18n system (pinned in ru-RU too); the rowTail
 *   renders between the receiver group and More; the overflow hooks
 *   cover quick → receiver → hold → ref → span → step → mode (CTR|FIX
 *   hides last, into More's permanent MODE row); an unhosted mount
 *   (no rowTail) keeps PR1's flat grammar. PR3 round 4 — the hosted LOOK
 *   is the Standard-face raised-key family, and the CSS-mechanism pins at
 *   the end of this file fail if the row falls back to the retired
 *   capsule chrome.
 *
 * The More panel is opened through the [MORE ▾] key exactly as the operator
 * opens it — `openMore()` below is the ONLY helper that reaches into
 * More-hosted controls. Literal text assertions everywhere, never `t(key)`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { flushSync, mount, unmount, createRawSnippet } from 'svelte';
import { getLocale, setLocale } from '$lib/i18n';
import ScopeControlsSurface, {
  CHOICES, TOGGLES, type ScopeChoiceField, type ScopeToggleField,
} from '../ScopeControlsSurface.svelte';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import {
  createFiniteRendererContext, type FiniteControlAppearance,
} from '../../primitives/control-instruments/control-instrument-renderer.svelte';
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
afterEach(() => { resetRetainedInvocations(); vi.restoreAllMocks(); target.remove(); });

type Handlers = {
  onToggleChange?: (field: ScopeToggleField, next: boolean) => void;
  onChoiceChange?: (field: ScopeChoiceField, value: number) => void;
  onSpanChange?: (span: number) => void;
  onSpeedChange?: (speed: number) => void;
  onRefChange?: (ref: number) => void;
};

function render(view: RadioViewModel, handlers: Handlers = {}, props: { rowTail?: boolean } = {}) {
  // MOR-2545 PR3: a tail probe standing in for the toolbar's rowTail.
  // createRawSnippet must return ONE element, hence the wrapper div.
  const rowTail = props.rowTail
    ? createRawSnippet(() => ({
        render: () =>
          '<div data-testid="row-tail"><span data-overflow="step" data-testid="row-tail-step"></span>'
          + '<button data-overflow="quick" data-testid="row-tail-quick">AVG</button></div>',
      }))
    : undefined;
  const component = mount(ScopeControlsSurface, { target, props: { view, ...handlers, rowTail } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  const el = (id: string) => q<HTMLElement>(`[data-testid="${id}"]`);
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="scope-controls-surface"]'),
    el,
    /** Opens the More panel through the [MORE ▾] key, like the operator does. */
    openMore: () => { el('scope-more')!.click(); flushSync(); },
  };
}
/** MOR-1304 F3 recipe: bypasses jsdom's disabled-button `.click()` no-op. */
const bypassClick = (el: HTMLElement) => el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
const pressEscape = () => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); flushSync(); };

describe('structural presence: absent group / absent leaves render nothing extra', () => {
  it('renders nothing when scopeControls is absent', () => {
    const r = render(topologyFixtures['1/single']);
    expect(r.root()).toBeNull();
    r.dispose();
  });

  it('hides a choice leaf whose structural availability is false — in the row AND in More', () => {
    const r = render(withSc({ mode: unread(OFF) }));
    expect(r.el('scope-mode-row')).toBeNull();
    r.openMore();
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
  it('shows EDGE in More and hides SPAN when mode is known FIX (1)', () => {
    const r = render(withSc({ mode: known(1) }));
    expect(r.el('scope-span')).toBeNull();
    r.openMore();
    expect(r.el('scope-edge')).not.toBeNull();
    r.dispose();
  });

  it('shows SPAN in the row and hides EDGE when mode is known CTR (0)', () => {
    const r = render(withSc({ mode: known(0) }));
    expect(r.el('scope-span')).not.toBeNull();
    r.openMore();
    expect(r.el('scope-edge')).toBeNull();
    r.dispose();
  });

  // Carry-forward (3), the exact wording: an unobserved mode hides BOTH
  // rather than rendering either with a fabricated guess.
  it('hides BOTH EDGE and SPAN when mode is unread, never fabricating CTR', () => {
    const r = render(withSc({ mode: unread() }));
    expect(r.el('scope-span')).toBeNull();
    r.openMore();
    expect(r.el('scope-edge')).toBeNull();
    r.dispose();
  });

  // F2 — mode S-F (3) shows EDGE and hides SPAN
  it('shows EDGE in More and hides SPAN when mode is known S-F (3)', () => {
    const r = render(withSc({ mode: known(3) }));
    expect(r.el('scope-span')).toBeNull();
    r.openMore();
    expect(r.el('scope-edge')).not.toBeNull();
    r.dispose();
  });

  // F2 — mode S-C (2) shows SPAN and hides EDGE
  it('shows SPAN in the row and hides EDGE when mode is known S-C (2)', () => {
    const r = render(withSc({ mode: known(2) }));
    expect(r.el('scope-span')).not.toBeNull();
    r.openMore();
    expect(r.el('scope-edge')).toBeNull();
    r.dispose();
  });

  // MOR-2545: in S-C/S-F NEITHER row mode key lights, and the More panel's
  // full mode choice is what shows the current mode.
  it('lights neither row key in S-C; the More mode choice shows the current mode', () => {
    const r = render(withSc({ mode: known(2) }));
    expect(r.el('scope-mode-row-0')!.getAttribute('aria-checked')).toBe('false');
    expect(r.el('scope-mode-row-1')!.getAttribute('aria-checked')).toBe('false');
    expect(r.el('scope-mode-row-0')!.getAttribute('data-lit')).toBe('false');
    expect(r.el('scope-mode-row-1')!.getAttribute('data-lit')).toBe('false');
    r.openMore();
    expect(r.el('scope-mode-2')!.getAttribute('aria-checked')).toBe('true');
    expect(r.el('scope-mode-2')!.getAttribute('data-lit')).toBe('true');
    r.dispose();
  });
});

describe('unread leaves render honestly, never fabricated (MOR-2545 owner rules)', () => {
  // `centerType` has no mode-applicability gate (unlike `mode` itself), so
  // it isolates the choice-leaf honesty story from carry-forward 3 above.
  it('an unread choice leaf shows no aria-checked="true" option and stays disabled', () => {
    const r = render(withSc({ centerType: unread() }));
    r.openMore();
    expect(r.el('scope-centerType-0')!.getAttribute('aria-checked')).toBe('false');
    expect(r.el('scope-centerType-0')!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('an unread toggle OMITS aria-pressed entirely and is drawn UNLIT with its label, no value', () => {
    const r = render(withSc({ hold: unread() }));
    const hold = r.el('scope-hold')!;
    expect(hold.hasAttribute('aria-pressed')).toBe(false);
    expect(hold.getAttribute('data-lit')).toBe('false');
    expect(hold.textContent).toBe('HOLD');
    r.dispose();
  });

  it('an unread stepper value is EMPTY with its reserved-width box, never a placeholder', () => {
    const r = render(withSc({ refDb: unread() }));
    const value = r.el('scope-ref-value')!;
    expect(value.textContent).toBe('');
    expect(value.classList.contains('scope-step-value')).toBe(true);
    r.dispose();
  });

  it('never prints a placeholder or a NAME: true/false text anywhere in the surface', () => {
    const r = render(base());
    r.openMore();
    const text = r.root()!.textContent ?? '';
    expect(text).not.toMatch(/:\s*(true|false)/i);
    expect(text).not.toContain('—');
    expect(text).not.toContain('?');
    r.dispose();
  });

  // Same honesty bar in CTR with EVERY value unread: steppers EMPTY, toggles label-only.
  it('in CTR with every value unread prints no placeholder and no true/false text', () => {
    const r = render(withSc({
      mode: known(0), span: unread(), refDb: unread(), speed: unread(),
      hold: unread(), dual: unread(), duringTx: unread(), vbwNarrow: unread(),
      edge: unread(), centerType: unread(), rbw: unread(), receiver: unread(),
    }));
    r.openMore();
    const text = r.root()!.textContent ?? '';
    expect(text).not.toMatch(/[—?–]|UNKNOWN|:\s*(true|false)/i);
    r.dispose();
  });

  it('does not coerce an observed out-of-list choice into a selected option', () => {
    const r = render(withSc({ centerType: known(99) }));
    r.openMore();
    for (const value of [0, 1, 2]) expect(r.el(`scope-centerType-${value}`)!.getAttribute('aria-checked')).toBe('false');
    r.dispose();
  });
});

describe('the ONE always-visible row (MOR-2545)', () => {
  it('every always-visible key sits in one row element', () => {
    const r = render(withSc({ mode: known(0) }));
    const row = r.el('scope-controls-row')!;
    expect(row).not.toBeNull();
    for (const id of ['scope-mode-row', 'scope-span', 'scope-ref', 'scope-hold', 'scope-receiver', 'scope-more']) {
      const node = r.el(id);
      expect(node, id).not.toBeNull();
      expect(node!.closest('[data-testid="scope-controls-row"]'), id).toBe(row);
    }
    r.dispose();
  });

  it('omits MAIN/SUB without the structural receiver fact (single-receiver radios)', () => {
    const r = render(withSc({ receiver: unread(OFF) }));
    expect(r.el('scope-receiver')).toBeNull();
    r.openMore();
    expect(r.el('scope-receiver')).toBeNull();
    expect(r.el('scope-more-panel')).not.toBeNull();
    r.dispose();
  });

  it('the row mode keys dispatch the mode choice from the row itself', () => {
    const onChoiceChange = vi.fn();
    const r = render(base(), { onChoiceChange });
    r.el('scope-mode-row-0')!.click();
    flushSync();
    expect(onChoiceChange).toHaveBeenCalledExactlyOnceWith('mode', 0);
    r.dispose();
  });
});

describe('the hosted row look (MOR-2545 PR3 round 4, owner style B — HOSTED mounts only)', () => {
  // PR3 round 2 (finding 3): keyed-group markup is gated on the host's
  // rowTail. Every assertion below mounts with a rowTail; the flat-grammar
  // test after this describe pins the unhosted mounts.
  it('CTR|FIX is ONE keyed group: both keys are its only children', () => {
    const r = render(withSc({ mode: known(0) }), {}, { rowTail: true });
    const modeRow = r.el('scope-mode-row')!;
    expect(modeRow.classList.contains('scope-capsule')).toBe(true);
    expect(modeRow.getAttribute('role')).toBe('radiogroup');
    const keys = modeRow.querySelectorAll('button');
    expect(keys).toHaveLength(2);
    expect(keys[0]!.textContent).toBe('CTR');
    expect(keys[1]!.textContent).toBe('FIX');
    // Round 4: the group is a bare flex box of SEPARATE family keys — no
    // shared frame, no inner divider (the segment rules are gone from the
    // sheet; pinned by the CSS pins at the end of this describe). The
    // structure the family keys on (adjacent segment children of one
    // group) is pinned here.
    expect(keys[1]!.previousElementSibling).toBe(keys[0]);
    r.dispose();
  });

  it('MAIN|SUB is ONE keyed group with exactly the two receiver keys', () => {
    const r = render(base(), {}, { rowTail: true });
    const receiver = r.el('scope-receiver')!;
    expect(receiver.classList.contains('scope-capsule')).toBe(true);
    const keys = receiver.querySelectorAll('button');
    expect(keys).toHaveLength(2);
    expect(keys[0]!.textContent).toBe('MAIN');
    expect(keys[1]!.textContent).toBe('SUB');
    r.dispose();
  });

  it('a lit segment carries data-lit=true; an unlit one does not', () => {
    const r = render(withSc({ mode: known(0), receiver: known(1) }));
    expect(r.el('scope-mode-row-0')!.getAttribute('data-lit')).toBe('true');
    expect(r.el('scope-mode-row-1')!.getAttribute('data-lit')).toBe('false');
    expect(r.el('scope-receiver-1')!.getAttribute('data-lit')).toBe('true');
    expect(r.el('scope-receiver-0')!.getAttribute('data-lit')).toBe('false');
    r.dispose();
  });

  it('[MORE ▾] exists with the i18n label, a title, and opens the More panel', () => {
    const r = render(base(), {}, { rowTail: true });
    const more = r.el('scope-more')!;
    expect(more.textContent).toBe('More ▾'); // en catalog: core.spectrum.more
    expect(more.getAttribute('title')).toBe('More ▾');
    expect(more.getAttribute('aria-label')).toBe('More scope controls');
    expect(r.el('scope-more-panel')).toBeNull();
    r.openMore();
    expect(r.el('scope-more-panel')).not.toBeNull();
    expect(more.getAttribute('aria-expanded')).toBe('true');
    r.dispose();
  });

  // PR3 round 2 (finding 8): the label must go through t() — a hard-coded
  // 'More ▾' stays green elsewhere (English-only renders). ru catalog:
  // core.spectrum.more = "Ещё" (CSS uppercases it visually).
  it('the hosted MORE label follows the locale: ru-RU renders Ещё, not More', () => {
    const previous = getLocale();
    setLocale('ru-RU');
    try {
      const r = render(base(), {}, { rowTail: true });
      expect(r.el('scope-more')!.textContent).toBe('Ещё ▾');
      r.dispose();
    } finally {
      setLocale(previous);
    }
  });

  // PR3 round 2 (finding 3): without a host rowTail (LCD skins, mobile,
  // bare mounts) the surface renders PR1's flat grammar exactly as before.
  it('an unhosted mount keeps the PR1 flat grammar: key groups, reserved widths, ⋯ key', () => {
    const r = render(withSc({ mode: known(0) }));
    expect(r.el('scope-mode-row')!.classList.contains('scope-capsule')).toBe(false);
    expect(r.el('scope-mode-row')!.classList.contains('scope-key-group')).toBe(true);
    expect(r.el('scope-mode-row-0')!.getAttribute('style')).toBe('--scope-key-width: 42px;');
    expect(r.el('scope-receiver')!.classList.contains('scope-key-group')).toBe(true);
    expect(r.el('scope-receiver-0')!.getAttribute('style')).toBe('--scope-key-width: 48px;');
    expect(r.el('scope-hold')!.getAttribute('style')).toBe('--scope-key-width: 52px;');
    const more = r.el('scope-more')!;
    expect(more.textContent).toBe('⋯');
    expect(more.getAttribute('style')).toBe('--scope-key-width: 30px;');
    expect(more.getAttribute('title')).toBeNull();
    r.dispose();
  });

  it('the host rowTail renders between the receiver capsule and More', () => {
    const r = render(withSc({ mode: known(0) }), {}, { rowTail: true });
    const order = [r.el('scope-receiver')!, r.el('row-tail-step')!, r.el('row-tail-quick')!, r.el('scope-more')!];
    for (let i = 1; i < order.length; i++) {
      expect(order[i]!.compareDocumentPosition(order[i - 1]!) & Node.DOCUMENT_POSITION_PRECEDING,
        `item ${i} follows item ${i - 1}`).toBeTruthy();
    }
    r.dispose();
  });

  it('renders the host rowTail bare when the scope fact group is absent', () => {
    // topologyFixtures['1/single'] carries no scopeControls — exactly the
    // absent-group case the bare-tail fallback exists for.
    const r = render(topologyFixtures['1/single'], {}, { rowTail: true });
    expect(r.el('scope-hold')).toBeNull();
    expect(r.el('row-tail-step')).not.toBeNull();
    expect(r.el('row-tail-quick')).not.toBeNull();
    r.dispose();
  });

  // MOR-2545 round 4 (owner style B) + round 5 (verifier defect): the
  // surface's own CSS-mechanism pins. jsdom cannot compute the cascade, so
  // these read the sheets directly and FAIL IF THE ROW FALLS BACK TO THE
  // RETIRED CAPSULE LOOK (the teal lit wash or the segment frame/divider
  // rules returning, or a colour literal re-entering the row sheet) OR IF
  // THE FAMILY LEAKS PAST THE HOSTED ROW: the flat-key exclusion is
  // hosted-conditional, so the unhosted mounts — LCD, mobile, and the
  // desktop standalone audio_fft surfaces this component renders without a
  // host toolbar — keep the flat grammar, pixel-identical to origin/main.
  it('the family reaches only hosted keys; unhosted mounts keep the flat look', () => {
    const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '');
    const HOSTED_ONLY = '.scope-flat-key:not(.spectrum-toolbar.hosted *)';
    const bezel = strip(readFileSync('src/components-v2/controls/control-button.css', 'utf8'));
    const skin = strip(readFileSync('src/skins/desktop-v2/semantic-controls.css', 'utf8'));
    expect(bezel.split(HOSTED_ONLY).length - 1).toBe(13);
    expect(skin.split(HOSTED_ONLY).length - 1).toBe(3);
    // Fails if the exclusion is dropped globally again (round-4 defect:
    // the audio_fft standalone surfaces got the raised keys).
    expect(bezel.split(HOSTED_ONLY).join('')).not.toContain('.scope-flat-key');
    expect(skin.split(HOSTED_ONLY).join('')).not.toContain('.scope-flat-key');
    const cssRaw = readFileSync('src/components/spectrum/scope-capsule.css', 'utf8');
    const css = cssRaw.replace(/\/\*[\s\S]*?\*\//g, '');
    expect(css).not.toContain('--v2-accent-cyan-teal');
    expect(css).not.toContain('.scope-capsule > .scope-flat-key');
    expect(css.match(/#[0-9a-fA-F]{3,8}\b/)).toBeNull();
    // Geometry only: the sheet must not re-style a key's font weight or
    // transform (the family owns type as of round 4).
    const keyRule = css.match(/\.spectrum-toolbar\.hosted \.scope-flat-key \{([\s\S]*?)\n\}/);
    expect(keyRule, 'the key geometry rule exists').not.toBeNull();
    expect(keyRule![1]).not.toContain('font-');
    expect(keyRule![1]).not.toContain('color:');
  });
});

describe('narrow widths: lower-priority keys overflow into More (MOR-2545)', () => {
  // jsdom cannot lay out — this pins the STRUCTURE/hooks the container queries key on.
  it('row hooks mark the overflow groups and More renders their copies', () => {
    const r = render(withSc({ mode: known(0) }));
    const row = r.el('scope-controls-row')!;
    // Display order; hide-first is the reverse. CTR|FIX (mode) is hooked too
    // (the hosted sheet's last band retires it into More's permanent MODE
    // row); only the More anchor stays unhooked (filtered out).
    const hooked = [...row.children].map((c) => c.getAttribute('data-overflow')).filter(Boolean);
    expect(hooked).toEqual(['mode', 'span', 'ref', 'hold', 'receiver']);
    r.openMore();
    const overflow = r.el('scope-more-overflow')!;
    for (const hook of ['receiver', 'hold', 'ref', 'span']) expect(overflow.querySelector(`[data-overflow="${hook}"]`), hook).not.toBeNull();
    r.dispose();
  });

  // MOR-2545 PR3: with the host's rowTail present, the hooked order is the
  // FULL hide order — the quick keys (AVG/PEAK) hide FIRST, CTR|FIX (mode)
  // hides LAST (round 3). DOM order over the whole row (the More panel is
  // closed, so no overflow copies exist yet); the real hide rules key on
  // direct children, but the probe wraps its two hooks in one div.
  it('the full hide order with the rowTail present: quick → receiver → hold → ref → span → step → mode', () => {
    const r = render(withSc({ mode: known(0) }), {}, { rowTail: true });
    const row = r.el('scope-controls-row')!;
    const hooked = [...row.querySelectorAll('[data-overflow]')].map((c) => c.getAttribute('data-overflow'));
    expect(hooked).toEqual(['mode', 'span', 'ref', 'hold', 'receiver', 'step', 'quick']);
    r.dispose();
  });
});

describe('the More panel ([MORE ▾])', () => {
  it('opens on MORE, lights the key, and moves focus into the panel', () => {
    const r = render(base());
    expect(r.el('scope-more-panel')).toBeNull();
    r.openMore();
    expect(r.el('scope-more-panel')).not.toBeNull();
    expect(r.el('scope-more')!.getAttribute('aria-expanded')).toBe('true');
    expect(document.activeElement).toBe(r.el('scope-more-panel'));
    expect(r.el('scope-more-panel')!.hasAttribute('popover')).toBe(false); // no top-layer route (round 4)
    expect(r.el('scope-more-backdrop')!.hasAttribute('popover')).toBe(false);
    r.dispose();
  });

  it('closes on a window-level Escape and returns focus to the More key', () => {
    const r = render(base());
    r.openMore();
    pressEscape();
    expect(r.el('scope-more-panel')).toBeNull();
    expect(document.activeElement).toBe(r.el('scope-more'));
    r.dispose();
  });

  it('closes on an outside click (backdrop) and returns focus to the More key', () => {
    const r = render(base());
    r.openMore();
    r.el('scope-more-backdrop')!.click();
    flushSync();
    expect(r.el('scope-more-panel')).toBeNull();
    expect(document.activeElement).toBe(r.el('scope-more'));
    r.dispose();
  });

  it("clamps the fixed panel's left 8 px inside the viewport; top is the key's bottom edge", () => {
    const realWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { value: 480, configurable: true, writable: true });
    const r = render(base());
    try {
      // jsdom lays the panel out at 0×0: leftward-from-the-key is rect.right itself.
      r.el('scope-more')!.getBoundingClientRect = () => ({ right: 700, bottom: 40 }) as DOMRect;
      r.openMore();
      expect(r.el('scope-more-panel')!.style.left).toBe('472px'); // 700 clamped to 480 − 8
      expect(r.el('scope-more-panel')!.style.top).toBe('40px'); // the key's bottom edge
    } finally {
      r.dispose(); // a failing assertion must not leave the panel mounted for the next test
      Object.defineProperty(window, 'innerWidth', { value: realWidth, configurable: true, writable: true });
    }
  });

  it('registers its window keydown/resize/scroll listeners only while open (MOR-2514 keeps its Esc)', () => {
    const add = vi.spyOn(window, 'addEventListener');
    const remove = vi.spyOn(window, 'removeEventListener');
    const count = (spy: typeof add, type: string) => spy.mock.calls.filter(([t]) => t === type).length;
    const r = render(base());
    for (const type of ['keydown', 'resize', 'scroll']) expect(count(add, type), type).toBe(0); // closed: no listeners at all — Esc stays with MOR-2514
    r.openMore();
    for (const type of ['keydown', 'resize']) expect(count(add, type), type).toBe(1);
    expect(add.mock.calls.filter(([t]) => t === 'scroll')[0]![2]).toBe(true); // capture: any scroll closes
    pressEscape();
    expect(r.el('scope-more-panel')).toBeNull();
    for (const type of ['keydown', 'resize', 'scroll']) expect(count(remove, type), type).toBe(1);
    r.dispose();
  });
});

describe('handler guards are pinned independently of `disabled` (MOR-1304 F3)', () => {
  it('refuses a choice click dispatched directly at a disabled option', () => {
    const onChoiceChange = vi.fn();
    const r = render(withSc({ centerType: unread() }), { onChoiceChange });
    r.openMore();
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

  const STALE: Availability = { structural: true, operational: false };

  it('refuses a choice click on a KNOWN but operationally-stale field', () => {
    const onChoiceChange = vi.fn();
    const r = render(withSc({ centerType: known(1, STALE) }), { onChoiceChange });
    r.openMore();
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
  CHOICES.map(([field, , , options]) => [field, options[0][0]]),
) as Record<(typeof CHOICES)[number][0], number>;

describe('field identity: every control names ITS OWN field, never a neighbour\'s', () => {
  // MUTATION KILLED: a copy-paste-loop bug that closes over the wrong `field`
  // — every CHOICES entry is driven and must report its own name.
  it.each(CHOICES.map(([field]) => field))('choice group "%s" reports its own field', (field) => {
    const onChoiceChange = vi.fn();
    const r = render(base(), { onChoiceChange });
    // receiver lives in the row; the other choice groups live in More.
    if (field !== 'receiver') r.openMore();
    r.el(`scope-${field}-${CHOICE_FIRST_VALUE[field]}`)!.click();
    flushSync();
    expect(onChoiceChange).toHaveBeenCalledExactlyOnceWith(field, CHOICE_FIRST_VALUE[field]);
    r.dispose();
  });

  it.each(TOGGLES.map(([field]) => field))('toggle "%s" reports its own field', (field) => {
    const onToggleChange = vi.fn();
    const r = render(base(), { onToggleChange });
    // hold lives in the row; the other toggles live in More.
    if (field !== 'hold') r.openMore();
    r.el(`scope-${field}`)!.click();
    flushSync();
    expect(onToggleChange).toHaveBeenCalledExactlyOnceWith(field, expect.any(Boolean));
    r.dispose();
  });

  // `mode` uses the imported MODE_BUTTONS table rather than CHOICES, so it is
  // exercised separately — the FULL choice (incl. S-C/S-F) lives in More.
  it('mode reports "mode" with the clicked key\'s own value', () => {
    const onChoiceChange = vi.fn();
    const r = render(base(), { onChoiceChange });
    r.openMore();
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
    r.openMore();
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

  // F1 — SPAN at the ceiling stays 7, never 8 (domain 0-7)
  it('SPAN at the ceiling clamped to 7, never exceeds domain', () => {
    const onSpanChange = vi.fn();
    const r = render(withSc({ mode: known(0), span: known(7) }), { onSpanChange });
    r.el('scope-span')!.querySelectorAll('button')[1]!.click();
    flushSync();
    expect(onSpanChange).toHaveBeenCalledExactlyOnceWith(7);
    r.dispose();
  });

  // F1 — REF at the ceiling stays 10 (domain -30..10)
  it('REF at the ceiling clamped to 10, never exceeds domain', () => {
    const onRefChange = vi.fn();
    const r = render(withSc({ refDb: known(10) }), { onRefChange });
    r.el('scope-ref')!.querySelectorAll('button')[1]!.click();
    flushSync();
    expect(onRefChange).toHaveBeenCalledExactlyOnceWith(10);
    r.dispose();
  });

  // F1 — SPEED at the floor stays 0 (domain 0-2, note clampSpeed's inverted delta)
  it('SPEED at the floor clamped to 0, never goes below domain', () => {
    const onSpeedChange = vi.fn();
    const r = render(withSc({ speed: known(0) }), { onSpeedChange });
    r.openMore();
    r.el('scope-speed')!.querySelectorAll('button')[1]!.click();
    flushSync();
    expect(onSpeedChange).toHaveBeenCalledExactlyOnceWith(0);
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

describe('external finite appearance', () => {
  const fixture = FiniteControlRendererFixture as FiniteControlAppearance['action'];
  const appearance = {
    action: fixture,
    toggle: FiniteControlRendererFixture as FiniteControlAppearance['toggle'],
    choice: FiniteControlRendererFixture as FiniteControlAppearance['choice'],
  } satisfies FiniteControlAppearance;

  it('uses host labels and the existing Scope action/toggle/choice intents', () => {
    const onSpanChange = vi.fn(), onToggleChange = vi.fn(), onChoiceChange = vi.fn();
    const component = mount(ScopeControlsSurface, { target, props: {
      view: withSc({ mode: known(0), span: known(3), hold: known(false), centerType: known(1) }),
      finiteAppearance: appearance, rendererContext: createFiniteRendererContext(),
      onSpanChange, onToggleChange, onChoiceChange,
    } });
    flushSync();
    (target.querySelector('[aria-label="Increase scope span"]') as HTMLButtonElement).click();
    (target.querySelector('[data-testid="external-HOLD"]') as HTMLButtonElement).click();
    (target.querySelector('[data-testid="external-Scope center type-2"]') as HTMLButtonElement).click();
    expect(onSpanChange).toHaveBeenCalledExactlyOnceWith(4);
    expect(onToggleChange).toHaveBeenCalledExactlyOnceWith('hold', true);
    expect(onChoiceChange).toHaveBeenCalledExactlyOnceWith('centerType', 2);
    unmount(component);
  });

  it('refuses a retained callback after the external renderer unmounts', () => {
    const onToggleChange = vi.fn();
    const rendererContext = createFiniteRendererContext();
    const component = mount(ScopeControlsSurface, { target, props: {
      view: withSc({ hold: known(false) }), finiteAppearance: appearance, rendererContext, onToggleChange,
    } });
    flushSync();
    const retained = retainedInvocations.get('HOLD')!;
    unmount(component);
    retained();
    expect(onToggleChange).not.toHaveBeenCalled();
  });

  it('keeps the selected external appearance inert instead of falling back to native controls', () => {
    const component = mount(ScopeControlsSurface, { target, props: {
      view: withSc({ hold: known(false) }), finiteAppearance: appearance, rendererContext: null,
    } });
    flushSync();
    expect(target.querySelector('[data-testid="scope-controls-surface"]')).not.toBeNull();
    expect(target.querySelector('[data-testid="scope-hold"]')).toBeNull();
    expect(target.querySelector('[data-testid="external-HOLD"]')).toBeNull();
    expect(retainedInvocations.size).toBe(0);
    unmount(component);
  });

  it('preserves an out-of-list canonical reading without selecting an option', () => {
    const component = mount(ScopeControlsSurface, { target, props: {
      view: withSc({ centerType: known(99) }), finiteAppearance: appearance,
      rendererContext: createFiniteRendererContext(),
    } });
    flushSync();
    const group = target.querySelector('[data-testid="external-Scope center type"]')!;
    expect(group.getAttribute('data-reading')).toBe('99');
    expect(group.querySelector('[aria-checked="true"]')).toBeNull();
    unmount(component);
  });
});
