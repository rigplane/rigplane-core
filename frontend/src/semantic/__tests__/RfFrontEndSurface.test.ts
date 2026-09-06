/**
 * MOR-1306 — the semantic RF-front-end surface (vocabulary slice 6B).
 *
 * Pins the four carry-forward rulings named in `RfFrontEndSurface.svelte`'s
 * file header:
 *   (1) freshness — a stale/unread field renders unknown, never a stale value;
 *   (2)+(3) the PREAMP/DIGI-SEL mutex renders as a disabled control WITH AN
 *       EXPLANATION, matched on the DOTTED `disabledReasons` field path, and
 *       disables the control even when the preamp field is otherwise usable;
 *   (4) the mutex explanation is keyed by the generic `DisabledReasonCode`,
 *       not by a peer-control name.
 *
 * Fast-pool-safe by construction (MOR-1272): no `vi.mock`, no global spy.
 */
import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { flushSync, mount, unmount } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import RfFrontEndSurface, {
  DISABLED_REASON_LABEL, RF_FRONT_END_LEVELS, RF_FRONT_END_TOGGLES, UNKNOWN_TEXT,
} from '../RfFrontEndSurface.svelte';
import { topologyFixtures, withRfFrontEnd } from '../fixtures/topologies';
import type {
  Availability, DisabledReason, RadioViewModel, RfFrontEndField, RfFrontEndViewModel,
} from '../radio-view-model';
import type { CommandFeedbackContinuousPairInput } from '../../primitives/scalar/continuous-pair.svelte';

const ON: Availability = { structural: true, operational: true };
const OFF: Availability = { structural: false, operational: false };
const DEGRADED: Availability = { structural: true, operational: false };

const base = (): RadioViewModel => withRfFrontEnd(topologyFixtures['1/single']);
/** Re-shape the rfFrontEnd group of an otherwise fully-observed fixture. */
const withRf = (over: Partial<RfFrontEndViewModel>): RadioViewModel => {
  const view = base();
  return { ...view, rfFrontEnd: { ...view.rfFrontEnd!, ...over } };
};
const withReasons = (reasons: readonly DisabledReason[]): RadioViewModel => ({
  ...base(), disabledReasons: reasons,
});
const unread = <T>(availability: Availability = ON): RfFrontEndField<T> =>
  ({ reading: { status: 'unknown' }, availability });
const known = <T>(value: T, availability: Availability = ON): RfFrontEndField<T> =>
  ({ reading: { status: 'known', value }, availability });
type PairFeedback = Readonly<Pick<CommandFeedbackContinuousPairInput, 'rf' | 'sql'>>;
const pairFeedback = (
  over: Partial<Record<'rf' | 'sql', Record<string, unknown>>> = {},
  providerGeneration = 3,
): PairFeedback => {
  const lane = (name: 'rf' | 'sql', confirmed: number) => ({
    command: name === 'rf' ? 'set_rf_gain' : 'set_squelch',
    feedback: {
      confirmed, target: null, requestedTarget: null, phase: 'idle' as const,
      busy: false, availability: 'available' as const, outcome: null,
      lifecycleId: null, transitionId: null, providerGeneration, sessionEpoch: 7,
      scope: { control: name === 'rf' ? 'rf-gain' : 'squelch', receiver: 0 as const },
      repeatPolicy: 'latest-target-wins' as const,
      ...over[name],
    },
  });
  return { rf: lane('rf', 0.5), sql: lane('sql', 0.2) };
};

let target: HTMLDivElement;

