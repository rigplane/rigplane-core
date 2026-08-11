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
import { flushSync, mount, unmount } from 'svelte';
import RfFrontEndSurface, {
  DISABLED_REASON_LABEL, RF_FRONT_END_LEVELS, RF_FRONT_END_TOGGLES, UNKNOWN_TEXT,
} from '../RfFrontEndSurface.svelte';
import { topologyFixtures, withRfFrontEnd } from '../fixtures/topologies';
import type {
  Availability, DisabledReason, RadioViewModel, RfFrontEndField, RfFrontEndViewModel,
} from '../radio-view-model';

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
    expect(r.text('rfGain')).toContain('82%');
    expect(r.text('rfGain')).not.toContain('0.8196078431372549');
    r.dispose();
  });

  it('renders a known squelch reading as a rounded percent too', () => {
    const r = render(withRf({ squelch: known(0.2) }));
    expect(r.text('squelch')).toContain('20%');
    r.dispose();
  });
});

/* ── MOR-1447 leg 2: the combined RF/SQL knob ────────────────────── */

describe('the combined RF/SQL knob (controlModel="combined")', () => {
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
    r.dispose();
  });

  it('emits BOTH rfGain and squelch on a hard-left drag: RF min, SQL min', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { controlModel: 'combined', onLevelChange });
    const input = r.el('rf-sql')!.querySelector('input')!;
    input.value = '0';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).toHaveBeenCalledTimes(2);
    expect(onLevelChange).toHaveBeenNthCalledWith(1, 'rfGain', 0);
    expect(onLevelChange).toHaveBeenNthCalledWith(2, 'squelch', 0);
    r.dispose();
  });

  it('emits RF max / SQL min at the knob center (the dead zone default)', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { controlModel: 'combined', onLevelChange });
    const input = r.el('rf-sql')!.querySelector('input')!;
    input.value = '0.5';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).toHaveBeenNthCalledWith(1, 'rfGain', 1);
    expect(onLevelChange).toHaveBeenNthCalledWith(2, 'squelch', 0);
    r.dispose();
  });

  it('emits SQL max / RF max on a hard-right drag — owner semantics: "hard right = SQL max (RF max)"', () => {
    const onLevelChange = vi.fn();
    const r = render(base(), { controlModel: 'combined', onLevelChange });
    const input = r.el('rf-sql')!.querySelector('input')!;
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(onLevelChange).toHaveBeenNthCalledWith(1, 'rfGain', 1);
    expect(onLevelChange).toHaveBeenNthCalledWith(2, 'squelch', 1);
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

  it('disables the combined slider and emits nothing while either field is unusable', () => {
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
