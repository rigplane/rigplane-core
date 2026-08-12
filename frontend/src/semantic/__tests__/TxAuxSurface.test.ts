/**
 * MOR-1265 — the semantic TX-auxiliary surface (vocabulary slice 1B).
 *
 * SAFETY-CRITICAL. This surface carries ATU **TUNE**, which emits a carrier:
 * it is a transmit-causing action, not a settings control. Every test below
 * names the mutation it kills, because the failure modes are operational:
 *   (a) TUNE reachable while the App TX authority would refuse a key intent
 *       (MOR-1262 §2 slice 1 safety note i);
 *   (b) VOX armed on a field this radio never reported (safety note ii);
 *   (c) this surface growing a key/unkey control and becoming a SECOND TX
 *       authority (safety note iii) — exactly one `<RxTxSurface>` keys.
 *
 * The TUNE gate is the SHARED `keyBlockedReasons` predicate from
 * `rx-tx-surface.ts` — the same function, on the same authority snapshot, that
 * gates `RxTxSurface`'s key button. Not a copy: a copy could disagree.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import TxAuxSurface, {
  TX_AUX_LEVELS, TX_AUX_TOGGLES,
  type TxAuxLevelField, type TxAuxToggleField,
} from '../TxAuxSurface.svelte';
import { topologyFixtures, withTxAux } from '../fixtures/topologies';
import type { Availability, RadioViewModel, TxAuxViewModel } from '../radio-view-model';
import { keyBlockedReasons, type TxAuthoritySnapshot } from '../rx-tx-surface';

const IDLE_RX: TxAuthoritySnapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null,
};
const snap = (over: Partial<TxAuthoritySnapshot> = {}): TxAuthoritySnapshot => ({ ...IDLE_RX, ...over });

/** `1/single` is the fixture whose permit is 'allowed' AND txTarget known. */
const base = (): RadioViewModel => withTxAux(topologyFixtures['1/single']);

type AnyField = keyof TxAuxViewModel;
const ALL_FIELDS: readonly AnyField[] = [
  ...TX_AUX_TOGGLES.map(([f]) => f), ...TX_AUX_LEVELS.map(([f]) => f),
] as readonly AnyField[];

/** Re-shape ONE txAux field of an otherwise fully-available fixture. */
function withField(
  view: RadioViewModel, field: AnyField,
  over: { availability?: Availability; unknown?: boolean },
): RadioViewModel {
  const txAux = view.txAux!;
  const current = txAux[field];
  return {
    ...view,
    txAux: {
      ...txAux,
      [field]: {
        reading: over.unknown ? { status: 'unknown' } : current.reading,
        availability: over.availability ?? current.availability,
      },
    } as TxAuxViewModel,
  };
}

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Handlers = {
  onToggle?: (field: TxAuxToggleField) => void;
  onLevelChange?: (field: TxAuxLevelField, value: number) => void;
  onAtuTune?: () => void;
};

function render(view: RadioViewModel, tx: TxAuthoritySnapshot, handlers: Handlers = {}) {
  const component = mount(TxAuxSurface, { target, props: { view, tx, ...handlers } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="tx-aux-surface"]'),
    control: (field: string) => q<HTMLElement>(`[data-testid="tx-aux-${field}"]`),
    input: (field: string) => q<HTMLInputElement>(`[data-testid="tx-aux-${field}"] input`),
    tune: () => q<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]'),
    tuneReasons: () => [...target.querySelectorAll('[data-testid="tx-aux-tune-blocked"] [data-reason]')]
      .map((el) => el.getAttribute('data-reason')),
  };
}