function render(view: RadioViewModel, handlers: Record<string, unknown> = {}) {
  target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(RfFrontEndSurface, { target, props: { view, ...handlers } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => { unmount(component); target.remove(); },
    root: () => q('[data-testid="rf-front-end-surface"]'),
    el: (id: string) => q<HTMLElement>(`[data-testid="rf-front-end-${id}"]`),
    text: (id: string) => q<HTMLElement>(`[data-testid="rf-front-end-${id}"]`)?.textContent?.trim(),
  };
}

/* ── the surface renders nothing without the group ─────────────── */

describe('the surface self-gates on the rfFrontEnd group', () => {
  it('renders NOTHING at all when the view model carries no rfFrontEnd group', () => {
    const view = { ...base() };
    delete (view as { rfFrontEnd?: unknown }).rfFrontEnd;
    const r = render(view);
    expect(r.root()).toBeNull();
    expect(target.textContent).toBe('');
    r.dispose();
  });

  it.each([
    ['preamp', 'preamp'], ['attenuator', 'attenuator'], ['rfGain', 'rfGain'], ['squelch', 'squelch'],
    ['digiSel', 'digiSel'], ['ipPlus', 'ipPlus'],
  ] as const)('renders no %s block at all when it is structurally absent', (field, id) => {
    const r = render(withRf({ [field]: unread(OFF) } as Partial<RfFrontEndViewModel>));
    expect(r.el(id)).toBeNull();
    r.dispose();
  });
});

/* ── (1) freshness: unknown is rendered as unknown, never stale-value ──── */

describe('carry-forward 1: a stale/unread reading renders unknown, never its last value', () => {
  it('renders a DEGRADED preamp as unknown text, not the last-known level', () => {
    const r = render(withRf({ preamp: unread<number>(DEGRADED) }));
    expect(r.el('preamp')!.dataset.observed).toBe('false');
    expect(r.text('preamp-value')).toBe(UNKNOWN_TEXT);
    r.dispose();
  });

  it('renders a DEGRADED attenuator as unknown text, not the last-known step', () => {
    const r = render(withRf({ attenuator: unread<number>(DEGRADED) }));
    expect(r.el('attenuator')!.dataset.observed).toBe('false');
    expect(r.text('attenuator-value')).toBe(UNKNOWN_TEXT);
    r.dispose();
  });

  it('renders a stale RF-gain reading as "?", never 0.5 or any prior level', () => {
    const r = render(withRf({ rfGain: unread<number>(DEGRADED) }));
    expect(r.text('rfGain')).toContain(UNKNOWN_TEXT);
    r.dispose();
  });

  it('makes the RF-gain slider inert while the level is unread, and emits nothing', () => {
    const onLevelChange = vi.fn();
    const r = render(withRf({ rfGain: unread<number>(DEGRADED) }), { onLevelChange });
    const input = r.el('rfGain')!.querySelector('input')!;
    expect(input.disabled).toBe(true);
    expect(input.valueAsNumber).toBe(0);
    input.value = '0.7';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('disables an unread preamp/attenuator choice and emits nothing on click', () => {
    const onPreampChange = vi.fn();
    const onAttenuatorChange = vi.fn();
    const r = render(
      withRf({ preamp: unread<number>(DEGRADED), attenuator: unread<number>(DEGRADED) }),
      { onPreampChange, onAttenuatorChange },
    );
    const preBtn = r.el('preamp-1')!;
    const attBtn = r.el('attenuator-6')!;
    expect(preBtn.hasAttribute('disabled')).toBe(true);
    expect(attBtn.hasAttribute('disabled')).toBe(true);
    preBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    attBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();
    expect(onPreampChange).not.toHaveBeenCalled();
    expect(onAttenuatorChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('keeps every radio unchecked for unread choices while exposing unknown output', () => {
    const r = render(
      withRf({ preamp: unread<number>(DEGRADED), attenuator: unread<number>(DEGRADED) }),
    );
    for (const value of [0, 1, 2]) expect(r.el(`preamp-${value}`)!.getAttribute('aria-checked')).toBe('false');
    for (const value of [0, 6, 12, 18]) expect(r.el(`attenuator-${value}`)!.getAttribute('aria-checked')).toBe('false');
    expect(r.text('preamp-value')).toBe(UNKNOWN_TEXT);
    expect(r.text('attenuator-value')).toBe(UNKNOWN_TEXT);
    r.dispose();
  });
});

/* ── (2)+(3) the PREAMP/DIGI-SEL mutex ──────────────────────────── */

describe('carry-forwards 2+3: the PREAMP mutex disables the control WITH AN EXPLANATION', () => {
  const MUTEX: DisabledReason = { field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' };

  it('disables every preamp choice while the mutex entry is present, even though preamp is usable', () => {
    const r = render(withReasons([MUTEX]));
    for (const value of [0, 1, 2]) expect(r.el(`preamp-${value}`)!.hasAttribute('disabled')).toBe(true);
    r.dispose();
  });

  it('shows the mutex explanation text, not merely an attribute', () => {
    const r = render(withReasons([MUTEX]));
    expect(r.text('preamp-mutex-reason')).toBe(DISABLED_REASON_LABEL['mutually-exclusive-control']);
    r.dispose();
  });

  it('carries the reason code as data, matched on the DOTTED field path', () => {
    const r = render(withReasons([MUTEX]));
    expect(r.el('preamp')!.dataset.disabledReason).toBe('mutually-exclusive-control');
    r.dispose();
  });

  // MUTATION KILLED (the `?? false` bypass, MOR-1293's forbidden shape): the
  // handler guard must refuse to emit even if `disabled` were somehow bypassed
  // (a programmatic dispatchEvent, exactly like a restyled design language).
  it('the handler itself refuses to emit while the mutex is active — independent of `disabled`', () => {
    const onPreampChange = vi.fn();
    const r = render(withReasons([MUTEX]), { onPreampChange });
    r.el('preamp-1')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();
    expect(onPreampChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('leaves preamp enabled and emits normally once the mutex entry is absent', () => {
    const onPreampChange = vi.fn();
    const r = render(base(), { onPreampChange });
    const btn = r.el('preamp-0')!;
    expect(btn.hasAttribute('disabled')).toBe(false);
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();
    expect(onPreampChange).toHaveBeenCalledExactlyOnceWith(0);
    expect(r.el('preamp-mutex-reason')).toBeNull();
    r.dispose();
  });

  it('never disables the attenuator, RF gain or squelch controls for the preamp mutex', () => {
    const r = render(withReasons([MUTEX]));
    expect(r.el('attenuator-6')!.hasAttribute('disabled')).toBe(false);
    expect(r.el('rfGain')!.querySelector('input')!.disabled).toBe(false);
    expect(r.el('squelch')!.querySelector('input')!.disabled).toBe(false);
    r.dispose();
  });
});

/* ── (4) the explanation is keyed by the generic code ───────────── */

describe('carry-forward 4: the mutex explanation names the code, never a peer control', () => {
  it('the label text never mentions DIGI-SEL', () => {
    const label = DISABLED_REASON_LABEL['mutually-exclusive-control']!;
    expect(label.toUpperCase()).not.toContain('DIGI-SEL');
    expect(label.toUpperCase()).not.toContain('DIGISEL');
  });

  it('is keyed by DisabledReasonCode, not by field name', () => {
    expect(Object.keys(DISABLED_REASON_LABEL)).toEqual(['mutually-exclusive-control']);
  });
});

/* ── choices, levels and toggles render and emit from the facts ────── */

describe('preamp and attenuator render as choice groups from the capability-derived sets', () => {
  it('checks exactly the observed preamp level', () => {
    const r = render(withRf({ preamp: known(2) }));
    for (const value of [0, 1, 2]) {
      expect(r.el(`preamp-${value}`)!.getAttribute('aria-checked')).toBe(String(value === 2));
    }
    expect(r.text('preamp-value')).toBe('2');
    r.dispose();
  });

  it('emits the clicked preamp level verbatim', () => {
    const onPreampChange = vi.fn();
    const r = render(base(), { onPreampChange });
    r.el('preamp-2')!.click();
    flushSync();
    expect(onPreampChange).toHaveBeenCalledExactlyOnceWith(2);
    r.dispose();
  });

  it('does not select an offered radio for a known out-of-offered preamp value', () => {
    const onPreampChange = vi.fn();
    const r = render(withRf({ preamp: known(3) }), { onPreampChange });
    for (const value of [0, 1, 2]) expect(r.el(`preamp-${value}`)!.getAttribute('aria-checked')).toBe('false');
    expect(r.text('preamp-value')).toBe('3');
    r.el('preamp-1')!.click();
    flushSync();
    expect(onPreampChange).toHaveBeenCalledExactlyOnceWith(1);
    expect(r.el('preamp-1')!.getAttribute('aria-checked')).toBe('false');
    r.dispose();
  });

  it('checks exactly the observed attenuator step and emits it verbatim', () => {
    const onAttenuatorChange = vi.fn();
    const r = render(withRf({ attenuator: known(12) }), { onAttenuatorChange });
    expect(r.el('attenuator-12')!.getAttribute('aria-checked')).toBe('true');
    r.el('attenuator-18')!.click();
    flushSync();
    expect(onAttenuatorChange).toHaveBeenCalledExactlyOnceWith(18);
    r.dispose();
  });
});

describe('RF gain and squelch render as 0..1 sliders, no rescale', () => {
  it.each(RF_FRONT_END_LEVELS)('declares the %s slider on the 0..1 scale', (field, _label, min, max) => {
    const r = render(base());
    const input = r.el(field)!.querySelector('input')!;
    expect([input.min, input.max]).toEqual([String(min), String(max)]);
    r.dispose();
  });

  it('emits the slider value verbatim, on the way out', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { onLevelChange });
    const input = r.el('squelch')!.querySelector('input')!;
    input.value = '0.33';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).toHaveBeenCalledExactlyOnceWith('squelch', 0.33);
    r.dispose();
  });

  // MUTATION KILLED: a guard that only lives in `disabled`.
  it('the level handler refuses to emit for an unusable field, independent of `disabled`', () => {
    const onLevelChange = vi.fn();
    const r = render(withRf({ squelch: unread<number>(DEGRADED) }), { onLevelChange });
    const input = r.el('squelch')!.querySelector('input')!;
    input.value = '0.5';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).not.toHaveBeenCalled();
    r.dispose();
  });

  // MOR-1447: the readout must format the known 0..1 fraction as a percent,
  // not the raw wire float — the live IC-7300 walkthrough regression this
  // pins reads `main.rfGain` back as the literal `0.8196078431372549`.
  it('renders a known RF-gain reading as a rounded percent, not the raw wire float', () => {
    const r = render(withRf({ rfGain: known(0.8196078431372549) }));
    expect(r.el('rfGain')!.dataset.feedbackIntegration).toBe('compatibility-reading');
    expect(r.el('rfGain')!.querySelector('input')!.getAttribute('feedback-policy'))
      .toBe('feedback-integrated');
    expect(r.text('rfGain')).toContain('82%');
    expect(r.text('rfGain')).not.toContain('0.8196078431372549');
    r.dispose();
  });

  it('renders a known squelch reading as a rounded percent too', () => {
    const r = render(withRf({ squelch: known(0.2) }));
    expect(r.text('squelch')).toContain('20%');
    r.dispose();
  });

  it('treats explicit null as unresolved for both separate controls without raw fallback', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { rfSqlFeedback: null, onLevelChange });
    for (const field of ['rfGain', 'squelch'] as const) {
      const group = r.el(field)!;
      const input = group.querySelector('input')!;
      expect(group.dataset.feedbackIntegration).toBe('authority-unresolved');
      expect(group.dataset.observed).toBe('false');
      expect(input.disabled).toBe(true);
      expect(group.querySelector('output')?.textContent).toBe(UNKNOWN_TEXT);
      input.value = '0.5';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    flushSync();
    expect(onLevelChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('keeps command-feedback values, phases, and eligibility independent per separate lane', () => {
    const onLevelChange = vi.fn();
    const feedback = pairFeedback({
      rf: {
        target: 0.75, requestedTarget: 0.75, phase: 'awaiting-confirmation', busy: true,
        lifecycleId: 'rf-pending', transitionId: 'rf-awaiting',
      },
      sql: {
        confirmed: null, availability: 'unavailable', phase: 'unavailable',
      },
    });
    const r = render(withRf({ rfGain: known(0.1), squelch: known(0.9) }), {
      rfSqlFeedback: feedback, onLevelChange,
    });
    const rfGroup = r.el('rfGain')!;
    const sqlGroup = r.el('squelch')!;
    const rfInput = rfGroup.querySelector('input')!;
    const sqlInput = sqlGroup.querySelector('input')!;
    expect(rfGroup.dataset.feedbackIntegration).toBe('command-feedback');
    expect(rfGroup.dataset.commandPhase).toBe('awaiting-confirmation');
    expect(rfGroup.getAttribute('aria-busy')).toBe('true');
    expect(rfGroup.dataset.observed).toBe('true');
    expect(rfInput.valueAsNumber).toBe(0.75);
    expect(rfGroup.querySelector('output')?.textContent).toBe('75%');
    expect(rfInput.disabled).toBe(false);
    expect(sqlGroup.dataset.commandPhase).toBe('unavailable');
    expect(sqlGroup.dataset.observed).toBe('false');
    expect(sqlGroup.querySelector('output')?.textContent).toBe(UNKNOWN_TEXT);
    expect(sqlInput.disabled).toBe(true);
    rfInput.value = '0.55';
    rfInput.dispatchEvent(new Event('input', { bubbles: true }));
    sqlInput.value = '0.4';
    sqlInput.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).toHaveBeenCalledExactlyOnceWith('rfGain', 0.55);
    r.dispose();
  });

  it('keeps the surviving separate lane eligible for a structurally incomplete combined profile', () => {
    const onLevelChange = vi.fn();
    const r = render(withRf({ squelch: unread(OFF) }), {
      controlModel: 'combined',
      rfSqlFeedback: pairFeedback({ rf: { target: 0.4, requestedTarget: 0.4 } }),
      onLevelChange,
    });
    expect(r.el('rf-sql')).toBeNull();
    expect(r.el('squelch')).toBeNull();
    const rfInput = r.el('rfGain')!.querySelector('input')!;
    expect(rfInput.disabled).toBe(false);
    expect(rfInput.valueAsNumber).toBe(0.4);
    rfInput.value = '0.3';
    rfInput.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).toHaveBeenCalledExactlyOnceWith('rfGain', 0.3);
    r.dispose();
  });

  it('invalidates separate drafts and announcements across provider and null authority replacement', () => {
    const feedback = new SvelteMap<string, PairFeedback>([['current', pairFeedback()]]);
    const onLevelChange = vi.fn();
    target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(RfFrontEndSurface, { target, props: {
      view: base(), onLevelChange,
      get rfSqlFeedback() { return feedback.get('current') ?? null; },
    } });
    flushSync();
    const input = target.querySelector<HTMLInputElement>('[data-testid="rf-front-end-rfGain"] input')!;
    input.value = '0.9';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(input.valueAsNumber).toBe(0.9);
    feedback.set('current', pairFeedback({ rf: { confirmed: 0.4 } }, 4));
    flushSync();
    expect(input.valueAsNumber).toBe(0.4);
    expect(target.querySelectorAll('[data-testid="rf-front-end-rfGain"] [data-control-feedback-status]')).toHaveLength(0);
    feedback.delete('current');
    flushSync();
    expect(input.disabled).toBe(true);
    expect(target.querySelector('[data-testid="rf-front-end-rfGain"] output')?.textContent).toBe(UNKNOWN_TEXT);
    unmount(component);
    target.remove();
  });

  it('retires only the represented lane draft and follows later same-authority canonical truth', () => {
    const feedback = new SvelteMap<string, PairFeedback>([['current', pairFeedback()]]);
    const onLevelChange = vi.fn();
    target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(RfFrontEndSurface, { target, props: {
      view: base(), onLevelChange,
      get rfSqlFeedback() { return feedback.get('current')!; },
    } });
    flushSync();
    const rfInput = target.querySelector<HTMLInputElement>('[data-testid="rf-front-end-rfGain"] input')!;
    const sqlInput = target.querySelector<HTMLInputElement>('[data-testid="rf-front-end-squelch"] input')!;
    rfInput.value = '0.7';
    rfInput.dispatchEvent(new Event('input', { bubbles: true }));
    sqlInput.value = '0.6';
    sqlInput.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).toHaveBeenCalledTimes(2);

    feedback.set('current', pairFeedback({
      rf: {
        target: 179 / 255, requestedTarget: 179 / 255, phase: 'awaiting-confirmation',
        busy: true, lifecycleId: 'rf-179', transitionId: 'rf-awaiting-179',
      },
    }));
    flushSync();
    expect(sqlInput.valueAsNumber).toBe(0.6);

    feedback.set('current', pairFeedback({
      rf: {
        confirmed: 179 / 255, requestedTarget: 179 / 255, phase: 'confirmed',
        lifecycleId: 'rf-179', transitionId: 'rf-confirmed-179',
        outcome: { phase: 'confirmed' },
      },
    }));
    flushSync();
    feedback.set('current', pairFeedback({ rf: { confirmed: 204 / 255 } }));
    flushSync();
    expect(rfInput.valueAsNumber).toBe(0.8);
    expect(target.querySelector('[data-testid="rf-front-end-rfGain"] output')?.textContent)
      .toBe('80%');
    expect(sqlInput.valueAsNumber).toBe(0.6);
    expect(onLevelChange).toHaveBeenCalledTimes(2);
    unmount(component);
    target.remove();
  });

  it('owns and destroys two independent native scalar bindings', () => {
    const source = readFileSync('src/semantic/RfFrontEndSurface.svelte', 'utf8');
    expect(source).toMatch(
      /const rfGainScalar = createContinuousScalar\([\s\S]*?'rfGain'[\s\S]*?nativeRangeContinuousScalarPolicy/,
    );
    expect(source).toMatch(
      /const squelchScalar = createContinuousScalar\([\s\S]*?'squelch'[\s\S]*?nativeRangeContinuousScalarPolicy/,
    );
    expect(source).toMatch(/rfGainScalar\.destroy\(\)/);
    expect(source).toMatch(/squelchScalar\.destroy\(\)/);
  });
});

/* ── MOR-1447 leg 2: the combined RF/SQL knob ────────────────────── */

describe('the combined RF/SQL knob (controlModel="combined")', () => {
  it('keeps its optional feedback prop primitive-shaped and free of runtime imports', () => {
    const source = readFileSync('src/semantic/RfFrontEndSurface.svelte', 'utf8');
    const props = source.slice(source.indexOf('interface Props'), source.indexOf('}: Props'));
    expect(props).toContain("Pick<CommandFeedbackContinuousPairInput, 'rf' | 'sql'>");
    expect(props).toMatch(/rfSqlFeedback\?:[\s\S]*\| null/);
    expect(source).not.toMatch(/from ['"]\$lib\/runtime|from ['"][^'"]*runtime\/adapters/);
  });

  it('renders one rf-sql control instead of the two separate sliders', () => {
    const r = render(base(), { controlModel: 'combined' });
    expect(r.el('rf-sql')).not.toBeNull();
    expect(r.el('rfGain')).toBeNull();
    expect(r.el('squelch')).toBeNull();
    r.dispose();
  });

  it('keeps the two separate sliders when controlModel is "separate" (default)', () => {
    const r = render(base());
    expect(r.el('rf-sql')).toBeNull();
    expect(r.el('rfGain')).not.toBeNull();
    expect(r.el('squelch')).not.toBeNull();
    r.dispose();
  });

  it('falls back to the two-slider rendering if either field is structurally absent, even when combined is declared', () => {
    const r = render(withRf({ squelch: unread(OFF) }), { controlModel: 'combined' });
    expect(r.el('rf-sql')).toBeNull();
    expect(r.el('rfGain')).not.toBeNull();
    expect(r.el('squelch')).toBeNull(); // squelch itself is absent, per its own structural gate
    r.dispose();
  });

  it('declares the combined slider on the 0..1 scale', () => {
    const r = render(base(), { controlModel: 'combined' });
    const input = r.el('rf-sql')!.querySelector('input')!;
    expect([input.min, input.max]).toEqual(['0', '1']);
    expect(input.getAttribute('feedback-policy')).toBe('feedback-integrated');
    r.dispose();
  });

  it('treats explicit null as unresolved integration with no raw fallback', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { controlModel: 'combined', rfSqlFeedback: null, onLevelChange });
    const group = r.el('rf-sql')!;
    const input = group.querySelector('input')!;
    expect(group.dataset.feedbackIntegration).toBe('authority-unresolved');
    expect(group.dataset.observed).toBe('false');
    expect(input.disabled).toBe(true);
    expect(r.text('rf-sql-rf-value')).toBe(UNKNOWN_TEXT);
    expect(r.text('rf-sql-sql-value')).toBe(UNKNOWN_TEXT);
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('uses command feedback for position, readout, observation, and request gating', () => {
    const onLevelChange = vi.fn();
    const r = render(withRf({ rfGain: unread(DEGRADED), squelch: unread(DEGRADED) }), {
      controlModel: 'combined', rfSqlFeedback: pairFeedback(), onLevelChange,
    });
    const group = r.el('rf-sql')!;
    const input = group.querySelector('input')!;
    expect(group.dataset.feedbackIntegration).toBe('command-feedback');
    expect(group.dataset.observed).toBe('true');
    expect(input.disabled).toBe(false);
    expect(input.valueAsNumber).toBeCloseTo(0.632, 3);
    expect(r.text('rf-sql-rf-value')).toBe('50%');
    expect(r.text('rf-sql-sql-value')).toBe('20%');
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange.mock.calls).toEqual([['rfGain', 1], ['squelch', 1]]);
    r.dispose();
  });

  it('renders independent lane phases, errors, busy state, and announcements', () => {
    const values = new SvelteMap<string, PairFeedback>([['feedback', pairFeedback()]]);
    target = document.createElement('div'); document.body.appendChild(target);
    const component = mount(RfFrontEndSurface, { target, props: {
      view: base(), controlModel: 'combined',
      get rfSqlFeedback() { return values.get('feedback')!; },
    } });
    flushSync();
    values.set('feedback', pairFeedback({
      rf: {
        phase: 'confirmed', requestedTarget: 0.5,
        outcome: { phase: 'confirmed' }, lifecycleId: 'rf-1', transitionId: 'rf-confirmed',
      },
      sql: {
        phase: 'failed', requestedTarget: 0.4, outcome: { phase: 'failed', error: 'denied' },
        lifecycleId: 'sql-1', transitionId: 'sql-failed',
      },
    }));
    flushSync();
    const group = target.querySelector<HTMLElement>('[data-testid="rf-front-end-rf-sql"]')!;
    expect(group.dataset.rfCommandPhase).toBe('confirmed');
    expect(group.dataset.sqlCommandPhase).toBe('failed');
    expect(group.getAttribute('aria-busy')).toBe('false');
    expect(group.querySelector('[data-testid="rf-front-end-rf-sql-rf-status"]')?.textContent).toContain('Confirmed');
    expect(group.querySelector('[data-testid="rf-front-end-rf-sql-sql-status"]')?.textContent).toContain('Failed');
    expect(group.querySelectorAll('[data-control-feedback-status]')).toHaveLength(2);
    expect(group.textContent).toContain('denied');
    unmount(component); target.remove();
  });

  it('keeps requested and last-confirmed lane values distinct while pending', () => {
    const feedback = pairFeedback({ rf: {
      target: 0.75, requestedTarget: 0.75, phase: 'awaiting-confirmation', busy: true,
      lifecycleId: 'rf-pending', transitionId: 'rf-awaiting',
    } });
    const r = render(base(), { controlModel: 'combined', rfSqlFeedback: feedback });
    expect(r.text('rf-sql-rf-value')).toBe('75%');
    expect(r.text('rf-sql-rf-status')).toContain('Awaiting confirmation 75%; confirmed 50%');
    expect(r.el('rf-sql')!.getAttribute('aria-busy')).toBe('true');
    r.dispose();
  });

  it('invalidates a local draft when provider authority is replaced', () => {
    const values = new SvelteMap<string, PairFeedback>([['feedback', pairFeedback()]]);
    target = document.createElement('div'); document.body.appendChild(target);
    const component = mount(RfFrontEndSurface, { target, props: {
      view: base(), controlModel: 'combined',
      get rfSqlFeedback() { return values.get('feedback')!; },
      onLevelChange: vi.fn(),
    } });
    flushSync();
    const input = target.querySelector<HTMLInputElement>('[data-testid="rf-front-end-rf-sql"] input')!;
    input.value = '1'; input.dispatchEvent(new Event('input', { bubbles: true })); flushSync();
    expect(input.valueAsNumber).toBe(1);
    values.set('feedback', pairFeedback({ rf: { confirmed: 1 }, sql: { confirmed: 0 } }, 4));
    flushSync();
    expect(input.valueAsNumber).toBe(0.5);
    unmount(component); target.remove();
  });

  // Per-field change guard (verifier follow-up R1, mirrors
  // `DualParamRenderer.svelte`'s `emitPair`): only a field whose mapped
  // value actually differs from its current confirmed reading emits. `base()`
  // starts at rfGain=known(1)/squelch=known(0) — the knob's own "center, at
  // rest" position — so each case below is chosen to isolate exactly ONE
  // field changing, mirroring the real single-knob write pattern instead of
  // spamming a redundant re-send of the field that didn't move.

  it('a hard-left drag emits ONLY rfGain — squelch is already at min, unchanged', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { controlModel: 'combined', onLevelChange });
    const input = r.el('rf-sql')!.querySelector('input')!;
    input.value = '0';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).toHaveBeenCalledExactlyOnceWith('rfGain', 0);
    r.dispose();
  });

  it('the knob center emits NOTHING — RF is already max and SQL already min, the "at rest" position', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { controlModel: 'combined', onLevelChange });
    const input = r.el('rf-sql')!.querySelector('input')!;
    input.value = '0.5';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).not.toHaveBeenCalled();
    r.dispose();
  });

  it('uses the pair binding to dispatch a canonical reversal after an unconfirmed request', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { controlModel: 'combined', onLevelChange });
    const input = r.el('rf-sql')!.querySelector('input')!;
    expect(input.dataset.pairEvidence).toBe('reading');
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.value = '0.5';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange.mock.calls).toEqual([
      ['squelch', 1],
      ['squelch', 0],
    ]);
    r.dispose();
  });

  it('clears identical lane draft and request state when receiver topology is replaced', () => {
    const views = new SvelteMap([['current', base()]]);
    const onLevelChange = vi.fn();
    target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(RfFrontEndSurface, {
      target,
      props: {
        get view() { return views.get('current')!; },
        controlModel: 'combined', onLevelChange,
      },
    });
    flushSync();
    const input = target.querySelector<HTMLInputElement>('[data-testid="rf-front-end-rf-sql"] input')!;
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(input.valueAsNumber).toBe(1);

    views.set('current', withRfFrontEnd(topologyFixtures['2/ab_shared']));
    flushSync();
    expect(input.valueAsNumber).toBe(0.5);
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange.mock.calls).toEqual([
      ['squelch', 1],
      ['squelch', 1],
    ]);
    unmount(component);
    target.remove();
  });

  it('a hard-right drag emits ONLY squelch — RF is already at max, unchanged (owner semantics: "hard right = SQL max (RF max)")', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { controlModel: 'combined', onLevelChange });
    const input = r.el('rf-sql')!.querySelector('input')!;
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).toHaveBeenCalledExactlyOnceWith('squelch', 1);
    r.dispose();
  });

  it('a leg-crossing drag emits BOTH — RF and SQL are both actually moving, unlike the single-leg cases above', () => {
    const onLevelChange = vi.fn();
    // Starts already on the right leg (RF max, SQL 0.5) instead of at rest,
    // then crosses to the left leg — both halves genuinely change value.
    const r = render(withRf({ rfGain: known(1), squelch: known(0.5) }), {
      controlModel: 'combined', onLevelChange,
    });
    const input = r.el('rf-sql')!.querySelector('input')!;
    input.value = '0.23';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).toHaveBeenCalledTimes(2);
    expect(onLevelChange).toHaveBeenCalledWith('rfGain', 0.5);
    expect(onLevelChange).toHaveBeenCalledWith('squelch', 0);
    r.dispose();
  });

  // Readback projection: SQL known above min projects the knob to the right
  // leg (RF forced to max) — the physical knob cannot express "RF below max
  // AND SQL above min" simultaneously, so this is the one honest reading.
  it('positions the knob on the right leg when SQL reads above min', () => {
    const r = render(withRf({ rfGain: known(0.8196078431372549), squelch: known(0.2) }), {
      controlModel: 'combined',
    });
    const input = r.el('rf-sql')!.querySelector('input')!;
    expect(input.valueAsNumber).toBeCloseTo(0.632, 3);
    r.dispose();
  });

  // Readback projection: RF known below max with SQL at min projects to the
  // left leg.
  it('positions the knob on the left leg when RF reads below max and SQL is at min', () => {
    const r = render(withRf({ rfGain: known(0.5), squelch: known(0) }), { controlModel: 'combined' });
    const input = r.el('rf-sql')!.querySelector('input')!;
    expect(input.valueAsNumber).toBeCloseTo(0.23, 3);
    r.dispose();
  });

  it('shows a combined RF/SQL readout formatted as two percentages', () => {
    const r = render(withRf({ rfGain: known(0.5), squelch: known(0.2) }), { controlModel: 'combined' });
    expect(r.text('rf-sql')).toContain('50%');
    expect(r.text('rf-sql')).toContain('20%');
    r.dispose();
  });

  it('disables the combined slider and emits nothing while squelch is unusable', () => {
    const onLevelChange = vi.fn();
    const r = render(withRf({ squelch: unread<number>(DEGRADED) }), {
      controlModel: 'combined', onLevelChange,
    });
    const input = r.el('rf-sql')!.querySelector('input')!;
    expect(input.disabled).toBe(true);
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).not.toHaveBeenCalled();
    r.dispose();
  });

  // Verifier follow-up R2: the mirror of the case above. A mutation that
  // dropped ONLY the `!usable(rf.rfGain)` half of the guard (leaving
  // `!usable(rf.squelch)` in place) would still pass the test above — it
  // needs this independent mirror to be killed.
  it('disables the combined slider and emits nothing while rfGain is unusable', () => {
    const onLevelChange = vi.fn();
    const r = render(withRf({ rfGain: unread<number>(DEGRADED) }), {
      controlModel: 'combined', onLevelChange,
    });
    const input = r.el('rf-sql')!.querySelector('input')!;
    expect(input.disabled).toBe(true);
    input.value = '0';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).not.toHaveBeenCalled();
    r.dispose();
  });
});

