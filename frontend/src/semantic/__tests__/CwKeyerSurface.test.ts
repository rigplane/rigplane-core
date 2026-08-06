/**
 * MOR-1310 — the semantic CW-keyer surface (vocabulary slice 9B).
 *
 * SAFETY-CRITICAL. Break-in KEYS THE TRANSMITTER, so every test below names
 * the mutation it kills:
 *   (a) the surface becoming a second key path — it must emit SETTING intents
 *       only, and never a key/unkey or a transmit-causing action
 *       (`cw_auto_tune`), exactly one `<RxTxSurface>` stays the authority
 *       (MOR-1262 decomposition R9);
 *   (b) break-in enabled under a permit that is not positively `'allowed'` —
 *       `denied` AND `unknown`, each pinned independently, on the widget AND
 *       in the handler (a `.click()` on a disabled button is a no-op, so the
 *       `disabled` assertion alone is vacuous — MOR-1304 F3);
 *   (c) a second permit derivation appearing in the CW path (MOR-1296 §2);
 *   (d) the `twinPeak` mutex rendered without naming RTTY, leaving the
 *       operator a permanently-disabled control in a block they read as "CW"
 *       (MOR-1296 O1);
 *   (e) an unread break-in state presented as "off" — v2's `formatBreakIn`
 *       falls back to 'OFF' and slice 9A degraded it to `unknown` precisely so
 *       an unreadable keyer never reads as "the key is safe".
 *
 * Fast-pool-safe by construction (MOR-1272): no `vi.mock`, no `vi.stubGlobal`,
 * no global spy. The command-bus / no-key-path behavioural pins against the
 * REAL module live in `components-v2/wiring/__tests__/
 * semantic-cw-keyer-wiring.component.test.ts`.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import CwKeyerSurface, {
  APF_CHOICES, BREAK_IN_BLOCKED_LABEL, BREAK_IN_CHOICES, CW_LEVELS, MUTEX_LABEL, POSTURE_LABEL,
  UNKNOWN_TEXT, breakInPosture, type CwLevelField,
} from '../CwKeyerSurface.svelte';
import { topologyFixtures, withCwKeyer, withTxAux } from '../fixtures/topologies';
import type {
  Availability, BreakInMode, CwKeyerField, CwKeyerViewModel, DisabledReason, RadioViewModel,
} from '../radio-view-model';

const SOURCE = readFileSync('src/semantic/CwKeyerSurface.svelte', 'utf8');
/** Comments stripped, so the file's own doctrine prose can never be what a
 *  source-scanning test matches. */
const CODE = SOURCE
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const ON: Availability = { structural: true, operational: true };
const OFF: Availability = { structural: false, operational: false };
const DEGRADED: Availability = { structural: true, operational: false };

/** `1/single` carries an `allowed` permit — the only fixture whose break-in is
 *  legitimately live, which is what most of the non-permit tests need. */
const base = (): RadioViewModel => withCwKeyer(topologyFixtures['1/single']);
const withCw = (
  over: Partial<CwKeyerViewModel>, view: RadioViewModel = base(),
): RadioViewModel => ({ ...view, cwKeyer: { ...view.cwKeyer!, ...over } });
const unread = <T>(availability: Availability = ON): CwKeyerField<T> =>
  ({ reading: { status: 'unknown' }, availability });
const known = <T>(value: T, availability: Availability = ON): CwKeyerField<T> =>
  ({ reading: { status: 'known', value }, availability });
const withReasons = (view: RadioViewModel, ...extra: DisabledReason[]): RadioViewModel =>
  ({ ...view, disabledReasons: [...view.disabledReasons, ...extra] });

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Handlers = {
  onBreakInMode?: (mode: number) => void;
  onLevelChange?: (field: CwLevelField, value: number) => void;
  onApfOn?: (on: boolean) => void;
  onTwinPeakToggle?: () => void;
  onReversePaddleToggle?: () => void;
};