function withSurface(
  view: RadioViewModel, tx: TxAuthoritySnapshot,
  fn: (s: ReturnType<typeof render>) => void, handlers: Handlers = {},
): void {
  const s = render(view, tx, handlers);
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
  // MUTATION KILLED: rendering a structurally-absent control as merely
  // disabled. "This radio has no VOX" and "VOX is unreadable right now" are
  // different claims, and the MOR-977/1256 doctrine renders them differently.
  it.each(ALL_FIELDS)('renders no control at all for a structurally absent "%s"', (field) => {
    const view = withField(base(), field, {
      availability: { structural: false, operational: false },
    });
    withSurface(view, snap(), (s) => {
      expect(s.control(field)).toBeNull();
      expect(s.root()).not.toBeNull();
    });
  });

  it.each(ALL_FIELDS)('renders an enabled control for a fully available "%s"', (field) => {
    withSurface(base(), snap(), (s) => {
      expect(s.control(field)).not.toBeNull();
      expect(isDisabled(s, field)).toBe(false);
    });
  });

  // MUTATION KILLED: dropping the whole-group guard. The surface must refuse
  // to render for a view model with no txAux facts rather than invent twelve
  // all-unknown controls (the MOR-1244 N3 fail-closed polarity).
  it('renders nothing for a view model carrying no txAux group', () => {
    const view = { ...topologyFixtures['1/single'] };
    withSurface(view as RadioViewModel, snap(), (s) => {
      expect(s.root()).toBeNull();
      expect(target.querySelectorAll('button')).toHaveLength(0);
    });
  });
});

// ── 2. Operational gating: present, disabled, with a reason ────────────────

describe('operational availability decides whether a control is USABLE', () => {
  // MUTATION KILLED: collapsing the two levels into one, so an operationally
  // degraded control either vanishes (losing the structural claim) or stays
  // live (acting on a value the radio never confirmed).
  it.each(ALL_FIELDS)('keeps "%s" present but disabled when only operationally unavailable', (field) => {
    const view = withField(base(), field, {
      availability: { structural: true, operational: false },
    });
    withSurface(view, snap(), (s) => {
      expect(s.control(field)).not.toBeNull();
      expect(isDisabled(s, field)).toBe(true);
      expect(s.control(field)!.dataset.disabledReason).toBe('field-not-observed');
    });
  });

  // MUTATION KILLED: enabling a control whose reading is `unknown`. Every
  // toggle computes its next value from the CURRENT one, so acting on an
  // unobserved field flips it to a guess — fail closed instead.
  it.each(ALL_FIELDS)('never enables "%s" on an unobserved reading', (field) => {
    const view = withField(base(), field, { unknown: true });
    withSurface(view, snap(), (s) => {
      expect(s.control(field)).not.toBeNull();
      expect(isDisabled(s, field)).toBe(true);
      expect(s.control(field)!.dataset.disabledReason).toBe('field-not-observed');
    });
  });

  it('shows no disabled reason on a control that is actually usable', () => {
    withSurface(base(), snap(), (s) => {
      for (const field of ALL_FIELDS) {
        expect(s.control(field)!.dataset.disabledReason).toBeUndefined();
      }
    });
  });
});

// ── 2b. MOR-1422: the disabled reason is legible, not just a data hook ─────

describe('the disabled reason is exposed on hover and to screen readers (MOR-1422)', () => {
  /** Resolves a control's `aria-describedby` target and returns ITS text —
   *  the id alone would only prove wiring, not that a screen reader has
   *  something to actually read. */
  function describedText(el: HTMLElement): string | null {
    const id = el.getAttribute('aria-describedby');
    if (!id) return null;
    return target.querySelector(`#${id}`)?.textContent ?? null;
  }

  it('puts "Not yet observed" on title and aria-describedby for an unobserved toggle', () => {
    const view = withField(base(), 'vox', { availability: { structural: true, operational: false } });
    withSurface(view, snap(), (s) => {
      const control = s.control('vox')!;
      expect(control.title).toBe('Not yet observed');
      expect(describedText(control)).toBe('Not yet observed');
    });
  });

  it('puts "Not yet observed" on title and aria-describedby for an unobserved level input', () => {
    const view = withField(base(), 'rfPower', { availability: { structural: true, operational: false } });
    withSurface(view, snap(), (s) => {
      const input = s.input('rfPower')!;
      expect(input.title).toBe('Not yet observed');
      expect(describedText(input)).toBe('Not yet observed');
    });
  });

  it('carries no reason text on hover or for screen readers once a control is usable', () => {
    withSurface(base(), snap(), (s) => {
      const control = s.control('vox')!;
      expect(control.title).toBe('');
      expect(control.hasAttribute('aria-describedby')).toBe(false);
    });
  });
});

