import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';

const baseProps = {
  rfGain: 255,
  squelch: 0,
  att: 0,
  pre: 0,
  digiSel: false,
  ipPlus: false,
  rfGainAvailable: true,
  squelchAvailable: true,
  attAvailable: true,
  preAvailable: true,
  digiSelAvailable: true,
  ipPlusAvailable: true,
  attValues: [0, 6, 12],
  attLabels: {} as Record<string, string>,
  preValues: [0, 1, 2],
  preOptions: [
    { value: 0, label: 'OFF' },
    { value: 1, label: 'P1' },
    { value: 2, label: 'P2' },
  ],
  showRfGain: true,
  showSquelch: true,
  showAtt: true,
  showPre: true,
  preDisabled: false,
  preDisabledReason: '',
  showDigiSel: true,
  showIpPlus: true,
};

const propsState = new SvelteMap([['value', { ...baseProps }]]);

const feedback = (
  control: 'rf-gain' | 'squelch',
  confirmed: number | null,
  over: Record<string, unknown> = {},
) => ({
  confirmed, target: null, requestedTarget: null, phase: 'idle' as const, busy: false,
  availability: 'available' as const, outcome: null, lifecycleId: null, transitionId: null,
  providerGeneration: 3, sessionEpoch: 7, scope: { control, receiver: 0 as const },
  repeatPolicy: 'latest-target-wins' as const, ...over,
});
const availableFeedback = () => ({
  rf: { command: 'set_rf_gain', feedback: feedback('rf-gain', 0.5) },
  sql: { command: 'set_squelch', feedback: feedback('squelch', 0.2) },
});
const feedbackState = new SvelteMap<string, ReturnType<typeof availableFeedback> | null>([
  ['value', availableFeedback()],
]);

const mockHandlers = {
  onRfGainChange: vi.fn(),
  onSquelchChange: vi.fn(),
  onAttChange: vi.fn(),
  onPreChange: vi.fn(),
  onDigiSelToggle: vi.fn(),
  onIpPlusToggle: vi.fn(),
};

// MOR-1536: RfFrontEnd now also reads the preamp/attenuator armed signals —
// default unarmed here, this file's tests are not about that behavior
// (covered by `mor1536-armed-adoption.test.ts`).
const unarmed = { armed: false, value: null };

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveRfFrontEndProps: () => propsState.get('value')!,
  getRfFrontEndHandlers: () => mockHandlers,
  getRfSqlControlFeedback: () => feedbackState.get('value')!,
  getPreampArmed: () => unarmed,
  getAttenuatorArmed: () => unarmed,
}));

import RfFrontEnd from '../RfFrontEnd.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountPanel(overrides?: Partial<typeof baseProps>) {
  propsState.set('value', { ...baseProps, ...overrides });
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(RfFrontEnd, { target: t });
  flushSync();
  components.push(component);
  return t;
}

function findPreButton(t: HTMLElement, label: string): HTMLButtonElement {
  const buttons = Array.from(t.querySelectorAll('button')) as HTMLButtonElement[];
  const match = buttons.find((b) => b.textContent?.trim() === label);
  if (!match) throw new Error(`PRE button "${label}" not found`);
  return match;
}

