/**
 * MOR-1305 — the semantic DSP surface (vocabulary slice 5B).
 *
 * Carry-forward pins from the MOR-1290 fact-layer decisions this surface must
 * not relax (see `DspSurface.svelte`'s header for the full statement):
 *  (1) `agcLabels`/`nbLevelMax`/`nbLevelPercent` arrive as plain props, never
 *      read off a capabilities import inside this file — block 5.
 *  (2) a structurally-present, never-observed `agcTimeConstant` renders like
 *      any other unobserved present field — no special-casing — block 2.
 *  (3) every reading renders exactly as the fact group states it — no local
 *      rescale of `nrLevel`/`nbDepth` — block 6.
 *  (4) `unknown` renders as `?`, never a v2 fabricated default — block 2.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import DspSurface, { DSP_LEVELS, DSP_TOGGLES, type DspLevelField, type DspToggleField } from '../DspSurface.svelte';
import { topologyFixtures, withDsp } from '../fixtures/topologies';
import type { Availability, DspViewModel, RadioViewModel } from '../radio-view-model';

/** `1/single` + a fully-observed dsp group (nrActive true, nbActive false —
 *  both toggles exercised at least once by the base fixture). */
const base = (): RadioViewModel => withDsp(topologyFixtures['1/single']);

type AnyField = keyof DspViewModel;
const NUMERIC_FIELDS: readonly AnyField[] = [
  ...DSP_TOGGLES.map(([f]) => f), ...DSP_LEVELS.map(([f]) => f), 'nbLevel',
] as readonly AnyField[];
/** The thumb position an unread level control MUST claim (MOR-1304/1305 F2)
 *  — `numberOf`'s own fallback, verbatim, so a fallback-value mutation is
 *  pinned rather than left free to claim any position. `nbLevel` falls back
 *  to `0` (its own literal in `DspSurface.svelte`), not its declared min. */
const LEVEL_FALLBACK: Partial<Record<AnyField, number>> = {
  ...Object.fromEntries(DSP_LEVELS.map(([f, , min]) => [f, min])),
  nbLevel: 0,
};

/** Re-shape ONE dsp field of an otherwise fully-available fixture. */
function withField(
  view: RadioViewModel, field: AnyField,
  over: { availability?: Availability; unknown?: boolean },
): RadioViewModel {
  const dsp = view.dsp!;
  const current = dsp[field] as { reading: unknown; availability: Availability };
  return {
    ...view,
    dsp: {
      ...dsp,
      [field]: {
        reading: over.unknown ? { status: 'unknown' } : current.reading,
        availability: over.availability ?? current.availability,
      },
    } as DspViewModel,
  };
}

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Handlers = {
  onToggle?: (field: DspToggleField, next: boolean) => void;
  onLevelChange?: (field: DspLevelField, value: number) => void;
  onNotchModeChange?: (mode: 'off' | 'auto' | 'manual') => void;
  onAgcModeChange?: (mode: number) => void;
};
type Props = Handlers & {
  agcLabels?: Record<string, string>; nbLevelMax?: number; nbLevelPercent?: boolean;
  pendingNb?: boolean | null; pendingNr?: boolean | null;
};

function render(view: RadioViewModel, props: Props = {}) {
  const component = mount(DspSurface, { target, props: { view, ...props } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="dsp-surface"]'),
    control: (field: string) => q<HTMLElement>(`[data-testid="dsp-${field}"]`),
    input: (field: string) => q<HTMLInputElement>(`[data-testid="dsp-${field}"] input`),
    notchButton: (mode: string) => q<HTMLButtonElement>(`[data-testid="dsp-notchMode-${mode}"]`),
    agcButton: (mode: number) => q<HTMLButtonElement>(`[data-testid="dsp-agcMode-${mode}"]`),
  };
}

function withSurface(view: RadioViewModel, fn: (s: ReturnType<typeof render>) => void, props: Props = {}): void {
  const s = render(view, props);
  try { fn(s); } finally { s.dispose(); }
}

/** `disabled` for a <button>, or for the <input> a level control wraps. */
function isDisabled(s: ReturnType<typeof render>, field: string): boolean {
  const el = s.control(field)!;
  if (el instanceof HTMLButtonElement) return el.disabled;
  return s.input(field)!.disabled;
}

