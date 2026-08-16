/**
 * MOR-1310 — the semantic CW-keyer surface (vocabulary slice 9B).
 *
 * SAFETY-CRITICAL. Break-in KEYS THE TRANSMITTER, so every test below names
 * the mutation it kills:
 *   (a) the surface becoming a second key path — it must emit SETTING intents
 *       only, and never a key/unkey or a transmit-causing action; RX frequency
 *       correction remains a non-TX setting intent, while exactly one
 *       `<RxTxSurface>` stays the authority
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
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import CwKeyerSurface, {
  APF_CHOICES, BREAK_IN_CHOICES, BREAK_IN_REASON_KEY, CW_LEVELS, MUTEX_LABEL, POSTURE_LABEL,
  UNKNOWN_TEXT, breakInBlockedLabel, breakInPosture, type CwLevelField,
} from '../CwKeyerSurface.svelte';
import { topologyFixtures, withCwKeyer, withTxAux } from '../fixtures/topologies';
import type {
  Availability, BreakInMode, CwKeyerField, CwKeyerViewModel, DisabledReason, RadioViewModel,
} from '../radio-view-model';
import { t } from '$lib/i18n';
import type {
  ControlFeedbackPresentationInput, PresentationPhase,
} from '../../primitives/control-feedback/control-feedback-presentation';

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
  breakInDelayFeedback?: Readonly<ControlFeedbackPresentationInput<number>>;
  onAutoTune?: () => void;
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

function renderReactiveFeedback(initial: ControlFeedbackPresentationInput<number>) {
  const onLevelChange = vi.fn();
  const props = proxy({ view: base(), onLevelChange, breakInDelayFeedback: initial });
  const component = mount(CwKeyerSurface, { target, props });
  flushSync();
  const input = () => target.querySelector<HTMLInputElement>(
    '[data-testid="cw-keyer-breakInDelay"] input',
  )!;
  const output = () => target.querySelector<HTMLElement>(
    '[data-testid="cw-keyer-breakInDelay-value"]',
  )!;
  return { dispose: () => unmount(component), input, output, onLevelChange, props };
}

/** A real click, not `.click()`: `.click()` is a no-op on a disabled button, so
 *  it can never tell a `disabled` attribute apart from a handler guard. */
