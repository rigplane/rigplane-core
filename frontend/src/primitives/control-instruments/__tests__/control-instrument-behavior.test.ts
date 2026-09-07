import { describe, expect, it, vi } from 'vitest';
import {
  bindAbsoluteChoiceInstrument, bindActionInstrument, bindChoiceInstrument, bindToggleInstrument,
  type AvailabilityActionInput, type InstrumentAvailability, type InstrumentField,
} from '../control-instrument-behavior';

const usable = <T>(value: T): InstrumentField<T> => ({
  availability: { structural: true, operational: true }, reading: { status: 'known', value },
});
const unavailable = <T>(value: T): InstrumentField<T> => ({
  availability: { structural: true, operational: false }, reading: { status: 'known', value },
});
const unknown = <T>(): InstrumentField<T> => ({
  availability: { structural: true, operational: true }, reading: { status: 'unknown' },
});

describe('control-instrument behavior bindings', () => {
  it('keeps field and availability-only action inputs mutually exclusive', () => {
    const invoke = vi.fn();
    const mixed: AvailabilityActionInput = {
      // @ts-expect-error Action evidence must come from exactly one input form.
      availability: { structural: true, operational: true }, field: usable(1), invoke,
    };

    expect(mixed).toBeDefined();
  });

  it('re-checks an action field and blocker at invocation time', () => {
    let field = usable(1);
    let blocked = false;
    const invoke = vi.fn();
    const behavior = bindActionInstrument(() => ({ field, blocked, invoke }));

    field = unavailable(1);
    behavior.invoke();
    field = unknown<number>();
    behavior.invoke();
    blocked = true;
    field = usable(1);
    behavior.invoke();
    blocked = false;
    behavior.invoke();

    expect(invoke).toHaveBeenCalledOnce();
  });

  it('re-checks availability-only action admission and the current callback', () => {
    let availability: InstrumentAvailability | undefined;
    let blocked = false;
    const first = vi.fn();
    const replacement = vi.fn();
    let invoke = first;
    const behavior = bindActionInstrument(() => ({ availability, blocked, invoke }));

    expect(behavior.available).toBe(false);
    behavior.invoke();
    availability = { structural: false, operational: true };
    behavior.invoke();
    availability = { structural: true, operational: false };
    behavior.invoke();
    availability = { structural: true, operational: true };
    blocked = true;
    behavior.invoke();
    blocked = false;
    invoke = replacement;

    expect(behavior.available).toBe(true);
    behavior.invoke();
    expect(first).not.toHaveBeenCalled();
    expect(replacement).toHaveBeenCalledOnce();
  });

  it('forwards optional availability-only action feedback without inventing it', () => {
    const feedback = { phase: 'submitted' } as const;
    const present = bindActionInstrument(() => ({
      availability: { structural: true, operational: true }, feedback, invoke: vi.fn(),
    }));
    const absent = bindActionInstrument(() => ({
      availability: { structural: true, operational: true }, invoke: vi.fn(),
    }));

    expect(present.feedback).toBe(feedback);
    expect(absent.feedback).toBeUndefined();
  });

  it('derives a toggle target from the current confirmed boolean', () => {
    let field = usable(false);
    const invoke = vi.fn();
    const behavior = bindToggleInstrument(() => ({ field, invoke }));

    expect(behavior.confirmed).toBe(false);
    field = usable(true);
    behavior.invoke();
    expect(invoke).toHaveBeenCalledExactlyOnceWith(false);
  });

  it('omits unknown toggle state and does not invoke it', () => {
    const invoke = vi.fn();
    const behavior = bindToggleInstrument(() => ({ field: unknown<boolean>(), invoke }));

    expect(behavior.confirmed).toBeUndefined();
    behavior.invoke();
    expect(invoke).not.toHaveBeenCalled();
  });

  it('keeps a supplied feedback envelope unchanged and does not invent one', () => {
    const feedback = { phase: 'submitted', target: true } as const;
    const present = bindToggleInstrument(() => ({ field: usable(true), invoke: vi.fn(), feedback }));
    const absent = bindToggleInstrument(() => ({ field: usable(true), invoke: vi.fn() }));

    expect(present.feedback).toBe(feedback);
    expect(absent.feedback).toBeUndefined();
  });

  it('only selects and invokes values in the current offered finite choice set', () => {
    let field = usable(9);
    let choices = [1, 2] as readonly number[];
    const invoke = vi.fn();
    const behavior = bindChoiceInstrument(() => ({ field, choices, invoke }));

    expect(behavior.selected).toBeUndefined();
    expect(behavior.isSelected(1)).toBe(false);
    behavior.invoke(9);
    expect(invoke).not.toHaveBeenCalled();

    field = usable(2);
    choices = [2, 3];
    expect(behavior.selected).toBe(2);
    behavior.invoke(3);
    expect(invoke).toHaveBeenCalledExactlyOnceWith(3);
  });

  it('keeps known-field selection independent from availability and permits offered targets', () => {
    let field = unavailable(9);
    const invoke = vi.fn();
    const behavior = bindChoiceInstrument(() => ({ field, choices: [1, 2], invoke }));

    expect(behavior.selected).toBeUndefined();
    expect(behavior.available).toBe(false);
    field = usable(9);
    behavior.invoke(1);
    expect(invoke).toHaveBeenCalledExactlyOnceWith(1);
  });

  it('separates absolute invocation eligibility from an unknown canonical selection', () => {
    const invoke = vi.fn();
    const behavior = bindAbsoluteChoiceInstrument(() => ({
      choices: ['local', 'live'] as const, selected: undefined, available: true, invoke,
    }));

    expect(behavior.available).toBe(true);
    expect(behavior.selected).toBeUndefined();
    expect(behavior.isSelected('live')).toBe(false);
    behavior.invoke('live');
    expect(invoke).toHaveBeenCalledExactlyOnceWith('live');
    expect(behavior.selected).toBeUndefined();
  });

  it('rechecks absolute blockers and offered membership at invocation time', () => {
    let choices = [1, 2] as readonly number[];
    let blocked = false;
    const invoke = vi.fn();
    const behavior = bindAbsoluteChoiceInstrument(() => ({
      choices, selected: 2, available: true, blocked, invoke,
    }));

    choices = [2, 3];
    behavior.invoke(1);
    blocked = true;
    expect(behavior.available).toBe(false);
    expect(behavior.selected).toBe(2);
    behavior.invoke(3);
    blocked = false;
    behavior.invoke(3);
    expect(invoke).toHaveBeenCalledExactlyOnceWith(3);
  });

  it('uses exact membership and never substitutes an invoked target for selection', () => {
    const invoke = vi.fn();
    const behavior = bindAbsoluteChoiceInstrument<number | boolean>(() => ({
      choices: [1, true], selected: 1, available: true, invoke,
    }));

    expect(behavior.isSelected(1)).toBe(true);
    expect(behavior.isSelected(true)).toBe(false);
    behavior.invoke(true);
    expect(invoke).toHaveBeenCalledExactlyOnceWith(true);
    expect(behavior.selected).toBe(1);
  });

  it('forwards arbitrary absolute-choice feedback by identity without manufacturing it', () => {
    const feedback = { phase: 'submitted', target: 'live' } as const;
    const present = bindAbsoluteChoiceInstrument(() => ({
      choices: ['live'], selected: undefined, available: true, feedback, invoke: vi.fn(),
    }));
    const absent = bindAbsoluteChoiceInstrument(() => ({
      choices: ['live'], selected: undefined, available: true, invoke: vi.fn(),
    }));

    expect(present.feedback).toBe(feedback);
    expect(absent.feedback).toBeUndefined();
  });
});