// ── 1. Structural gating: absent, never a disabled promise ─────────────────

describe('structural availability decides whether a control EXISTS', () => {
  it.each(NUMERIC_FIELDS)('renders no control at all for a structurally absent "%s"', (field) => {
    const view = withField(base(), field, { availability: { structural: false, operational: false } });
    withSurface(view, (s) => {
      expect(s.control(field)).toBeNull();
      expect(s.root()).not.toBeNull();
    });
  });

  it.each(NUMERIC_FIELDS)('renders an enabled control for a fully available "%s"', (field) => {
    withSurface(base(), (s) => {
      expect(s.control(field)).not.toBeNull();
      expect(isDisabled(s, field)).toBe(false);
    });
  });

  it('renders nothing for a view model carrying no dsp group', () => {
    const view = { ...topologyFixtures['1/single'] };
    withSurface(view as RadioViewModel, (s) => {
      expect(s.root()).toBeNull();
      expect(target.querySelectorAll('button, input')).toHaveLength(0);
    });
  });

  it('renders no notchMode/agcMode control when structurally absent', () => {
    const view = withField(withField(base(), 'notchMode', {
      availability: { structural: false, operational: false },
    }), 'agcMode', { availability: { structural: false, operational: false } });
    withSurface(view, (s) => {
      expect(s.notchButton('off')).toBeNull();
      expect(s.agcButton(1)).toBeNull();
    });
  });
});

// ── 2. Operational gating + unknown-honesty (carry-forwards 2 and 4) ───────

describe('operational availability decides whether a control is USABLE', () => {
  it.each(NUMERIC_FIELDS)('keeps "%s" present but disabled when only operationally unavailable', (field) => {
    const view = withField(base(), field, { availability: { structural: true, operational: false } });
    withSurface(view, (s) => {
      expect(s.control(field)).not.toBeNull();
      expect(isDisabled(s, field)).toBe(true);
      expect(s.control(field)!.dataset.disabledReason).toBe('field-not-observed');
    });
  });

  // Carry-forward (4): an unobserved reading renders '?', never a v2 default
  // (0 dB, OFF, WIDE) — and never enables the control either.
  it.each(NUMERIC_FIELDS)('renders "?" and disables "%s" on an unobserved reading', (field) => {
    const view = withField(base(), field, { unknown: true });
    withSurface(view, (s) => {
      expect(isDisabled(s, field)).toBe(true);
      const text = s.control(field)!.textContent ?? '';
      expect(text).toContain('?');
      // MOR-1304/1305 F2: the rendered slider THUMB of an unread level is not
      // free to claim any position — it must sit at the field's declared
      // fallback (`numberOf`'s own default), never a mutated stand-in value.
      const input = s.input(field);
      if (input) expect(input.value).toBe(String(LEVEL_FALLBACK[field]));
    });
  });

  // MOR-1304/1305 N1/MD7: `pressedOf` must return `undefined` — never `false`
  // — for an unobserved toggle reading, so Svelte OMITS `aria-pressed`
  // entirely. `aria-pressed="false"` is not the absence of a claim, it is the
  // claim "this control is OFF" about a reading the radio never reported —
  // carry-forward 4's exact prohibition, applied to the attribute as well as
  // the visible text.
  it.each(DSP_TOGGLES.map(([f]) => f))(
    'never fabricates aria-pressed=false for an unobserved toggle "%s"',
    (field) => {
      const view = withField(base(), field, { unknown: true });
      withSurface(view, (s) => {
        expect(s.control(field)!.getAttribute('aria-pressed')).toBeNull();
      });
    },
  );

  // Carry-forward (2): `agcTimeConstant` structurally present but never
  // observed renders exactly like any other honest present-unobserved field —
  // no special-casing for the borrowed-capability optimism.
  it('renders agcTimeConstant present-and-disabled, not hidden, when unobserved', () => {
    const view = withField(base(), 'agcTimeConstant', { unknown: true });
    withSurface(view, (s) => {
      expect(s.control('agcTimeConstant')).not.toBeNull();
      expect(isDisabled(s, 'agcTimeConstant')).toBe(true);
    });
  });

  it('disables notchMode/agcMode buttons on an unobserved reading', () => {
    const view = withField(withField(base(), 'notchMode', { unknown: true }), 'agcMode', { unknown: true });
    withSurface(view, (s) => {
      expect(s.notchButton('off')!.disabled).toBe(true);
      expect(s.agcButton(1)!.disabled).toBe(true);
    });
  });
});

