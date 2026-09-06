/** Stateless current-input behavior bindings for control renderers. */

export interface InstrumentAvailability {
  readonly structural: boolean;
  readonly operational: boolean;
}

export type InstrumentReading<T> =
  | Readonly<{ status: 'known'; value: T }>
  | Readonly<{ status: 'unknown' }>;

/** Structural subset shared by semantic field facts without owning them. */
export interface InstrumentField<T> {
  readonly availability: InstrumentAvailability;
  readonly reading: InstrumentReading<T>;
}

interface BindingInput<T, Feedback> {
  readonly field: InstrumentField<T> | undefined;
  readonly blocked?: boolean;
  readonly feedback?: Feedback;
}

interface ActionInput<T, Feedback> extends BindingInput<T, Feedback> {
  readonly invoke: () => void;
}

interface ToggleInput<Feedback> extends BindingInput<boolean, Feedback> {
  readonly invoke: (next: boolean) => void;
}

interface ChoiceInput<T, Feedback> extends BindingInput<T, Feedback> {
  readonly choices: readonly T[];
  readonly invoke: (value: T) => void;
}

const canInvoke = <T>(input: BindingInput<T, unknown>): input is BindingInput<T, unknown> & {
  readonly field: InstrumentField<T> & { readonly reading: Readonly<{ status: 'known'; value: T }> };
} => input.blocked !== true
  && input.field !== undefined
  && input.field.availability.structural
  && input.field.availability.operational
  && input.field.reading.status === 'known';

export interface ActionInstrumentBehavior<Feedback> {
  readonly available: boolean;
  readonly feedback: Feedback | undefined;
  invoke(): void;
}

export function bindActionInstrument<T, Feedback = never>(
  current: () => ActionInput<T, Feedback>,
): ActionInstrumentBehavior<Feedback> {
  return {
    get available() { return canInvoke(current()); },
    get feedback() { return current().feedback; },
    invoke() {
      const input = current();
      if (canInvoke(input)) input.invoke();
    },
  };
}

export interface ToggleInstrumentBehavior<Feedback> extends ActionInstrumentBehavior<Feedback> {
  readonly confirmed: boolean | undefined;
}

export function bindToggleInstrument<Feedback = never>(
  current: () => ToggleInput<Feedback>,
): ToggleInstrumentBehavior<Feedback> {
  return {
    get available() { return canInvoke(current()); },
    get confirmed() {
      const field = current().field;
      return field?.reading.status === 'known' ? field.reading.value : undefined;
    },
    get feedback() { return current().feedback; },
    invoke() {
      const input = current();
      if (canInvoke(input)) input.invoke(!input.field.reading.value);
    },
  };
}

export interface ChoiceInstrumentBehavior<T, Feedback> {
  readonly available: boolean;
  readonly feedback: Feedback | undefined;
  readonly selected: T | undefined;
  isSelected(value: T): boolean;
  invoke(value: T): void;
}

export function bindChoiceInstrument<T, Feedback = never>(
  current: () => ChoiceInput<T, Feedback>,
): ChoiceInstrumentBehavior<T, Feedback> {
  const offered = (choices: readonly T[], value: T): boolean => choices.some(choice => Object.is(choice, value));
  return {
    get available() { return canInvoke(current()); },
    get selected() {
      const input = current();
      return input.field?.reading.status === 'known' && offered(input.choices, input.field.reading.value)
        ? input.field.reading.value : undefined;
    },
    get feedback() { return current().feedback; },
    isSelected(value) { return this.selected !== undefined && Object.is(this.selected, value); },
    invoke(value) {
      const input = current();
      if (canInvoke(input) && offered(input.choices, value)) input.invoke(value);
    },
  };
}