const press = (el: HTMLElement) => el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
const slide = (el: HTMLInputElement, value: number) => {
  el.value = String(value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
};
const feedback = (
  phase: PresentationPhase,
  over: Partial<ControlFeedbackPresentationInput<number>> = {},
): Readonly<ControlFeedbackPresentationInput<number>> => ({
  confirmed: 64, target: null, requestedTarget: null, phase,
  transitionId: null, outcome: null, ...over,
});

/* ── (a) the surface is not a key path ────────────────────────── */

describe('the CW-keyer surface is NOT a key path (decomposition R9)', () => {
  /** The whole static import closure of the file, allow-listed. Kills: adding
   *  ANY import that could reach the TX controller, the transport or the
   *  permit utility — including through a relative specifier.
   *  `./pressed-of` (MOR-1358) is allow-listed alongside the fact contract:
   *  it is a pure, dependency-free `aria-pressed` derivation shared with
   *  four sibling surfaces, itself importing only a TYPE from
   *  `./radio-view-model` — it cannot reach the TX controller, the
   *  transport or the permit utility any more than the fact contract can.
   *  This check regexes THIS file's specifiers only, so that premise is
   *  pinned one level down by `pressed-of.test.ts`'s `'has no runtime
   *  import'` case (verify-MOR-1358 F1) — the two together are the closure. */
  it('imports nothing but the fact contract and the shared pressedOf helper', () => {
    const specifiers = [...CODE.matchAll(/from\s+'([^']+)'/g)].map((m) => m[1]);
    expect(specifiers.length).toBeGreaterThan(0);
    // MOR-1474: `$lib/i18n` added for the operator-legible `t()` catalog
    // lookups — the SAME allow-listed addition MOR-1448 made to
    // BandSurface's identical closure guard. It is a pure string-resolution
    // module (no transport, no controller, no permit utility) and cannot
    // widen this file's reach any more than `./pressed-of` does.
    expect([...new Set(specifiers)]).toEqual([
      '$lib/i18n', './radio-view-model', './pressed-of',
      '../primitives/control-feedback/control-feedback-presentation',
    ]);
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
      'TxAuthoritySnapshot', 'keyBlockedReasons', 'getFrequencyPermit', 'txBands', 'sendCommand',
      '$lib/transport', '$lib/utils/tx-permit',
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
      'onReversePaddleToggle', 'breakInDelayFeedback', 'autoTuneAvailable', 'onAutoTune',
    ]);
  });

  it('renders an available RX frequency-correction control and emits exactly once', () => {
    const onAutoTune = vi.fn();
    const component = mount(CwKeyerSurface, {
      target,
      props: { view: base(), autoTuneAvailable: true, onAutoTune },
    });
    flushSync();
    const control = target.querySelector<HTMLElement>('[data-testid="cw-keyer-auto-tune"]');
    expect(control).not.toBeNull();
    expect(control!.textContent).toContain('RX frequency correction');
    press(control!);
    expect(onAutoTune).toHaveBeenCalledExactlyOnceWith();
    unmount(component);
  });

  it('omits the RX frequency-correction control when unavailable or callback-free', () => {
    const unavailableCallback = vi.fn();
    const unavailable = mount(CwKeyerSurface, {
      target,
      props: { view: base(), autoTuneAvailable: false, onAutoTune: unavailableCallback },
    });
    flushSync();
    expect(target.querySelector('[data-testid="cw-keyer-auto-tune"]')).toBeNull();
    expect(unavailableCallback).not.toHaveBeenCalled();
    unmount(unavailable);

    const callbackFree = mount(CwKeyerSurface, {
      target,
      props: { view: base(), autoTuneAvailable: true },
    });
    flushSync();
    const control = target.querySelector<HTMLElement>('[data-testid="cw-keyer-auto-tune"]');
    expect(control).not.toBeNull();
    expect(() => press(control!)).not.toThrow();
    unmount(callbackFree);
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

/* ── MOR-1648/MOR-1753 — truthful draft and feedback ─────────── */

describe('Break-in Delay separates draft, submitted target and confirmed truth', () => {
  it('shows a local draft, then emits one bounded integer and presents the submitted target', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { onLevelChange, breakInDelayFeedback: feedback('idle') });
    const input = r.input('breakInDelay')!;
    for (const value of [80, 96, 111]) {
      input.value = String(value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    expect(onLevelChange).not.toHaveBeenCalled();
    flushSync();
    expect(input.value).toBe('111');
    expect(r.text('breakInDelay-value')).toContain('111 draft');
    input.dispatchEvent(new Event('change', { bubbles: true }));
    expect(onLevelChange).toHaveBeenCalledExactlyOnceWith('breakInDelay', 111);
    flushSync();
    expect(input.value).toBe('64');
    r.dispose();
  });

  it.each([
    ['submitted', true, 111, null],
    ['awaiting-confirmation', true, 111, null],
    ['failed', false, 64, 'failed'],
    ['timed-out', false, 64, 'timed-out'],
    ['cancelled', false, 64, 'cancelled'],
    ['superseded', false, 64, 'superseded'],
    ['confirmed', false, 111, 'confirmed'],
  ] as const)('projects %s without presenting an unconfirmed target as canonical', (
    phase, busy, displayed, outcome,
  ) => {
    const terminal = outcome === null ? null : { phase: outcome };
    const r = render(base(), {
      breakInDelayFeedback: feedback(phase, {
        confirmed: phase === 'confirmed' ? 111 : 64,
        target: busy ? 111 : null,
        requestedTarget: 111,
        transitionId: `[1,"command","${phase}"]`,
        outcome: terminal,
      }),
    });
    const input = r.input('breakInDelay')!;
    expect(input.value).toBe(String(displayed));
    expect(input.dataset.commandPhase).toBe(phase);
    expect(input.getAttribute('aria-busy')).toBe(String(busy));
    expect(r.text('breakInDelay-value')).toContain(`${displayed} ${phase.replaceAll('-', ' ')}`);
    const live = target.querySelector('[data-control-feedback-status]');
    expect(live?.getAttribute('aria-live')).toBe('polite');
    expect(live?.textContent).toContain('111');
    r.dispose();
  });

  it('fails closed when feedback is unavailable and Escape cancels a draft without dispatch', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { onLevelChange, breakInDelayFeedback: feedback('idle') });
    const input = r.input('breakInDelay')!;
    input.value = '99';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    expect(onLevelChange).not.toHaveBeenCalled();
    expect(input.value).toBe('64');

    r.dispose();

    const unavailable = render(base(), {
      onLevelChange, breakInDelayFeedback: feedback('unavailable', { confirmed: null }),
    });
    const unavailableInput = unavailable.input('breakInDelay')!;
    expect(unavailableInput.disabled).toBe(true);
    expect(unavailableInput.dataset.commandPhase).toBe('unavailable');
    expect(unavailableInput.getAttribute('aria-valuetext')).toBe('Break-in delay unavailable');
    expect(unavailable.text('breakInDelay-value')).toContain('— unavailable');
    unavailable.dispose();
  });

  it('rounds and clamps a programmatic final candidate before dispatch', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { onLevelChange });
    const input = r.input('breakInDelay')!;
    Object.defineProperty(input, 'valueAsNumber', { configurable: true, value: 300.4 });
    input.dispatchEvent(new Event('change', { bubbles: true }));
    expect(onLevelChange).toHaveBeenCalledExactlyOnceWith('breakInDelay', 255);
    r.dispose();
  });

  it('cancels a gesture and suppresses its trailing change', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { onLevelChange });
    const input = r.input('breakInDelay')!;
    input.value = '111';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('pointercancel', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    expect(onLevelChange).not.toHaveBeenCalled();
    expect(input.value).toBe('64');
    r.dispose();
  });

  it('emits nothing when unmounted after intermediate drag input', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { onLevelChange });
    const input = r.input('breakInDelay')!;
    input.value = '111';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    r.dispose();
    expect(onLevelChange).not.toHaveBeenCalled();
  });

  it.each([
    ['cancelled', 72, 'provider-replaced'],
    ['failed', 73, 'failed-transition'],
    ['timed-out', 74, 'timed-out-transition'],
    ['superseded', 75, 'superseded-transition'],
    ['idle', 76, null],
  ] as const)('invalidates a stale draft when authority advances to %s', (phase, canonical, id) => {
    const r = renderReactiveFeedback(feedback('idle'));
    r.input().value = '111';
    r.input().dispatchEvent(new Event('input', { bubbles: true }));
    const terminal = phase === 'idle' ? null : { phase };
    r.props.breakInDelayFeedback = feedback(phase, {
      confirmed: canonical, requestedTarget: phase === 'idle' ? null : 111,
      transitionId: id, outcome: terminal,
    });
    flushSync();
    expect(r.input().value).toBe(String(canonical));
    expect(r.output().textContent).toContain(`${canonical} ${phase.replaceAll('-', ' ')}`);
    r.input().dispatchEvent(new Event('change', { bubbles: true }));
    expect(r.onLevelChange).not.toHaveBeenCalled();

    r.input().value = '99';
    r.input().dispatchEvent(new Event('input', { bubbles: true }));
    r.input().dispatchEvent(new Event('change', { bubbles: true }));
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('breakInDelay', 99);
    r.dispose();
  });

  it('preserves an active draft while the same feedback authority is re-projected', () => {
    const current = feedback('awaiting-confirmation', {
      target: 80, requestedTarget: 80, transitionId: 'authority-a',
    });
    const r = renderReactiveFeedback(current);
    r.input().value = '111';
    r.input().dispatchEvent(new Event('input', { bubbles: true }));
    r.props.breakInDelayFeedback = { ...current };
    flushSync();
    expect(r.input().value).toBe('111');
    r.input().dispatchEvent(new Event('change', { bubbles: true }));
    expect(r.onLevelChange).toHaveBeenCalledExactlyOnceWith('breakInDelay', 111);
    r.dispose();
  });

  it.each([
    ['missing', null], ['not-a-number', Number.NaN], ['infinite', Number.POSITIVE_INFINITY],
    ['below-domain', -1], ['above-domain', 256], ['off-lattice', 64.5],
  ] as const)('fails closed for %s canonical truth', (_case, confirmed) => {
    const r = renderReactiveFeedback(feedback('idle', { confirmed }));
    expect(r.input().disabled).toBe(true);
    expect(r.input().hasAttribute('aria-valuenow')).toBe(false);
    expect(r.input().getAttribute('aria-valuetext')).toBe('Break-in delay unavailable');
    expect(r.output().textContent).toContain('— unavailable');
    r.input().value = '99';
    r.input().dispatchEvent(new Event('input', { bubbles: true }));
    r.input().dispatchEvent(new Event('change', { bubbles: true }));
    expect(r.onLevelChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it.each([
    ['non-finite target', feedback('submitted', {
      target: Number.NaN, requestedTarget: 111, transitionId: 'bad-target',
    })],
    ['mismatched terminal outcome', feedback('failed', {
      requestedTarget: 111, transitionId: 'bad-outcome', outcome: { phase: 'confirmed' },
    })],
  ])('fails closed for %s', (_case, malformed) => {
    const r = renderReactiveFeedback(malformed);
    expect(r.input().disabled).toBe(true);
    expect(r.input().hasAttribute('aria-valuenow')).toBe(false);
    expect(r.output().textContent).toContain('— unavailable');
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
    expect(blocked.textContent!.trim()).toBe(breakInBlockedLabel(code));
    r.dispose();
  });

  // Kills: a label map that drifts from the three codes the adapter can emit
  // for this field (`deriveCwKeyerReasons`).
  it('has a label for each of the three codes the CW break-in gate can record', () => {
    expect(Object.keys(BREAK_IN_REASON_KEY).sort())
      .toEqual(['capability-unavailable', 'out-of-band', 'tx-target-unknown']);
  });

  /* ── MOR-1474: operator-legible wording, reusing the MOR-1448 per-status
     catalog keys for the SAME `txPermit`-sourced fact this field's
     `deriveCwKeyerReasons` reads. ──────────────────────────────────────── */
  describe('MOR-1474 — break-in block reason resolves through the shared TX-reason catalog', () => {
    it.each([
      ['out-of-band', 'core.band.tx.reason.outOfBand'],
      ['capability-unavailable', 'core.band.tx.reason.rangesNotConfigured'],
      ['tx-target-unknown', 'core.band.tx.reason.targetUnknown'],
    ] as const)('composes code %s through core.cwKeyer.breakIn.blocked with reason key %s', (code, key) => {
      expect(breakInBlockedLabel(code)).toBe(
        t('core.cwKeyer.breakIn.blocked', { reason: t(key) }),
      );
    });

    it('never leaks the raw disabledReasonCode into the rendered text', () => {
      for (const code of ['out-of-band', 'capability-unavailable', 'tx-target-unknown'] as const) {
        expect(breakInBlockedLabel(code)).not.toContain(code);
      }
    });
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