beforeEach(() => {
  components = [];
  propsState.set('value', { ...baseProps });
  feedbackState.set('value', availableFeedback());
  mockHandlers.onRfGainChange = vi.fn();
  mockHandlers.onSquelchChange = vi.fn();
  mockHandlers.onAttChange = vi.fn();
  mockHandlers.onPreChange = vi.fn();
  mockHandlers.onDigiSelToggle = vi.fn();
  mockHandlers.onIpPlusToggle = vi.fn();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('RfFrontEnd preamp/digisel mutex (component)', () => {
  it('disables the PRE buttons and sends no preamp change when DIGI-SEL is on', () => {
    const t = mountPanel({ showPre: true, preDisabled: true, pre: 0 });
    const p1 = findPreButton(t, 'P1');

    expect(p1.disabled).toBe(true);

    p1.click();
    flushSync();

    expect(mockHandlers.onPreChange).not.toHaveBeenCalled();
  });

  it('enables the PRE buttons and forwards the preamp change when DIGI-SEL is off', () => {
    const t = mountPanel({ showPre: true, preDisabled: false, pre: 0 });
    const p1 = findPreButton(t, 'P1');

    expect(p1.disabled).toBe(false);

    p1.click();
    flushSync();

    expect(mockHandlers.onPreChange).toHaveBeenCalledWith(1);
  });
});

describe('RfFrontEnd RF/SQL feedback bindings', () => {
  function slider(target: HTMLElement): HTMLElement {
    return target.querySelector('[role="slider"]') as HTMLElement;
  }

  it('mounts the combined pair owner and preserves RF-before-SQL gesture order', () => {
    feedbackState.set('value', {
      rf: { command: 'set_rf_gain', feedback: feedback('rf-gain', 1) },
      sql: { command: 'set_squelch', feedback: feedback('squelch', 0) },
    });
    const target = mountPanel({ showRfGain: true, showSquelch: true });
    const control = slider(target) as HTMLElement & {
      setPointerCapture(id: number): void;
      hasPointerCapture(id: number): boolean;
      releasePointerCapture(id: number): void;
    };
    control.setPointerCapture = vi.fn();
    control.hasPointerCapture = vi.fn(() => true);
    control.releasePointerCapture = vi.fn();
    vi.spyOn(target.querySelector('.vc-dual') as HTMLElement, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, width: 100 } as DOMRect);
    const order: string[] = [];
    mockHandlers.onRfGainChange.mockImplementation(value => order.push(`rf:${value}`));
    mockHandlers.onSquelchChange.mockImplementation(value => order.push(`sql:${value}`));

    control.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, cancelable: true, pointerId: 1, clientX: 0,
    }));
    control.dispatchEvent(new PointerEvent('pointermove', {
      bubbles: true, pointerId: 1, clientX: 100,
    }));
    control.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }));
    flushSync();

    expect(order).toEqual(['rf:0', 'rf:1', 'sql:1']);
    expect(control.getAttribute('aria-valuetext')).toContain('RF 100%, squelch 100%');
  });

  it('keeps an optimistic RF-only HBar through pending, exact confirmation, and later truth', () => {
    const target = mountPanel({ showRfGain: true, showSquelch: false });
    const control = slider(target) as HTMLElement & {
      setPointerCapture(id: number): void;
      hasPointerCapture(id: number): boolean;
      releasePointerCapture(id: number): void;
    };
    control.setPointerCapture = vi.fn();
    control.hasPointerCapture = vi.fn(() => true);
    control.releasePointerCapture = vi.fn();
    vi.spyOn(target.querySelector('.vc-hbar') as HTMLElement, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, width: 100 } as DOMRect);

    control.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, cancelable: true, pointerId: 2, clientX: 70,
    }));
    control.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 2 }));
    flushSync();
    expect(Math.round(mockHandlers.onRfGainChange.mock.calls[0][0] * 255)).toBe(179);
    expect(target.querySelector('.vc-value')?.textContent).toBe('70%');

    feedbackState.set('value', {
      ...availableFeedback(),
      rf: { command: 'set_rf_gain', feedback: feedback('rf-gain', 0.5, {
        target: 179 / 255, requestedTarget: 179 / 255,
        phase: 'awaiting-confirmation', busy: true,
        lifecycleId: 'rf-179', transitionId: 'rf-awaiting-179',
      }) },
    });
    flushSync();
    expect(target.querySelector('.vc-value')?.textContent).toBe('70%');
    expect(control.dataset.commandPhase).toBe('awaiting-confirmation');
    expect(control.getAttribute('aria-valuenow')).toBe('0.5');

    feedbackState.set('value', {
      ...availableFeedback(),
      rf: { command: 'set_rf_gain', feedback: feedback('rf-gain', 179 / 255, {
        requestedTarget: 179 / 255, phase: 'confirmed',
        outcome: { phase: 'confirmed' }, lifecycleId: 'rf-179',
        transitionId: 'rf-confirmed-179',
      }) },
    });
    flushSync();
    expect(control.getAttribute('aria-valuenow')).toBe(String(179 / 255));

    feedbackState.set('value', {
      ...availableFeedback(),
      rf: { command: 'set_rf_gain', feedback: feedback('rf-gain', 0.8) },
    });
    flushSync();
    expect(target.querySelector('.vc-value')?.textContent).toBe('80%');
  });

  it('switches branches, keeps qualified RF when SQL disappears, and recovers from null authority', () => {
    vi.useFakeTimers();
    const target = mountPanel({ showRfGain: true, showSquelch: true });
    expect(target.querySelector('.vc-dual')).not.toBeNull();

    const unavailableSql = feedback('squelch', null, {
      availability: 'unavailable', phase: 'unavailable',
    });
    feedbackState.set('value', {
      rf: { command: 'set_rf_gain', feedback: feedback('rf-gain', 0.5) },
      sql: { command: 'set_squelch', feedback: unavailableSql },
    });
    propsState.set('value', { ...baseProps, showRfGain: true, showSquelch: false });
    flushSync();
    const rfOnly = slider(target);
    expect(target.querySelector('.vc-hbar')).not.toBeNull();
    expect(rfOnly.getAttribute('aria-valuenow')).toBe('0.5');

    feedbackState.set('value', null);
    flushSync();
    expect(slider(target)).toBe(rfOnly);
    expect(rfOnly.getAttribute('aria-disabled')).toBe('true');
    expect(target.querySelector('.vc-value')?.textContent).toBe('—');

    feedbackState.set('value', {
      rf: { command: 'set_rf_gain', feedback: feedback('rf-gain', 0.8, {
        providerGeneration: 4, sessionEpoch: 8,
      }) },
      sql: { command: 'set_squelch', feedback: feedback('squelch', 0.1, {
        providerGeneration: 4, sessionEpoch: 8,
      }) },
    });
    flushSync();
    expect(slider(target)).toBe(rfOnly);
    expect(rfOnly.getAttribute('aria-disabled')).toBe('false');
    expect(rfOnly.getAttribute('aria-valuenow')).toBe('0.8');
    vi.advanceTimersByTime(1_000);
    expect(mockHandlers.onRfGainChange).not.toHaveBeenCalled();
    expect(mockHandlers.onSquelchChange).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