/* ── MOR-1441 leg 2: the preamp pending-target affordance ───────────── */

describe('pending-target affordance (MOR-1441 leg 2)', () => {
  it('marks only the pending choice, leaves the CONFIRMED choice checked, and marks the group', () => {
    const r = render(withRf({ preamp: known(0) }), { pendingPreamp: 2 });
    const group = r.el('preamp')!;
    expect(group.dataset.preampStatus).toBe('pending');
    expect(r.el('preamp-0')!.getAttribute('aria-checked')).toBe('true');
    expect(r.el('preamp-0')!.dataset.pending).toBe('false');
    expect(r.el('preamp-2')!.getAttribute('aria-checked')).toBe('false');
    expect(r.el('preamp-2')!.dataset.pending).toBe('true');
    r.dispose();
  });

  it('renders confirmed status and no pending marker when nothing is pending', () => {
    const r = render(base());
    const group = r.el('preamp')!;
    expect(group.dataset.preampStatus).toBe('confirmed');
    for (const value of [0, 1, 2]) expect(r.el(`preamp-${value}`)!.dataset.pending).toBe('false');
    r.dispose();
  });

  it('renders a screen-reader announcement only while pending', () => {
    const pending = render(base(), { pendingPreamp: 1 });
    expect(pending.el('preamp')!.querySelector('.sr-only')).not.toBeNull();
    pending.dispose();
    const confirmed = render(base());
    expect(confirmed.el('preamp')!.querySelector('.sr-only')).toBeNull();
    confirmed.dispose();
  });

  // THE seam test (MOR-1441 leg-1 lesson applied to a discrete control): a
  // click while a DIFFERENT preamp level is pending must still dispatch the
  // CLICKED value verbatim — never something read off the pending display,
  // and never suppressed by the mutex/change-guard machinery above (which
  // reads only confirmed fields, untouched by this leg).
  it('SEAM: clicking a choice while a DIFFERENT level is pending emits the CLICKED value, unaffected by pending', () => {
    const onPreampChange = vi.fn();
    const r = render(base(), { onPreampChange, pendingPreamp: 2 });
    r.el('preamp-1')!.click();
    flushSync();
    expect(onPreampChange).toHaveBeenCalledExactlyOnceWith(1);
    r.dispose();
  });

  it('SEAM: clicking the PENDING choice itself still emits it explicitly, never suppressed', () => {
    const onPreampChange = vi.fn();
    const r = render(base(), { onPreampChange, pendingPreamp: 2 });
    r.el('preamp-2')!.click();
    flushSync();
    expect(onPreampChange).toHaveBeenCalledExactlyOnceWith(2);
    r.dispose();
  });
});