// ── 3. VOX — safety note (ii), arming voice keying ─────────────────────────

describe('VOX arming obeys the two-level gate (safety note ii)', () => {
  it('is ABSENT on a radio without the VOX capability', () => {
    const view = withField(base(), 'vox', { availability: { structural: false, operational: false } });
    withSurface(view, snap(), (s) => expect(s.control('vox')).toBeNull());
  });

  it('is present-and-disabled while structurally present but unreadable', () => {
    const view = withField(base(), 'vox', { availability: { structural: true, operational: false } });
    withSurface(view, snap(), (s) => {
      expect(s.control('vox')!.dataset.disabledReason).toBe('field-not-observed');
      expect(isDisabled(s, 'vox')).toBe(true);
    });
  });

  // MUTATION KILLED: arming VOX from a click on an unobserved field. A VOX
  // toggle computes `!current`; with `current` unknown the "on" it sends is a
  // coin flip that arms voice keying.
  it('emits no intent when clicked on an unobserved reading', () => {
    const onToggle = vi.fn();
    const view = withField(base(), 'vox', { unknown: true });
    withSurface(view, snap(), (s) => {
      (s.control('vox') as HTMLButtonElement).click();
      flushSync();
      expect(onToggle).not.toHaveBeenCalled();
    }, { onToggle });
  });

  it('emits the toggle intent for its own field when usable', () => {
    const onToggle = vi.fn();
    withSurface(base(), snap(), (s) => {
      (s.control('vox') as HTMLButtonElement).click();
      flushSync();
      expect(onToggle).toHaveBeenCalledExactlyOnceWith('vox');
    }, { onToggle });
  });

  // MUTATION KILLED: sharing one disabled state across vox/voxGain/
  // antiVoxGain/voxDelay (the MOR-1244 handoff note: they share a structural
  // capability but each carries its OWN operational gate).
  it('does not collapse voxGain/antiVoxGain/voxDelay into the vox toggle state', () => {
    const view = withField(base(), 'voxGain', { availability: { structural: true, operational: false } });
    withSurface(view, snap(), (s) => {
      expect(isDisabled(s, 'voxGain')).toBe(true);
      expect(isDisabled(s, 'vox')).toBe(false);
      expect(isDisabled(s, 'antiVoxGain')).toBe(false);
      expect(isDisabled(s, 'voxDelay')).toBe(false);
    });
  });
});

// ── 4. ATU TUNE — safety note (i), a transmit-causing action ───────────────

/** Every way the App TX authority (or the permit) refuses a key intent. */
const BLOCKING: readonly (readonly [string, Partial<TxAuthoritySnapshot>])[] = [
  ['a fault is latched', { fault: 'on-timeout' }],
  ['a lease is in progress', { phase: 'key-confirm-pending' }],
  ['this browser may own the key', { mayOwnKey: true }],
  ['the radio is already transmitting', { radioTx: 'on' }],
  ['the RF state is unknown', { radioTx: 'unknown' }],
  ['TX risk is uncertain', { txRisk: 'uncertain' }],
  ['TX risk is confirmed-on', { txRisk: 'confirmed-on' }],
];

