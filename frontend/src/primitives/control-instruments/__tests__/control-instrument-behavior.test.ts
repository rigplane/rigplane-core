import { describe, expect, it, vi } from 'vitest';
import {
  bindActionInstrument, bindChoiceInstrument, bindToggleInstrument,
  type InstrumentField,
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
  it('re-checks an action field and blocker at invocation time', () => {
    let field = usable(1);
    let blocked = false;
    const invoke = vi.fn();
    const behavior = bindActionInstrument(() => ({ field, blocked, invoke }));

    field = unavailable(1);
    behavior.invoke();
    blocked = true;
    field = usable(1);
    behavior.invoke();
    blocked = false;
    behavior.invoke();

    expect(invoke).toHaveBeenCalledOnce();
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
});