// ── 3. Toggle intents: nrActive/nbActive compute their own next value ──────

describe('toggle intents compute the next boolean from the current reading', () => {
  it('emits (field, next) flipping the current value', () => {
    const onToggle = vi.fn();
    // base(): nrActive known(true), nbActive known(false).
    withSurface(base(), (s) => {
      s.control('nrActive')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      flushSync();
      expect(onToggle).toHaveBeenCalledExactlyOnceWith('nrActive', false);
      s.control('nbActive')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      flushSync();
      expect(onToggle).toHaveBeenNthCalledWith(2, 'nbActive', true);
    }, { onToggle });
  });

  // MUTATION KILLED: toggling from an unobserved reading — the next value
  // would be a coin flip presented as a confirmed command.
  it('emits nothing when the reading is unobserved', () => {
    const onToggle = vi.fn();
    const view = withField(base(), 'nrActive', { unknown: true });
    withSurface(view, (s) => {
      s.control('nrActive')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      flushSync();
      expect(onToggle).not.toHaveBeenCalled();
    }, { onToggle });
  });
});

// ── 4. Level intents carry the field and the raw value ─────────────────────

describe('level intents reach the caller with the field and the raw value', () => {
  it.each(DSP_LEVELS)('emits (%s, value) on input with the declared range', (field, _label, min, max, step, _fmt?) => {
    const onLevelChange = vi.fn();
    withSurface(base(), (s) => {
      const input = s.input(field)!;
      expect(input.min).toBe(String(min));
      expect(input.max).toBe(String(max));
      expect(input.step).toBe(String(step));
      input.value = String(min);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      flushSync();
      expect(onLevelChange).toHaveBeenCalledExactlyOnceWith(field, min);
    }, { onLevelChange });
  });

  it('passes reading values through unrescaled (carry-forward 3)', () => {
    // withDsp(): nrLevel 8, nbDepth 5 — the adapter's own display-scaled
    // values; this surface must show them verbatim, never re-derive a scale.
    withSurface(base(), (s) => {
      expect(s.input('nrLevel')!.valueAsNumber).toBe(8);
      expect(s.input('nbDepth')!.valueAsNumber).toBe(5);
    });
  });

  /**
   * MOR-1304/1305 F3: the `level()` handler guard must reject an unusable
   * field on its OWN, independently of the `disabled` attribute. A plain
   * `.click()`/native interaction never reaches a disabled control's handler
   * at all, which would let this test pass even if the in-component guard
   * were deleted — so the input event is dispatched directly, the same
   * bypass a determined operator's assistive tech or a stray event listener
   * could still trigger.
   */
  it('emits nothing when the level reading is unobserved, even bypassing disabled', () => {
    const onLevelChange = vi.fn();
    const view = withField(base(), 'nrLevel', { unknown: true });
    withSurface(view, (s) => {
      const input = s.input('nrLevel')!;
      expect(input.disabled).toBe(true);
      input.value = '9';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      flushSync();
      expect(onLevelChange).not.toHaveBeenCalled();
    }, { onLevelChange });
  });
});

// ── 5. Range-parameterisation: nbLevel takes its ceiling from caps-echo props ─