function render(view: RadioViewModel, handlers: Handlers = {}) {
  const component = mount(CwKeyerSurface, { target, props: { view, ...handlers } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="cw-keyer-surface"]'),
    el: (id: string) => q<HTMLElement>(`[data-testid="cw-keyer-${id}"]`),
    input: (id: string) => q<HTMLInputElement>(`[data-testid="cw-keyer-${id}"] input`),
    text: (id: string) => q<HTMLElement>(`[data-testid="cw-keyer-${id}"]`)?.textContent?.trim(),
    /** Every focusable control the surface renders, whatever its state. */
    controls: () => [...target.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')],
  };
}

/** A real click, not `.click()`: `.click()` is a no-op on a disabled button, so
 *  it can never tell a `disabled` attribute apart from a handler guard. */
const press = (el: HTMLElement) => el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
const slide = (el: HTMLInputElement, value: number) => {
  el.value = String(value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
};

/* ── (a) the surface is not a key path ────────────────────────── */

describe('the CW-keyer surface is NOT a key path (decomposition R9)', () => {
  /** The whole static import closure of the file, allow-listed. Kills: adding
   *  ANY import that could reach the TX controller, the transport or the
   *  permit utility — including through a relative specifier. */
  it('imports nothing but the fact contract', () => {
    const specifiers = [...CODE.matchAll(/from\s+'([^']+)'/g)].map((m) => m[1]);
    expect(specifiers.length).toBeGreaterThan(0);
    expect([...new Set(specifiers)]).toEqual(['./radio-view-model']);
  });

  // Kills: `onMount(() => …)` and every relative of it, plus a dynamic import
  // used to smuggle in the controller.
  it('declares no lifecycle hook and no effect', () => {
    for (const forbidden of ['onMount', 'onDestroy', '$effect', 'import(']) {
      expect(CODE).not.toContain(forbidden);
    }
  });

  // Kills: (a) a key path, (c) a second permit derivation. Neither the key
  // vocabulary nor the permit machinery may appear anywhere in this file.
  it('never mentions keying, PTT, the TX controller or a permit derivation', () => {
    for (const forbidden of [
      'ptt', 'Ptt', 'PTT', 'requestKey', 'onRequestKey', 'onRequestUnkey', 'tx-controller',
      'TxAuthoritySnapshot', 'keyBlockedReasons', 'getFrequencyPermit', 'txBands', 'auto_tune',
      'onAutoTune', 'AutoTune', 'sendCommand', '$lib/transport', '$lib/utils/tx-permit',
    ]) {
      expect(CODE).not.toContain(forbidden);
    }
  });

  // Kills: the surface growing an authority prop or a key intent. The props
  // list is the structural statement that this cannot key.
  it('takes exactly one state prop — the view model — plus SETTING intents', () => {
    const props = CODE.slice(CODE.indexOf('interface Props'), CODE.indexOf('}: Props'));
    expect([...props.matchAll(/^\s{4}(\w+)[?]?:/gm)].map((m) => m[1])).toEqual([
      'view', 'onBreakInMode', 'onLevelChange', 'onApfOn', 'onTwinPeakToggle',
      'onReversePaddleToggle',
    ]);
  });

  // Kills: adding an AUTO TUNE affordance. `cw_auto_tune` is transmit-causing;
  // the MOR-1244 ATU-TUNE precedent is state carried, control never.
  it('renders no AUTO TUNE control', () => {
    const r = render(base());
    expect(r.root()!.textContent!.toUpperCase()).not.toContain('TUNE');
    r.dispose();
  });

  // Kills: rendering an empty CW panel for a radio with no keyer.
  it('renders NOTHING at all when the view model carries no cwKeyer group', () => {
    const view = { ...base() };
    delete (view as { cwKeyer?: unknown }).cwKeyer;
    const r = render(view);
    expect(r.root()).toBeNull();
    expect(target.textContent).toBe('');
    r.dispose();
  });

  /**
   * The FULLY-ARMED sweep: break-in structurally available, permit `allowed`,
   * every fact observed, every control enabled — then every control on the
   * surface is interacted with. Only the five declared SETTING intents may
   * fire, and each carries a setting payload. Kills: wiring a key intent onto
   * any control (it would have to arrive as a sixth channel, or as a call on a
   * handler this surface is not allowed to have).
   */
  it('emits ONLY setting intents when every control is exercised fully armed', () => {
    const seen: string[] = [];
    const r = render(withCw({ breakIn: known<BreakInMode>('full') }, withTxAux(base())), {
      onBreakInMode: (m) => seen.push(`breakIn:${m}`),
      onLevelChange: (f, v) => seen.push(`level:${f}:${v}`),
      onApfOn: (on) => seen.push(`apf:${on}`),
      onTwinPeakToggle: () => seen.push('twinPeak'),
      onReversePaddleToggle: () => seen.push('reversePaddle'),
    });
    const controls = r.controls();
    expect(controls.length).toBeGreaterThan(0);
    for (const control of controls) {
      if (control instanceof HTMLInputElement) slide(control, Number(control.max));
      else press(control);
    }
    flushSync();
    expect(seen).toEqual([
      'breakIn:0', 'breakIn:1', 'breakIn:2',
      'level:keyerSpeed:48', 'level:pitchHz:900', 'level:breakInDelay:255',
      'reversePaddle', 'apf:false', 'apf:true', 'twinPeak',
    ]);
    r.dispose();
  });
});

/* ── (b) the break-in permit gate, fail-closed ─────────────────── */

describe('break-in obeys the ONE txPermit and fails closed', () => {
  it('enables the break-in choices under a positively allowed permit', () => {
    const r = render(base());
    for (const [label] of BREAK_IN_CHOICES) {
      expect(r.el(`break-in-${label}`)!.hasAttribute('disabled')).toBe(false);
    }
    expect(r.el('break-in')!.dataset.permitted).toBe('true');
    expect(r.el('break-in-blocked')).toBeNull();
    r.dispose();
  });

  // Kills: enabling break-in under a DENIED permit. `2/ab_shared`'s permit is
  // `denied`; `withCwKeyer` records the matching reason, as the validator
  // requires.
  it.each(BREAK_IN_CHOICES)('disables the %s choice under a denied permit', (label) => {
    const r = render(withCwKeyer(topologyFixtures['2/ab_shared']));
    expect(r.el(`break-in-${label}`)!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  /**
   * Kills: enabling break-in under an UNKNOWN permit — the deliberate
   * over-disable of MOR-1296 O2, and the single most likely "fix" a future
   * reader would apply. Pinned INDEPENDENTLY of the denied case: `unknown` is
   * not `denied`, and a `status === 'denied'` gate would pass the test above
   * while failing this one.
   */
  it.each(BREAK_IN_CHOICES)('disables the %s choice under an UNKNOWN permit', (label) => {
    const r = render(withCwKeyer(topologyFixtures['1/ab']));
    expect(r.el(`break-in-${label}`)!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  /**
   * The handler half, for BOTH non-allowed permits, dispatched as a real
   * bubbling click rather than `.click()`. Kills: dropping the `permitAllowed`
   * conjunct from `setBreakIn` — which the `disabled` assertions above cannot
   * see, because jsdom fires nothing on a disabled button (MOR-1304 F3).
   */
  it.each([
    ['denied', '2/ab_shared'], ['unknown', '1/ab'],
  ] as const)('emits no break-in intent under a %s permit, even bypassing disabled', (_s, key) => {
    const onBreakInMode = vi.fn();
    const r = render(withCwKeyer(topologyFixtures[key]), { onBreakInMode });
    for (const [label] of BREAK_IN_CHOICES) press(r.el(`break-in-${label}`)!);
    flushSync();
    expect(onBreakInMode).not.toHaveBeenCalled();
    r.dispose();
  });

  // Kills: swallowing the recorded reason and leaving a dead control with no
  // cause. The validator guarantees the entry exists (MOR-1296 §3), so the
  // surface only has to render it — and it must render the RIGHT one.
  it.each([
    ['2/ab_shared', 'out-of-band'], ['1/ab', 'tx-target-unknown'],
  ] as const)('renders the recorded reason for %s', (key, code) => {
    const view = withCwKeyer(topologyFixtures[key]);
    const r = render({
      ...view,
      disabledReasons: view.disabledReasons.map(
        (reason) => (reason.field === 'cwKeyer.breakIn' ? { field: reason.field, code } : reason),
      ),
    });
    const blocked = r.el('break-in-blocked')!;
    expect(blocked).not.toBeNull();
    expect(blocked.dataset.reason).toBe(code);
    expect(blocked.textContent!.trim()).toBe(BREAK_IN_BLOCKED_LABEL[code]);
    r.dispose();
  });

  // Kills: a label map that drifts from the three codes the adapter can emit
  // for this field (`deriveCwKeyerReasons`).
  it('has a label for each of the three codes the CW break-in gate can record', () => {
    expect(Object.keys(BREAK_IN_BLOCKED_LABEL).sort())
      .toEqual(['capability-unavailable', 'out-of-band', 'tx-target-unknown']);
  });

  // Kills: a radio with no break-in capability growing the control anyway.
  it('renders no break-in block at all when break-in is structurally absent', () => {
    const r = render(withCw({ breakIn: unread<BreakInMode>(OFF) }));
    expect(r.el('break-in')).toBeNull();
    r.dispose();
  });

  // Kills: acting on an unread break-in reading even while the permit allows.
  it('disables and refuses break-in while its own reading is unobserved', () => {
    const onBreakInMode = vi.fn();
    const r = render(withCw({ breakIn: unread<BreakInMode>(DEGRADED) }), { onBreakInMode });
    for (const [label] of BREAK_IN_CHOICES) {
      expect(r.el(`break-in-${label}`)!.hasAttribute('disabled')).toBe(true);
      press(r.el(`break-in-${label}`)!);
    }
    flushSync();
    expect(onBreakInMode).not.toHaveBeenCalled();
    r.dispose();
  });

  it.each(BREAK_IN_CHOICES)('emits the absolute %s intent when permitted', (label, mode) => {
    const onBreakInMode = vi.fn();
    const r = render(base(), { onBreakInMode });
    press(r.el(`break-in-${label}`)!);
    flushSync();
    expect(onBreakInMode).toHaveBeenCalledExactlyOnceWith(mode);
    r.dispose();
  });

  it.each(BREAK_IN_CHOICES)('checks exactly the observed break-in mode %s', (label) => {
    const r = render(withCw({ breakIn: known<BreakInMode>(label) }));
    for (const [other] of BREAK_IN_CHOICES) {
      expect(r.el(`break-in-${other}`)!.getAttribute('aria-checked')).toBe(String(other === label));
    }
    r.dispose();
  });
});

/* ── (e) armed-vs-off is a rendered distinction, unknown fails closed ── */

describe('break-in POSTURE distinguishes armed from off, and unknown from both', () => {
  // The explicit MOR-1296 open-question-5 decision, made here: "armed but not
  // permitted" reads differently from "off and not permitted", because the
  // radio's own paddle can still key an armed keyer while this UI refuses to
  // change the setting.
  it.each([
    ['off', 'off'], ['semi', 'armed'], ['full', 'armed'],
  ] as const)('reads a %s break-in as posture %s', (value, posture) => {
    expect(breakInPosture(known<BreakInMode>(value))).toBe(posture);
    const r = render(withCw({ breakIn: known<BreakInMode>(value) }));
    expect(r.el('break-in')!.dataset.posture).toBe(posture);
    expect(r.text('posture')).toBe(POSTURE_LABEL[posture]);
    r.dispose();
  });

  // Kills: v2's `formatBreakIn` fallback to 'OFF'. An unreadable keyer must
  // never present as "the key is safe".
  it('reads an UNOBSERVED break-in as unknown, never as off', () => {
    expect(breakInPosture(unread<BreakInMode>(DEGRADED))).toBe('unknown');
    const r = render(withCw({ breakIn: unread<BreakInMode>(DEGRADED) }));
    expect(r.el('break-in')!.dataset.posture).toBe('unknown');
    expect(r.text('posture')).toBe(POSTURE_LABEL.unknown);
    expect(r.text('posture')).not.toContain('off');
    r.dispose();
  });

  // Kills: a posture whose only channel is an attribute (forced-colors,
  // MOR-977), and a wording that does not say what the key will DO.
  it.each(['off', 'armed', 'unknown'] as const)('says in TEXT what %s means for the key', (p) => {
    expect(POSTURE_LABEL[p]).toMatch(/key/);
  });

  // The two "not permitted" states are visibly different, which is the whole
  // point of the decision above.
  it('renders armed-and-not-permitted differently from off-and-not-permitted', () => {
    const denied = withCwKeyer(topologyFixtures['2/ab_shared']);
    const armed = render(withCw({ breakIn: known<BreakInMode>('semi') }, denied));
    const armedText = armed.el('break-in')!.textContent;
    armed.dispose();
    const idle = render(withCw({ breakIn: known<BreakInMode>('off') }, denied));
    expect(idle.el('break-in')!.textContent).not.toBe(armedText);
    expect(armedText).toContain('ARMED');
    idle.dispose();
  });
});

/* ── (d) the group is not uniformly "CW" ──────────────────────── */

describe('the mutex reasons are rendered with the other mode NAMED (MOR-1296 O1)', () => {
  // Kills: labelling the block "CW". `twinPeak` is an RTTY control living here
  // for v2-family reasons; a block called "CW" makes it inexplicable.
  it('does not present the group as uniformly CW', () => {
    const r = render(base());
    expect(r.root()!.getAttribute('aria-label')).toBe('CW keyer and audio peak filters');
    r.dispose();
  });

  // Kills: rendering the generic `mutually-exclusive-control` code with no
  // words, leaving a permanently-disabled TPF button unexplained.
  it('disables TPF and names RTTY when the mutex reason is recorded', () => {
    const onTwinPeakToggle = vi.fn();
    const r = render(
      withReasons(base(), { field: 'cwKeyer.twinPeak', code: 'mutually-exclusive-control' }),
      { onTwinPeakToggle },
    );
    const button = r.el('twin-peak-toggle')!;
    expect(button.hasAttribute('disabled')).toBe(true);
    expect(r.text('twin-peak-mutex')).toBe(MUTEX_LABEL.twinPeak);
    expect(r.text('twin-peak-mutex')).toContain('RTTY');
    press(button);
    flushSync();
    expect(onTwinPeakToggle).not.toHaveBeenCalled();
    r.dispose();
  });

  // Same for APF, whose mutex names CW/CW-R.
  it('disables APF and names CW when the mutex reason is recorded', () => {
    const onApfOn = vi.fn();
    const r = render(
      withReasons(base(), { field: 'cwKeyer.apf', code: 'mutually-exclusive-control' }),
      { onApfOn },
    );
    for (const [label] of APF_CHOICES) {
      expect(r.el(`apf-${label}`)!.hasAttribute('disabled')).toBe(true);
      press(r.el(`apf-${label}`)!);
    }
    flushSync();
    expect(onApfOn).not.toHaveBeenCalled();
    expect(r.text('apf-mutex')).toBe(MUTEX_LABEL.apf);
    expect(r.text('apf-mutex')).toContain('CW');
    r.dispose();
  });

  // Kills: a mutex lookup that matches the WRONG field — the entries are
  // dotted paths (MOR-1293 O1) and APF's must not disable TPF or vice versa.
  it('keeps the other control live when only one mutex reason is recorded', () => {
    const r = render(
      withReasons(base(), { field: 'cwKeyer.apf', code: 'mutually-exclusive-control' }),
    );
    expect(r.el('twin-peak-toggle')!.hasAttribute('disabled')).toBe(false);
    expect(r.el('twin-peak-mutex')).toBeNull();
    r.dispose();
  });

  it('shows no mutex explanation while neither reason is recorded', () => {
    const r = render(base());
    expect(r.el('apf-mutex')).toBeNull();
    expect(r.el('twin-peak-mutex')).toBeNull();
    r.dispose();
  });
});

/* ── facts render honestly ─────────────────────────────────────── */

describe('every unread fact renders honestly, never as a v2 default', () => {
  it.each(CW_LEVELS)('renders the %s fact verbatim on its raw wire scale', (field, _l, min, max) => {
    const r = render(withCw({ [field]: known(max) } as Partial<CwKeyerViewModel>));
    expect(r.input(field)!.valueAsNumber).toBe(max);
    expect([r.input(field)!.min, r.input(field)!.max]).toEqual([String(min), String(max)]);
    r.dispose();
  });

  // Kills: an unread level rendering as a number, and a thumb free to claim
  // any position (the MOR-1279 F1 / MOR-1304 F2 precedent).
  it.each(CW_LEVELS)('renders an unread %s as unknown and parks the thumb at min', (field, _l, min) => {
    const onLevelChange = vi.fn();
    const r = render(
      withCw({ [field]: unread<number>() } as Partial<CwKeyerViewModel>), { onLevelChange },
    );
    expect(r.text(`${field}-value`)).toContain(UNKNOWN_TEXT);
    expect(r.el(field)!.dataset.observed).toBe('false');
    expect(r.input(field)!.disabled).toBe(true);
    expect(r.input(field)!.valueAsNumber).toBe(min);
    slide(r.input(field)!, min + 1);
    flushSync();
    expect(onLevelChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it.each(CW_LEVELS)('emits the %s intent verbatim when observed', (field, _l, _min, max) => {
    const onLevelChange = vi.fn();
    const r = render(base(), { onLevelChange });
    slide(r.input(field)!, max);
    flushSync();
    expect(onLevelChange).toHaveBeenCalledExactlyOnceWith(field, max);
    r.dispose();
  });

  it.each(CW_LEVELS)('renders no %s block when it is structurally absent', (field) => {
    const r = render(withCw({ [field]: unread(OFF) } as Partial<CwKeyerViewModel>));
    expect(r.el(field)).toBeNull();
    r.dispose();
  });

  // Kills: `?? 0` on the APF ordinal, and a hardcoded APF domain. The contract
  // states an ordinal; the surface shows it verbatim and offers absolute
  // on/off over it (an `apfOn`/`apfType` fact is a deferred 9A question).
  it('renders the APF ordinal verbatim and checks on for any non-zero type', () => {
    for (const [value, expected] of [[0, 'off'], [1, 'on'], [3, 'on']] as const) {
      const r = render(withCw({ apf: known(value) }));
      expect(r.text('apf-value')).toBe(String(value));
      for (const [label] of APF_CHOICES) {
        expect(r.el(`apf-${label}`)!.getAttribute('aria-checked')).toBe(String(label === expected));
      }
      r.dispose();
    }
  });

  it('renders an unread APF ordinal as unknown with nothing checked', () => {
    const r = render(withCw({ apf: unread<number>(DEGRADED) }));
    expect(r.text('apf-value')).toBe(UNKNOWN_TEXT);
    for (const [label] of APF_CHOICES) {
      expect(r.el(`apf-${label}`)!.getAttribute('aria-checked')).toBe('false');
    }
    r.dispose();
  });

  it.each(APF_CHOICES)('emits the absolute APF %s intent', (label, on) => {
    const onApfOn = vi.fn();
    const r = render(base(), { onApfOn });
    press(r.el(`apf-${label}`)!);
    flushSync();
    expect(onApfOn).toHaveBeenCalledExactlyOnceWith(on);
    r.dispose();
  });

  it.each([
    ['apf', 'apf'], ['twinPeak', 'twin-peak'], ['reversePaddle', 'reverse-paddle'],
  ] as const)('renders no %s block when it is structurally absent', (field, id) => {
    const r = render(withCw({ [field]: unread(OFF) } as Partial<CwKeyerViewModel>));
    expect(r.el(id)).toBeNull();
    r.dispose();
  });

  it.each([
    ['twinPeak', 'twin-peak-toggle', 'onTwinPeakToggle'],
    ['reversePaddle', 'reverse-paddle', 'onReversePaddleToggle'],
  ] as const)('disables and refuses %s while unobserved', (field, id, handler) => {
    const spy = vi.fn();
    const r = render(
      withCw({ [field]: unread<boolean>(DEGRADED) } as Partial<CwKeyerViewModel>),
      { [handler]: spy },
    );
    expect(r.el(id)!.hasAttribute('disabled')).toBe(true);
    expect(r.el(id)!.hasAttribute('aria-pressed')).toBe(false);
    press(r.el(id)!);
    flushSync();
    expect(spy).not.toHaveBeenCalled();
    r.dispose();
  });

  it('emits the reverse-paddle intent when observed', () => {
    const onReversePaddleToggle = vi.fn();
    const r = render(base(), { onReversePaddleToggle });
    press(r.el('reverse-paddle')!);
    flushSync();
    expect(onReversePaddleToggle).toHaveBeenCalledTimes(1);
    r.dispose();
  });

  // Kills: an unknown state that is only a colour or an attribute (MOR-977).
  it('keeps every unknown distinguishable as TEXT', () => {
    const r = render(withCw({
      keyerSpeed: unread<number>(), pitchHz: unread<number>(), apf: unread<number>(),
    }));
    expect(r.root()!.textContent).toContain(UNKNOWN_TEXT);
    r.dispose();
  });
});

/* ── sidetone level is READ from txAux, never duplicated ───────── */

describe('sidetone level is txAux.monitorLevel — read there, not duplicated (MOR-1296 §4)', () => {
  // Kills: a second sidetone CONTROL, or a `cwKeyer.sidetoneLevel` fact. The
  // readout carries no input and no intent; the one control lives in
  // `TxAuxSurface`.
  it('shows the txAux monitor level as a READOUT with no control of its own', () => {
    const r = render(withTxAux(base()));
    expect(r.text('sidetone')).toBe('Sidetone level: 128');
    expect(r.el('sidetone')!.querySelector('input, button')).toBeNull();
    r.dispose();
  });

  it('shows no sidetone readout at all when the model carries no txAux group', () => {
    const r = render(base());
    expect(r.el('sidetone')).toBeNull();
    r.dispose();
  });

  // Kills: reintroducing the fact into the CW group.
  it('the cwKeyer group states no sidetone level of its own', () => {
    expect(Object.keys(base().cwKeyer!).sort()).toEqual([
      'apf', 'breakIn', 'breakInDelay', 'keyerSpeed', 'pitchHz', 'reversePaddle', 'twinPeak',
    ]);
  });
});
