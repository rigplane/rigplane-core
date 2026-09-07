import { describe, expect, it, vi } from 'vitest';
import {
  createAbsoluteChoiceRendererSeat,
  createActionRendererSeat,
  createChoiceRendererSeat,
  createFiniteRendererContext,
  createToggleRendererSeat,
  type AvailabilityActionRendererInput,
  type ControlOption,
  type FiniteRendererContext,
} from '../control-instrument-renderer.svelte';
import type { InstrumentAvailability, InstrumentField } from '../control-instrument-behavior';

const field = <T>(value: T): InstrumentField<T> => ({
  availability: { structural: true, operational: true },
  reading: { status: 'known', value },
});
const unknown = <T>(): InstrumentField<T> => ({
  availability: { structural: true, operational: true }, reading: { status: 'unknown' },
});

describe('finite renderer leases', () => {
  it('rejects mixed action evidence at the renderer boundary', () => {
    const invoke = vi.fn();
    const mixed: AvailabilityActionRendererInput = {
      context: createFiniteRendererContext(), label: 'Run',
      // @ts-expect-error Renderer action evidence must use exactly one input form.
      availability: { structural: true, operational: true }, field: field(1), invoke,
    };

    expect(mixed).toBeDefined();
  });

  it('permanently refuses a retained A1 callback after A-B-A without cleanup', () => {
    let context = createFiniteRendererContext();
    let current = field(1);
    const invoke = vi.fn();
    const seat = createActionRendererSeat(() => ({ context, field: current, label: '+', invoke }));
    const stale = seat.attachRenderer();

    context = createFiniteRendererContext();
    context = createFiniteRendererContext();
    stale.invoke();
    expect(stale.active).toBe(false);
    expect(invoke).not.toHaveBeenCalled();

    const replacement = seat.attachRenderer();
    replacement.invoke();
    expect(replacement.active).toBe(true);
    expect(invoke).toHaveBeenCalledOnce();
  });

  it('renders availability-only actions without confirmed or status evidence', () => {
    const context = createFiniteRendererContext();
    const feedback = { phase: 'submitted' } as const;
    const seat = createActionRendererSeat(() => ({
      context, label: 'Equalize', title: 'Match VFOs',
      availability: { structural: true, operational: true }, feedback, invoke: vi.fn(),
    }));
    const view = seat.attachRenderer().view;

    expect(view).toEqual({
      label: 'Equalize', title: 'Match VFOs', available: true, feedback,
    });
    expect('confirmed' in view!).toBe(false);
    expect('reading' in view!).toBe(false);
    expect('requested' in view!).toBe(false);
  });

  it('keeps availability-only leases independent and invokes the current callback once', () => {
    const context = createFiniteRendererContext();
    let availability: InstrumentAvailability | undefined = {
      structural: true, operational: true,
    };
    const firstInvoke = vi.fn();
    const replacementInvoke = vi.fn();
    let invoke = firstInvoke;
    const seat = createActionRendererSeat(() => ({
      context, label: 'Swap', availability, invoke,
    }));
    const first = seat.attachRenderer();
    const second = seat.attachRenderer();

    first.dispose();
    first.invoke();
    invoke = replacementInvoke;
    second.invoke();
    availability = undefined;
    second.invoke();
    availability = { structural: true, operational: true };
    seat.destroy();
    second.invoke();

    expect(first.active).toBe(false);
    expect(second.active).toBe(false);
    expect(firstInvoke).not.toHaveBeenCalled();
    expect(replacementInvoke).toHaveBeenCalledOnce();
  });

  it('revokes availability-only A-B-A leases before reading action evidence', () => {
    let context: FiniteRendererContext | null = null;
    const readAvailability = vi.fn(() => ({ structural: true, operational: true }));
    const invoke = vi.fn();
    const seat = createActionRendererSeat(() => ({
      context, label: 'Speak', get availability() { return readAvailability(); }, invoke,
    }));
    const absent = seat.attachRenderer();
    expect(absent.view).toBeUndefined();
    absent.invoke();

    context = createFiniteRendererContext();
    const stale = seat.attachRenderer();

    context = createFiniteRendererContext();
    context = createFiniteRendererContext();
    expect(stale.view).toBeUndefined();
    stale.invoke();

    expect(stale.active).toBe(false);
    expect(readAvailability).not.toHaveBeenCalled();
    expect(invoke).not.toHaveBeenCalled();
  });

  it('keeps simultaneous leases independent and destroys all only with the seat', () => {
    const context = createFiniteRendererContext();
    const invoke = vi.fn();
    const seat = createActionRendererSeat(() => ({ context, field: field(1), label: 'Run', invoke }));
    const first = seat.attachRenderer();
    const second = seat.attachRenderer();
    expect(first.active).toBe(true);
    expect(second.active).toBe(true);
    first.dispose();
    second.invoke();
    expect(first.active).toBe(false);
    expect(second.active).toBe(true);
    expect(invoke).toHaveBeenCalledOnce();
    seat.destroy();
    expect(second.active).toBe(false);
    second.invoke();
    expect(invoke).toHaveBeenCalledOnce();
  });

  it('re-reads current toggle truth and carries no unsourced evidence', () => {
    const context = createFiniteRendererContext();
    let current = field(false);
    const invoke = vi.fn();
    const seat = createToggleRendererSeat(() => ({ context, field: current, label: 'Hold', invoke }));
    const lease = seat.attachRenderer();
    expect(lease.view).toEqual({ label: 'Hold', available: true, confirmed: false });
    current = field(true);
    expect(lease.view?.confirmed).toBe(true);
    expect('defaultValue' in lease.view!).toBe(false);
    expect('requested' in lease.view!).toBe(false);
    expect('feedback' in lease.view!).toBe(false);
    lease.invoke();
    expect(invoke).toHaveBeenCalledExactlyOnceWith(false);
  });

  it('carries a supplied default only as presentation metadata', () => {
    const context = createFiniteRendererContext();
    const seat = createToggleRendererSeat(() => ({
      context, field: field(true), label: 'Dual', defaultValue: false, invoke: vi.fn(),
    }));
    expect(seat.attachRenderer().view?.defaultValue).toBe(false);
  });

  it('keeps exact reading, admitted selection and disabled-option admission distinct', () => {
    const context = createFiniteRendererContext();
    let current = field('DATA2');
    let options: readonly ControlOption<string>[] = [
      { value: 'OFF', label: 'Off' }, { value: 'DATA1', label: 'Data 1', disabled: true },
    ];
    const invoke = vi.fn();
    const seat = createChoiceRendererSeat(() => ({
      context, field: current, label: 'Data', options, invoke,
    }));
    const lease = seat.attachRenderer();
    expect(lease.view?.reading).toEqual({ status: 'known', value: 'DATA2' });
    expect(lease.view?.selected).toBeUndefined();
    expect('defaultValue' in lease.view!).toBe(false);
    lease.invoke('DATA1');
    lease.invoke('outside');
    expect(invoke).not.toHaveBeenCalled();
    options = [{ value: 'OFF', label: 'Off' }, { value: 'DATA1', label: 'Data 1' }];
    lease.invoke('DATA1');
    expect(invoke).toHaveBeenCalledExactlyOnceWith('DATA1');
    current = unknown();
    expect(lease.view?.reading).toEqual({ status: 'unknown' });
  });

  it('keeps retained reading while availability-gated selection is an explicit opt-in', () => {
    const context = createFiniteRendererContext();
    let current: InstrumentField<string> = {
      availability: { structural: true, operational: false },
      reading: { status: 'known', value: 'USB' },
    };
    const invoke = vi.fn();
    const readCurrent = () => ({
      context, field: current, label: 'Mode',
      options: [{ value: 'USB', label: 'USB' }], invoke,
    });
    const defaultLease = createChoiceRendererSeat(readCurrent).attachRenderer();
    const gatedLease = createChoiceRendererSeat(readCurrent, {
      selectionRequiresAvailability: true,
    }).attachRenderer();

    expect(defaultLease.view?.reading).toEqual({ status: 'known', value: 'USB' });
    expect(defaultLease.view?.selected).toBe('USB');
    expect(gatedLease.view?.reading).toEqual({ status: 'known', value: 'USB' });
    expect(gatedLease.view?.selected).toBeUndefined();
    defaultLease.invoke('USB'); gatedLease.invoke('USB');
    expect(invoke).not.toHaveBeenCalled();

    current = field('USB');
    expect(gatedLease.view?.selected).toBe('USB');
    gatedLease.invoke('USB');
    expect(invoke).toHaveBeenCalledExactlyOnceWith('USB');
  });

  it('passes current option reasons through without changing option admission', () => {
    const context = createFiniteRendererContext();
    let options: readonly ControlOption<string>[] = [
      { value: 'MAIN', label: 'MAIN', disabled: true, disabledReason: 'MAIN unavailable' },
      { value: 'SUB', label: 'SUB', disabledReason: 'SUB description only' },
    ];
    const invoke = vi.fn();
    const seat = createAbsoluteChoiceRendererSeat(() => ({
      context, reading: { status: 'unknown' }, available: true,
      label: 'Active receiver', options, invoke,
    }));
    const lease = seat.attachRenderer();

    expect(lease.view?.options).toEqual(options);
    lease.invoke('MAIN');
    lease.invoke('SUB');
    expect(invoke).toHaveBeenCalledExactlyOnceWith('SUB');

    options = [
      { value: 'MAIN', label: 'MAIN', disabled: true, disabledReason: 'Replacement reason' },
      { value: 'SUB', label: 'SUB' },
    ];
    expect(lease.view?.options[0]?.disabledReason).toBe('Replacement reason');
    expect(lease.view?.options[1]?.disabledReason).toBeUndefined();
    lease.invoke('MAIN');
    lease.invoke('SUB');
    expect(invoke).toHaveBeenCalledTimes(2);
  });

  it('keeps an unknown absolute choice actionable through the existing binding', () => {
    const context = createFiniteRendererContext();
    const invoke = vi.fn();
    const seat = createAbsoluteChoiceRendererSeat(() => ({
      context, reading: { status: 'unknown' }, available: true, label: 'Band',
      options: [{ value: 20, label: '20 m' }], invoke,
    }));
    const lease = seat.attachRenderer();
    expect(lease.view?.available).toBe(true);
    expect(lease.view?.selected).toBeUndefined();
    lease.invoke(20);
    expect(invoke).toHaveBeenCalledExactlyOnceWith(20);
  });

  it('requires a real non-null context and latches mismatch before behavior reads', () => {
    let context: FiniteRendererContext | null = null;
    const readField = vi.fn(() => field(1));
    const seat = createActionRendererSeat(() => ({
      context, get field() { return readField(); }, label: 'Run', invoke: vi.fn(),
    }));
    const lease = seat.attachRenderer();
    expect(lease.active).toBe(false);
    context = createFiniteRendererContext();
    expect(lease.view).toBeUndefined();
    expect(readField).not.toHaveBeenCalled();
  });

  it('does not project a field choice before null or stale context admission', () => {
    let context: FiniteRendererContext | null = null;
    const readField = vi.fn(() => field('A'));
    const seat = createChoiceRendererSeat(() => ({
      context, get field() { return readField(); }, label: 'Mode',
      options: [{ value: 'A', label: 'A' }], invoke: vi.fn(),
    }));
    const nullLease = seat.attachRenderer();
    expect(nullLease.view).toBeUndefined();
    nullLease.invoke('A');
    expect(readField).not.toHaveBeenCalled();

    context = createFiniteRendererContext();
    const stale = seat.attachRenderer();
    readField.mockClear();
    context = createFiniteRendererContext();
    context = createFiniteRendererContext();
    expect(stale.view).toBeUndefined();
    stale.invoke('A');
    expect(readField).not.toHaveBeenCalled();
  });

  it('does not read an absolute choice before context admission', () => {
    const readReading = vi.fn(() => ({ status: 'unknown' as const }));
    const seat = createAbsoluteChoiceRendererSeat(() => ({
      context: null, get reading() { return readReading(); }, available: true, label: 'Band',
      options: [{ value: 20, label: '20 m' }], invoke: vi.fn(),
    }));
    const lease = seat.attachRenderer();
    expect(lease.view).toBeUndefined();
    lease.invoke(20);
    expect(readReading).not.toHaveBeenCalled();
  });
});