describe('ATU TUNE is gated by the App TX authority, exactly like the key intent', () => {
  it('offers TUNE when the authority is idle and the permit allows', () => {
    withSurface(base(), snap(), (s) => {
      expect(s.tune()).not.toBeNull();
      expect(s.tune()!.disabled).toBe(false);
      expect(s.tuneReasons()).toEqual([]);
    });
  });

  // MUTATION KILLED: gating TUNE on anything weaker than the key intent's own
  // predicate (or on nothing at all). Asserted against the SHARED
  // `keyBlockedReasons` so the two can never drift apart.
  it.each(BLOCKING)('disables TUNE while %s', (_label, over) => {
    const view = base();
    const tx = snap(over);
    expect(keyBlockedReasons(view, tx).length).toBeGreaterThan(0);
    withSurface(view, tx, (s) => {
      expect(s.tune()!.disabled).toBe(true);
      expect(s.tuneReasons()).toEqual([...keyBlockedReasons(view, tx)]);
    });
  });

  // The view-model half of the same predicate: an unknown TX target or a
  // denied/unknown permit blocks TUNE just as it blocks the key.
  it.each(['1/ab', '2/ab_shared'] as const)('disables TUNE on %s, whose permit is not allowed', (id) => {
    const view = withTxAux(topologyFixtures[id]);
    withSurface(view, snap(), (s) => {
      expect(s.tune()!.disabled).toBe(true);
      expect(s.tuneReasons()).toEqual([...keyBlockedReasons(view, snap())]);
    });
  });

  // MUTATION KILLED: relying on `disabled` alone. A programmatic click (or a
  // design language that restyles the control into a live element) must not
  // reach the radio either — widget-disabled AND handler-guarded.
  it.each(BLOCKING)('emits no TUNE intent from a forced click while %s', (_label, over) => {
    const onAtuTune = vi.fn();
    withSurface(base(), snap(over), (s) => {
      const tune = s.tune()!;
      tune.disabled = false; // simulate a restyled / programmatically enabled control
      tune.click();
      flushSync();
      expect(onAtuTune).not.toHaveBeenCalled();
    }, { onAtuTune });
  });

  it('emits the TUNE intent when the authority and the permit both allow', () => {
    const onAtuTune = vi.fn();
    withSurface(base(), snap(), (s) => {
      s.tune()!.click();
      flushSync();
      expect(onAtuTune).toHaveBeenCalledOnce();
    }, { onAtuTune });
  });

  // MUTATION KILLED: offering TUNE on a radio with no tuner, or on one whose
  // tuner status was never observed — a carrier fired from a guessed state.
  it('renders no TUNE control at all without a structurally present ATU', () => {
    const view = withField(base(), 'atu', { availability: { structural: false, operational: false } });
    withSurface(view, snap(), (s) => expect(s.tune()).toBeNull());
  });

  it('disables TUNE while the ATU status itself is unobserved', () => {
    const view = withField(base(), 'atu', { unknown: true });
    withSurface(view, snap(), (s) => expect(s.tune()!.disabled).toBe(true));
  });
});

// ── 5. Exactly one key path — safety note (iii) ────────────────────────────