describe('DIGI-SEL and IP+ render as toggles and emit the FLIPPED value', () => {
  it.each(RF_FRONT_END_TOGGLES)('shows the observed %s state as pressed/unpressed', (field) => {
    const r = render(withRf({ [field]: known(true) } as Partial<RfFrontEndViewModel>));
    expect(r.el(field)!.getAttribute('aria-pressed')).toBe('true');
    r.dispose();
  });

  it('emits the flipped value on click, computed from the observed reading', () => {
    const onToggle = vi.fn();
    const r = render(withRf({ digiSel: known(false) }), { onToggle });
    r.el('digiSel')!.click();
    flushSync();
    expect(onToggle).toHaveBeenCalledExactlyOnceWith('digiSel', true);
    r.dispose();
  });

  it('flips the other direction too', () => {
    const onToggle = vi.fn();
    const r = render(withRf({ ipPlus: known(true) }), { onToggle });
    r.el('ipPlus')!.click();
    flushSync();
    expect(onToggle).toHaveBeenCalledExactlyOnceWith('ipPlus', false);
    r.dispose();
  });

  // MUTATION KILLED: a guard that only lives in `disabled`.
  it('the toggle handler refuses to emit for an unusable field, independent of `disabled`', () => {
    const onToggle = vi.fn();
    const r = render(withRf({ digiSel: unread<boolean>(DEGRADED) }), { onToggle });
    r.el('digiSel')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();
    expect(onToggle).not.toHaveBeenCalled();
    r.dispose();
  });
});
