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
  readonly availability?: never;
  readonly invoke: () => void;
}

export interface AvailabilityActionInput<Feedback = never> {
  readonly availability: InstrumentAvailability | undefined;
  readonly field?: never;
  readonly blocked?: boolean;
  readonly feedback?: Feedback;
  readonly invoke: () => void;
}

interface ToggleInput<Feedback> extends BindingInput<boolean, Feedback> {
  readonly invoke: (next: boolean) => void;
}

interface ChoiceInput<T, Feedback> extends BindingInput<T, Feedback> {
  readonly choices: readonly T[];
  readonly invoke: (value: T) => void;
}

export interface AbsoluteChoiceInput<T, Feedback> {
  readonly choices: readonly T[];
  readonly selected: T | undefined;
  readonly available: boolean;
  readonly blocked?: boolean;
  readonly invoke: (value: T) => void;
  readonly feedback?: Feedback;
}

const canInvoke = <T>(input: BindingInput<T, unknown>): input is BindingInput<T, unknown> & {
  readonly field: InstrumentField<T> & { readonly reading: Readonly<{ status: 'known'; value: T }> };
} => input.blocked !== true
  && input.field !== undefined
  && input.field.availability.structural
  && input.field.availability.operational
  && input.field.reading.status === 'known';

const canInvokeAction = <T>(
  input: ActionInput<T, unknown> | AvailabilityActionInput<unknown>,
): boolean => {
  if ('availability' in input) {
    return input.blocked !== true
      && input.availability !== undefined
      && input.availability.structural
      && input.availability.operational;
  }
  return canInvoke(input);
};

export interface ActionInstrumentBehavior<Feedback> {
  readonly available: boolean;
  readonly feedback: Feedback | undefined;
  invoke(): void;
}

export function bindActionInstrument<T, Feedback = never>(
  current: () => ActionInput<T, Feedback> | AvailabilityActionInput<Feedback>,
): ActionInstrumentBehavior<Feedback> {
  return {
    get available() { return canInvokeAction(current()); },
    get feedback() { return current().feedback; },
    invoke() {
      const input = current();
      if (canInvokeAction(input)) input.invoke();
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

interface ChoiceBehaviorInput<T, Feedback> {
  readonly choices: readonly T[];
  readonly selected: T | undefined;
  readonly available: boolean;
  readonly blocked?: boolean;
  readonly invoke: (value: T) => void;
  readonly feedback?: Feedback;
}

const offered = <T>(choices: readonly T[], value: T): boolean =>
  choices.some(choice => Object.is(choice, value));

function bindChoiceBehavior<T, Feedback>(
  current: () => ChoiceBehaviorInput<T, Feedback>,
): ChoiceInstrumentBehavior<T, Feedback> {
  return {
    get available() {
      const input = current();
      return input.available && input.blocked !== true;
    },
    get selected() {
      const input = current();
      return input.selected !== undefined && offered(input.choices, input.selected)
        ? input.selected : undefined;
    },
    get feedback() { return current().feedback; },
    isSelected(value) { return this.selected !== undefined && Object.is(this.selected, value); },
    invoke(value) {
      const input = current();
      if (input.available && input.blocked !== true && offered(input.choices, value)) {
        input.invoke(value);
      }
    },
  };
}

export function bindChoiceInstrument<T, Feedback = never>(
  current: () => ChoiceInput<T, Feedback>,
): ChoiceInstrumentBehavior<T, Feedback> {
  return bindChoiceBehavior(() => {
    const input = current();
    return {
      choices: input.choices,
      selected: input.field?.reading.status === 'known' ? input.field.reading.value : undefined,
      available: canInvoke(input),
      invoke: input.invoke,
      feedback: input.feedback,
    };
  });
}

export function bindAbsoluteChoiceInstrument<T, Feedback = never>(
  current: () => AbsoluteChoiceInput<T, Feedback>,
): ChoiceInstrumentBehavior<T, Feedback> {
  return bindChoiceBehavior(current);
}
