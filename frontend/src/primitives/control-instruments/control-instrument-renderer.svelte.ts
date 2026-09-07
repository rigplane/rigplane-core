import type { Component } from 'svelte';
import {
  bindAbsoluteChoiceInstrument,
  bindActionInstrument,
  bindChoiceInstrument,
  bindToggleInstrument,
  type InstrumentAvailability,
  type InstrumentField,
  type InstrumentReading,
} from './control-instrument-behavior';

declare const finiteRendererContextBrand: unique symbol;
export type FiniteRendererContext = Readonly<{ [finiteRendererContextBrand]: true }>;

export const createFiniteRendererContext = (): FiniteRendererContext =>
  Object.freeze({}) as FiniteRendererContext;

export interface ControlLabel {
  readonly label: string;
  readonly accessibleLabel?: string;
  readonly title?: string;
}

export interface ControlOption<T> {
  readonly value: T;
  readonly label: string;
  readonly disabled?: boolean;
  readonly disabledReason?: string;
}

export interface RequestedTarget<T> {
  readonly kind: 'requested-target';
  readonly target: T;
}

interface RendererInput extends ControlLabel {
  readonly context: FiniteRendererContext | null;
}

interface FieldRendererInput<T, Feedback> extends RendererInput {
  readonly field: InstrumentField<T> | undefined;
  readonly blocked?: boolean;
  readonly feedback?: Feedback;
}

export interface ActionRendererInput<T, Feedback = never> extends FieldRendererInput<T, Feedback> {
  readonly availability?: never;
  readonly invoke: () => void;
}

export interface AvailabilityActionRendererInput<Feedback = never> extends RendererInput {
  readonly availability: InstrumentAvailability | undefined;
  readonly field?: never;
  readonly blocked?: boolean;
  readonly feedback?: Feedback;
  readonly invoke: () => void;
}

export interface ToggleRendererInput<Feedback = never> extends FieldRendererInput<boolean, Feedback> {
  readonly defaultValue?: boolean;
  readonly requested?: RequestedTarget<boolean>;
  readonly invoke: (next: boolean) => void;
}

export interface ChoiceRendererInput<T, Feedback = never> extends FieldRendererInput<T, Feedback> {
  readonly options: readonly ControlOption<T>[];
  readonly defaultValue?: T;
  readonly requested?: RequestedTarget<T>;
  readonly invoke: (value: T) => void;
}

export interface AbsoluteChoiceRendererInput<T, Feedback = never> extends RendererInput {
  readonly reading: InstrumentReading<T>;
  readonly available: boolean;
  readonly blocked?: boolean;
  readonly options: readonly ControlOption<T>[];
  readonly defaultValue?: T;
  readonly requested?: RequestedTarget<T>;
  readonly feedback?: Feedback;
  readonly invoke: (value: T) => void;
}

export interface ActionRendererView<Feedback = unknown> extends ControlLabel {
  readonly available: boolean;
  readonly feedback?: Feedback;
}

export interface ToggleRendererView<Feedback = unknown> extends ControlLabel {
  readonly available: boolean;
  readonly confirmed: boolean | undefined;
  readonly defaultValue?: boolean;
  readonly requested?: RequestedTarget<boolean>;
  readonly feedback?: Feedback;
}

export interface ChoiceRendererView<T, Feedback = unknown> extends ControlLabel {
  readonly available: boolean;
  readonly reading: InstrumentReading<T>;
  readonly selected: T | undefined;
  readonly options: readonly ControlOption<T>[];
  readonly defaultValue?: T;
  readonly requested?: RequestedTarget<T>;
  readonly feedback?: Feedback;
}

export interface FiniteRendererLease<View> {
  readonly active: boolean;
  readonly view: View | undefined;
  dispose(): void;
}

export interface ActionRendererLease<Feedback = unknown>
  extends FiniteRendererLease<ActionRendererView<Feedback>> {
  invoke(): void;
}

export interface ToggleRendererLease<Feedback = unknown>
  extends FiniteRendererLease<ToggleRendererView<Feedback>> {
  invoke(): void;
}

export interface ChoiceRendererLease<T, Feedback = unknown>
  extends FiniteRendererLease<ChoiceRendererView<T, Feedback>> {
  invoke(value: T): void;
}

export interface FiniteRendererSeat<Lease> {
  attachRenderer(): Lease;
  destroy(): void;
}

export type ActionRendererSeat<Feedback = unknown> = FiniteRendererSeat<ActionRendererLease<Feedback>>;
export type ToggleRendererSeat<Feedback = unknown> = FiniteRendererSeat<ToggleRendererLease<Feedback>>;
export type ChoiceRendererSeat<T, Feedback = unknown> = FiniteRendererSeat<ChoiceRendererLease<T, Feedback>>;

export interface ActionRendererProps { readonly lease: ActionRendererLease }
export interface ToggleRendererProps { readonly lease: ToggleRendererLease }
export interface ChoiceRendererProps<T> { readonly lease: ChoiceRendererLease<T> }

export interface FiniteControlAppearance<T = unknown> {
  readonly action: Component<ActionRendererProps>;
  readonly toggle: Component<ToggleRendererProps>;
  readonly choice: Component<ChoiceRendererProps<T>>;
}

interface LeaseGuard<Input extends RendererInput> {
  current(): Input | undefined;
  dispose(): void;
}