describe('nbLevel is range-parameterised by the caps-echoed nbLevelMax/nbLevelPercent props', () => {
  it('uses the default 0..255 raw range when no props are supplied', () => {
    withSurface(base(), (s) => {
      expect(s.input('nbLevel')!.max).toBe('255');
      expect(s.input('nbLevel')!.valueAsNumber).toBe(64); // withDsp() fixture value
      expect(s.control('nbLevel')!.textContent).toContain('64');
    });
  });

  it('uses a caller-supplied ceiling (FTX-1-shaped: native 0..10, raw display)', () => {
    withSurface(base(), (s) => {
      expect(s.input('nbLevel')!.max).toBe('10');
      // Raw pass-through display (carry-forward 3): the fixture's raw
      // reading (64) renders verbatim, regardless of the ceiling prop.
      expect(s.control('nbLevel')!.textContent).toContain('64');
    }, { nbLevelMax: 10, nbLevelPercent: false });
  });

  it('renders a percent display when nbLevelPercent is true', () => {
    withSurface(base(), (s) => {
      // 64 / 255 rounds to 25%.
      expect(s.control('nbLevel')!.textContent).toContain('25%');
    }, { nbLevelMax: 255, nbLevelPercent: true });
  });

  it('emits the raw nbLevel value unrescaled regardless of display mode', () => {
    const onLevelChange = vi.fn();
    withSurface(base(), (s) => {
      const input = s.input('nbLevel')!;
      input.value = '5';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      flushSync();
      expect(onLevelChange).toHaveBeenCalledExactlyOnceWith('nbLevel', 5);
    }, { onLevelChange, nbLevelMax: 10, nbLevelPercent: false });
  });
});

// ── 6. notchMode: a three-way choice, not a boolean toggle ──────────────────

describe('notchMode renders as a three-way choice', () => {
  it('marks the current value pressed and the others not', () => {
    // withDsp(): notchMode 'off'.
    withSurface(base(), (s) => {
      expect(s.notchButton('off')!.getAttribute('aria-pressed')).toBe('true');
      expect(s.notchButton('auto')!.getAttribute('aria-pressed')).toBe('false');
      expect(s.notchButton('manual')!.getAttribute('aria-pressed')).toBe('false');
    });
  });

  it('emits the clicked mode verbatim', () => {
    const onNotchModeChange = vi.fn();
    withSurface(base(), (s) => {
      s.notchButton('manual')!.click();
      flushSync();
      expect(onNotchModeChange).toHaveBeenCalledExactlyOnceWith('manual');
    }, { onNotchModeChange });
  });

  /**
   * MOR-1304/1305 F3: `.click()` on a disabled button never reaches its
   * `onclick` handler at all — a no-op that would pass this assertion even
   * with the `notch()` guard deleted. Dispatching the click event directly
   * proves the GUARD rejects it, not merely that `disabled` suppressed the
   * interaction.
   */
  it('emits nothing when clicked on an unobserved reading, even bypassing disabled', () => {
    const onNotchModeChange = vi.fn();
    const view = withField(base(), 'notchMode', { unknown: true });
    withSurface(view, (s) => {
      const btn = s.notchButton('auto')!;
      expect(btn.disabled).toBe(true);
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      flushSync();
      expect(onNotchModeChange).not.toHaveBeenCalled();
    }, { onNotchModeChange });
  });
});

// ── 7. agcMode: dynamic choice set from the fact group + caps-echoed labels ──