describe('this surface is never a second key path (safety note iii)', () => {
  /** Same stripper the presentation-zone source pins use — the assertions
   *  below are about CODE, and the file's own header comment necessarily
   *  names `RxTxSurface` while explaining why it must not appear in it. */
  const withoutComments = (source: string): string => source
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
  const source = withoutComments(readFileSync('src/semantic/TxAuxSurface.svelte', 'utf8'));

  // MUTATION KILLED: a TxAuxSurface variant that renders a key/unkey control.
  // This is the named test the 1B brief requires such a variant to fail.
  it('renders no key or unkey control', () => {
    withSurface(base(), snap(), () => {
      expect(target.querySelector('[data-testid="rx-tx-key"]')).toBeNull();
      expect(target.querySelector('[data-testid="rx-tx-unkey"]')).toBeNull();
      for (const el of target.querySelectorAll<HTMLElement>('[data-testid]')) {
        expect(el.dataset.testid?.startsWith('rx-tx')).toBe(false);
      }
      for (const el of target.querySelectorAll<HTMLElement>('button')) {
        expect(el.textContent?.toLowerCase()).not.toMatch(/\bun?key\b/);
      }
    });
  });

  // MUTATION KILLED: importing the TX controller, mounting RxTxSurface, or
  // accepting a key intent here — any of which would make this a second
  // authority that could disagree with the one in `RxTxSurface`.
  it('holds no key intent, no controller import and no RxTxSurface mount', () => {
    expect(source).not.toMatch(/RxTxSurface/);
    expect(source).not.toMatch(/tx-controller/);
    expect(source).not.toMatch(/onRequestKey|onRequestUnkey|requestKey|requestUnkey/);
    expect(source).not.toMatch(/\btx\.(start|release|setIntent|resetFault)\b/);
  });

  // MUTATION KILLED: re-deriving the block predicate locally. A second copy
  // could disagree with the key button's; the shared import cannot.
  it('imports the block predicate from the shared rx-tx vocabulary', () => {
    expect(source).toMatch(/import\s*\{[^}]*keyBlockedReasons[^}]*\}\s*from\s*'\.\/rx-tx-surface'/);
  });
});

// ── 6. Level intents carry the field and the raw value ─────────────────────

describe('level intents reach the caller with the field and the raw value', () => {
  it.each(TX_AUX_LEVELS)('emits (%s, value) on input', (field, _label, min, max, step) => {
    const onLevelChange = vi.fn();
    withSurface(base(), snap(), (s) => {
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

  // MUTATION KILLED: normalising or rescaling a level on the way out. The
  // MOR-1244 contract carries RAW wire values (v2 units); rescaling here
  // would silently halve someone's RF power.
  it('passes the raw reading straight into the control value', () => {
    withSurface(base(), snap(), (s) => {
      expect(s.input('rfPower')!.valueAsNumber).toBe(0.8);
      expect(s.input('micGain')!.valueAsNumber).toBe(128);
      expect(s.input('voxDelay')!.valueAsNumber).toBe(20);
    });
  });
});

// ── 7. Readout formatting (MOR-1452) ────────────────────────────────────────

describe('every TX-aux level slider reads back as a percent of its own domain', () => {
  // The mobile/narrow composition regression this pins: RF power read back
  // as the literal `0.5529411764705883` instead of a formatted percent.
  it('formats RF power as a rounded percent (fixture value 0.8 -> "80%")', () => {
    withSurface(base(), snap(), (s) => {
      const output = s.control('rfPower')!.querySelector('output')!;
      expect(output.textContent).toBe('80%');
    });
  });

  // MOR-1452: before this ticket, a raw 0..255 level (mic gain) rendered as
  // the plain wire integer ("128") while RF power (0..1) already read as a
  // percent — two conventions on the same panel. Both now read as a percent
  // of their OWN declared domain: 128 of 0..255 is "50%", not "128".
  it('formats a raw 0..255 level (mic gain) as a percent of its own domain, not the raw wire int', () => {
    withSurface(base(), snap(), (s) => {
      const output = s.control('micGain')!.querySelector('output')!;
      expect(output.textContent).toBe('50%');
    });
  });

  // Every other level field on the surface follows the same one convention —
  // percent of its own declared [min, max], never a bare number regardless
  // of whether the field's native range happens to be 0..255, 0..1, or 0..20.
  it.each([
    ['driveGain', '50%'],
    ['voxGain', '20%'],
    ['antiVoxGain', '12%'],
    ['voxDelay', '100%'],
    ['compressorLevel', '4%'],
    ['monitorLevel', '50%'],
  ])('formats %s as %s', (field, expected) => {
    withSurface(base(), snap(), (s) => {
      const output = s.control(field)!.querySelector('output')!;
      expect(output.textContent).toBe(expected);
    });
  });
});