function createSeat<Input extends RendererInput, Lease>(
  readCurrent: () => Input,
  createLease: (guard: LeaseGuard<Input>) => Lease,
): FiniteRendererSeat<Lease> {
  let destroyed = false;
  const revokers = new Set<() => void>();
  return {
    attachRenderer() {
      const captured = readCurrent().context;
      let revoked = captured === null;
      const revoke = () => {
        if (revoked) return;
        revoked = true;
        revokers.delete(revoke);
      };
      if (!revoked) revokers.add(revoke);
      return createLease({
        current() {
          if (destroyed || revoked) return undefined;
          const current = readCurrent();
          if (current.context === null || !Object.is(current.context, captured)) {
            revoke();
            return undefined;
          }
          return current;
        },
        dispose: revoke,
      });
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      for (const revoke of [...revokers]) revoke();
    },
  };
}

const labelView = (input: ControlLabel): ControlLabel => ({
  label: input.label,
  ...(input.accessibleLabel === undefined ? {} : { accessibleLabel: input.accessibleLabel }),
  ...(input.title === undefined ? {} : { title: input.title }),
});

const feedbackView = <Feedback>(feedback: Feedback | undefined) =>
  feedback === undefined ? {} : { feedback };

export function createActionRendererSeat<T, Feedback = never>(
  readCurrent: () => ActionRendererInput<T, Feedback> | AvailabilityActionRendererInput<Feedback>,
): ActionRendererSeat<Feedback> {
  const behavior = bindActionInstrument<T, Feedback>(() => readCurrent());
  return createSeat(readCurrent, (guard) => ({
    get active() { return guard.current() !== undefined; },
    get view() {
      const input = guard.current();
      return input === undefined ? undefined : {
        ...labelView(input), available: behavior.available, ...feedbackView(behavior.feedback),
      };
    },
    invoke() {
      if (guard.current() !== undefined) behavior.invoke();
    },
    dispose: guard.dispose,
  }));
}

export function createToggleRendererSeat<Feedback = never>(
  readCurrent: () => ToggleRendererInput<Feedback>,
): ToggleRendererSeat<Feedback> {
  const behavior = bindToggleInstrument<Feedback>(() => readCurrent());
  return createSeat(readCurrent, (guard) => ({
    get active() { return guard.current() !== undefined; },
    get view() {
      const input = guard.current();
      return input === undefined ? undefined : {
        ...labelView(input), available: behavior.available, confirmed: behavior.confirmed,
        ...(input.defaultValue === undefined ? {} : { defaultValue: input.defaultValue }),
        ...(input.requested === undefined ? {} : { requested: input.requested }),
        ...feedbackView(behavior.feedback),
      };
    },
    invoke() {
      if (guard.current() !== undefined) behavior.invoke();
    },
    dispose: guard.dispose,
  }));
}

function createChoiceLease<T, Feedback, Input extends RendererInput & {
  readonly options: readonly ControlOption<T>[];
  readonly defaultValue?: T;
  readonly requested?: RequestedTarget<T>;
  readonly feedback?: Feedback;
}>(
  guard: LeaseGuard<Input>,
  behavior: ReturnType<typeof bindChoiceInstrument<T, Feedback>>,
  readingOf: (input: Input) => InstrumentReading<T>,
  selectionRequiresAvailability = false,
): ChoiceRendererLease<T, Feedback> {
  return {
    get active() { return guard.current() !== undefined; },
    get view() {
      const input = guard.current();
      return input === undefined ? undefined : {
        ...labelView(input), available: behavior.available, reading: readingOf(input),
        selected: selectionRequiresAvailability && !behavior.available
          ? undefined : behavior.selected,
        options: input.options,
        ...(input.defaultValue === undefined ? {} : { defaultValue: input.defaultValue }),
        ...(input.requested === undefined ? {} : { requested: input.requested }),
        ...feedbackView(input.feedback),
      };
    },
    invoke(value) {
      const input = guard.current();
      const option = input?.options.find(candidate => Object.is(candidate.value, value));
      if (option !== undefined && option.disabled !== true) behavior.invoke(value);
    },
    dispose: guard.dispose,
  };
}

export function createChoiceRendererSeat<T, Feedback = never>(
  readCurrent: () => ChoiceRendererInput<T, Feedback>,
  options: { readonly selectionRequiresAvailability?: boolean } = {},
): ChoiceRendererSeat<T, Feedback> {
  const behavior = bindChoiceInstrument<T, Feedback>(() => {
    const input = readCurrent();
    return {
      field: input.field, blocked: input.blocked,
      choices: input.options.map(option => option.value),
      feedback: input.feedback, invoke: input.invoke,
    };
  });
  return createSeat(readCurrent, guard => createChoiceLease(
    guard, behavior, input => input.field?.reading ?? { status: 'unknown' },
    options.selectionRequiresAvailability,
  ));
}

export function createAbsoluteChoiceRendererSeat<T, Feedback = never>(
  readCurrent: () => AbsoluteChoiceRendererInput<T, Feedback>,
): ChoiceRendererSeat<T, Feedback> {
  const behavior = bindAbsoluteChoiceInstrument<T, Feedback>(() => {
    const input = readCurrent();
    return {
      choices: input.options.map(option => option.value),
      selected: input.reading.status === 'known' ? input.reading.value : undefined,
      available: input.available, blocked: input.blocked,
      feedback: input.feedback, invoke: input.invoke,
    };
  });
  return createSeat(readCurrent, guard => createChoiceLease(guard, behavior, input => input.reading));
}