describe('agcMode renders the capability-derived choice set with caps-echoed labels', () => {
  it('renders one button per agcModes entry plus OFF, labelled from agcLabels', () => {
    // withDsp(): agcModes [1, 2, 3], agcMode known(2).
    withSurface(base(), (s) => {
      expect(s.agcButton(0)!.textContent).toBe('OFF');
      expect(s.agcButton(1)!.textContent).toBe('FAST');
      expect(s.agcButton(2)!.textContent).toBe('MID');
      expect(s.agcButton(3)!.textContent).toBe('SLOW');
      expect(s.agcButton(2)!.getAttribute('aria-pressed')).toBe('true');
      expect(s.agcButton(1)!.getAttribute('aria-pressed')).toBe('false');
    }, { agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' } });
  });

  it('falls back to the raw ordinal when no label is supplied', () => {
    withSurface(base(), (s) => {
      expect(s.agcButton(1)!.textContent).toBe('1');
    }, { agcLabels: {} });
  });

  it('emits the clicked mode verbatim', () => {
    const onAgcModeChange = vi.fn();
    withSurface(base(), (s) => {
      s.agcButton(1)!.click();
      flushSync();
      expect(onAgcModeChange).toHaveBeenCalledExactlyOnceWith(1);
    }, { onAgcModeChange });
  });

  /**
   * MOR-1304/1305 F3: same bypass discipline as notchMode's guard test — the
   * event is dispatched directly so a deleted `agc()` guard is what this
   * assertion catches, not merely `disabled` suppressing a plain `.click()`.
   */
  it('emits nothing when clicked on an unobserved reading, even bypassing disabled', () => {
    const onAgcModeChange = vi.fn();
    const view = withField(base(), 'agcMode', { unknown: true });
    withSurface(view, (s) => {
      const btn = s.agcButton(1)!;
      expect(btn.disabled).toBe(true);
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      flushSync();
      expect(onAgcModeChange).not.toHaveBeenCalled();
    }, { onAgcModeChange });
  });
});

// ── 7b. Pending-target affordance (MOR-1441 leg 2) ──────────────────────────

describe('pending-target affordance (MOR-1441 leg 2)', () => {
  // base(): nrActive known(true), nbActive known(false).
  it('marks the pending toggle distinctly, leaves aria-pressed reading CONFIRMED', () => {
    withSurface(base(), (s) => {
      expect(s.control('nrActive')!.dataset.pendingStatus).toBe('pending');
      expect(s.control('nrActive')!.getAttribute('aria-pressed')).toBe('true'); // confirmed, unchanged
      expect(s.control('nbActive')!.dataset.pendingStatus).toBe('confirmed');
      expect(s.control('nbActive')!.getAttribute('aria-pressed')).toBe('false'); // confirmed, unchanged
    }, { pendingNr: false });
  });

  it('renders confirmed status and no announcement for both toggles when nothing is pending', () => {
    withSurface(base(), (s) => {
      expect(s.control('nrActive')!.dataset.pendingStatus).toBe('confirmed');
      expect(s.control('nbActive')!.dataset.pendingStatus).toBe('confirmed');
      expect(target.querySelector('.sr-only')).toBeNull();
    });
  });

  it('renders a screen-reader announcement only for the field that is pending', () => {
    withSurface(base(), (s) => {
      expect(s.control('nbActive')!.parentElement!.querySelector('.sr-only')).not.toBeNull();
      expect(target.querySelectorAll('.sr-only')).toHaveLength(1);
    }, { pendingNb: true });
  });

  // THE seam test — the exact class of defect leg 1's runaway bug belonged
  // to, applied to a toggle: `toggle()` computes the NEXT boolean from
  // `dsp[field].reading.value` (CONFIRMED), never from the pending prop. If
  // it instead read pending, an in-flight "turn NR on" (confirmed still
  // false, pending true) would compute `!true = false` and dispatch a
  // cancelling `set_nr(false)` the instant the operator clicks again —
  // silently reversing their own in-flight command instead of repeating it.
  it('SEAM: toggling while pending computes the next value from CONFIRMED, never from pending', () => {
    const onToggle = vi.fn();
    // Confirmed nrActive=true, but an in-flight "turn OFF" is pending
    // (pendingNr=false) — reading pending would compute `!false=true`
    // (re-affirming ON); reading confirmed correctly computes `!true=false`.
    withSurface(base(), (s) => {
      s.control('nrActive')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      flushSync();
      expect(onToggle).toHaveBeenCalledExactlyOnceWith('nrActive', false);
    }, { onToggle, pendingNr: false });
  });

  it('SEAM: the mirror case — confirmed nbActive=false, pending "turn ON" (true), still flips from confirmed', () => {
    const onToggle = vi.fn();
    withSurface(base(), (s) => {
      s.control('nbActive')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      flushSync();
      expect(onToggle).toHaveBeenCalledExactlyOnceWith('nbActive', true);
    }, { onToggle, pendingNb: true });
  });
});

// ── 8. Presentation-only: no store, transport or command import ────────────

describe('this surface stays presentation-only', () => {
  const withoutComments = (source: string): string => source
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
  const source = withoutComments(readFileSync('src/semantic/DspSurface.svelte', 'utf8'));

  it('imports no transport, store or command-bus module', () => {
    expect(source).not.toMatch(/\$lib\/transport/);
    expect(source).not.toMatch(/\$lib\/stores/);
    expect(source).not.toMatch(/command-bus/);
  });
});
